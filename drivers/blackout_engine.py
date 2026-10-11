"""drivers/blackout_engine.py
Instant blackout / mute (2026-10-06): one admin button (web remote,
Application section) that stops every sound and every light at once --
music, sweepers/VO, game music, the SFX amplifier, DMX fixtures + moving
head, the marquee, the outline strip, the red matrix panels and the arcade
button LEDs -- and a second press puts everything back exactly where it
was.

Audio is PAUSED, not stopped (pygame.mixer.pause(), mixer.music.pause(),
SIGSTOP on the SFX aplay child), so playback resumes mid-sound. Lights are
blanked at each output's send point (state.blackout_active), so their
engines keep computing and the look simply reappears on release.

While blacked out the show's clock is frozen: every frame, update() pushes
the wall-clock anchors the show runs on (Auto-DJ track timer, question /
intermission / applause deadlines, the medley's chunk clock and question,
transition timings) forward by the elapsed time, so nothing advances or
fires in the dark -- on release a song, question or countdown carries on
with the same time left as when the button was pressed.
"""
import math
import signal
import time

import pygame

from state import state

_last = None  # time of the previous update() while active
_engaged_at = 0.0

# state attributes that are timestamps the show counts toward / from.
# Shifted only while still relevant (> 0) so an unset 0.0 stays unset.
_STATE_ANCHORS = (
    "auto_dj_track_started_at",
    "round_deadline_at",
    "intermission_ends_at",
    "win_applause_ends_at",
    "mystery_defer_until",
    "announcement_banner_from",
    "announcement_banner_until",
    "marquee_transition_start",
    "marquee_reveal_at",
    "medley_results_until",
    "westminster_chime_ends_at",
    "show_outro_music_ends_at",
)


def toggle():
    if state.blackout_active:
        release()
    else:
        engage()
    return state.blackout_active


def engage():
    global _last, _engaged_at
    if state.blackout_active:
        return
    state.blackout_active = True
    _engaged_at = _last = time.time()
    _pause_audio(True)
    try:
        from drivers import simon_hardware
        simon_hardware.set_all_leds(False)
    except Exception as e:
        print(f"[BLACKOUT] Arcade LEDs off failed: {e}")
    print("[BLACKOUT] ENGAGED -- all sound and light stopped.")


def release():
    global _last
    if not state.blackout_active:
        return
    update(time.time())  # final clock shift up to this instant
    state.blackout_active = False
    _last = None
    _pause_audio(False)
    try:
        from inputs import gamepad
        gamepad._panel_led_state = None  # resync the arcade LEDs next frame
    except Exception:
        pass
    print(f"[BLACKOUT] Released after {time.time() - _engaged_at:.1f}s -- resuming.")


def update(now):
    """Per-frame (main.py): while active, freeze the show clock."""
    global _last
    if not state.blackout_active:
        return
    if _last is None:
        _last = now
    dt = max(0.0, now - _last)
    _last = now
    if dt > 0:
        _shift(dt)


def _shift(dt):
    for name in _STATE_ANCHORS:
        v = getattr(state, name, 0.0)
        if isinstance(v, (int, float)) and v > 0 and math.isfinite(v):
            setattr(state, name, v + dt)
    q = getattr(state, "medley_q", None)
    if isinstance(q, dict):
        for k in ("deadline", "started", "opens"):
            if k in q:
                q[k] += dt
    rv = getattr(state, "medley_reveal", None)
    if isinstance(rv, dict) and "until" in rv:
        rv["until"] += dt
    try:
        from drivers import medley_engine
        run = medley_engine._run
        if run:
            for k in ("began_at", "chunk_started_at"):
                if run.get(k):
                    run[k] += dt
    except Exception:
        pass


def _pause_audio(pause):
    try:
        if pygame.mixer.get_init():
            if pause:
                pygame.mixer.pause()
                pygame.mixer.music.pause()
            else:
                pygame.mixer.unpause()
                pygame.mixer.music.unpause()
    except Exception as e:
        print(f"[BLACKOUT] Mixer {'pause' if pause else 'resume'} failed: {e}")
    try:
        from drivers import sfx_engine
        proc = sfx_engine._current_proc
        if proc is not None and proc.poll() is None:
            proc.send_signal(signal.SIGSTOP if pause else signal.SIGCONT)
    except Exception as e:
        print(f"[BLACKOUT] SFX {'pause' if pause else 'resume'} failed: {e}")
