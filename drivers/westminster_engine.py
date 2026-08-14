# drivers/westminster_engine.py
"""Westminster "Bat Clock" top-of-hour show event (Feature Update): a phase
state machine in the same shape as drivers/price_game_engine.py -- flash ->
particle scatter -> digital time readout on the matrix (see
graphics/matrix_canvas.py::_render_westminster), a DMX kill/strobe/UV-ramp
sequence (see drivers/lighting_engine.py), and an audio chime with a fader
duck/restore (skipped if Auto Voice is OFF).

Fires automatically at the top of every hour, or on demand via
trigger_test() (desktop overlay button + POST /api/westminster/test on the
web remote) -- update() is polled every frame from
inputs/gamepad.py::process_events(), alongside the other per-frame engines."""
import time
import math
import random

import config
from state import state
from drivers import midi_driver
from audio.audio_engine import play_processed_sound, pick_random_chime

_last_particle_update_at = None


def _deal_particles():
    """Scatters config.WESTMINSTER_PARTICLE_COUNT "bats" outward from the
    matrix center at random angles/speeds -- a lightweight stand-in for a
    real particle engine, same spirit as the existing Space Invaders
    bullet/explosion state (drivers/space_invaders_engine.py)."""
    particles = []
    cx, cy = config.MATRIX_WIDTH / 2.0, config.MATRIX_HEIGHT / 2.0
    for _ in range(config.WESTMINSTER_PARTICLE_COUNT):
        angle = random.uniform(0, 2 * math.pi)
        speed = config.WESTMINSTER_PARTICLE_SPEED_PX_PER_SEC * random.uniform(0.6, 1.4)
        particles.append({
            "x": cx, "y": cy,
            "vx": math.cos(angle) * speed,
            "vy": math.sin(angle) * speed,
        })
    state.westminster_particles = particles


def _enter_phase(phase, now):
    state.westminster_phase = phase
    state.westminster_phase_started_at = now
    if phase == "particles":
        _deal_particles()


def _start_sequence(now):
    global _last_particle_update_at
    state.westminster_active = True
    state.westminster_started_at = now
    state.westminster_strobe_count = random.randint(
        config.WESTMINSTER_STROBE_MIN, config.WESTMINSTER_STROBE_MAX
    )
    state.westminster_particles = []
    _last_particle_update_at = now
    _enter_phase("flash", now)

    # Conditional rule: if Auto Voice is OFF, skip the chime + fader-duck
    # portion entirely (visual/DMX still run in full either way).
    if state.auto_announce_enabled:
        state.westminster_pre_mute_volume = state.music_volume
        midi_driver.tween_channel_faders_to(0, config.WESTMINSTER_AUDIO_DUCK_SECONDS)
        chime = pick_random_chime()
        play_processed_sound(chime)
        state.westminster_chime_ends_at = now + chime.get_length()
    else:
        state.westminster_chime_ends_at = 0.0

    print(f"[WESTMINSTER] Top-of-hour sequence triggered ({state.westminster_strobe_count} strobes, "
          f"audio {'ON' if state.auto_announce_enabled else 'SKIPPED (Auto Voice OFF)'}).")


def trigger_test():
    """Manual test trigger -- called from the desktop overlay panel
    (graphics/overlay_panel.py) and POST /api/westminster/test (web remote).
    Bypasses the automatic top-of-hour clock gate; a no-op if a sequence is
    already in progress (same guard shape as
    price_game_engine.start_price_game's `if state.price_game_active: return`)."""
    if state.westminster_active:
        print("[WESTMINSTER] Test trigger ignored -- a sequence is already in progress.")
        return
    _start_sequence(time.time())


def _advance_particles(now):
    global _last_particle_update_at
    if _last_particle_update_at is None:
        _last_particle_update_at = now
    dt = max(0.0, now - _last_particle_update_at)
    _last_particle_update_at = now
    for p in state.westminster_particles:
        p["x"] += p["vx"] * dt
        p["y"] += p["vy"] * dt


def _end_sequence():
    state.westminster_active = False
    state.westminster_phase = ""
    state.westminster_particles = []


def _update_active(now):
    phase = state.westminster_phase
    elapsed = now - state.westminster_phase_started_at

    if phase == "particles":
        _advance_particles(now)

    if phase == "flash" and elapsed >= config.WESTMINSTER_FLASH_SECONDS:
        _enter_phase("particles", now)
    elif phase == "particles" and elapsed >= config.WESTMINSTER_PARTICLE_SECONDS:
        _enter_phase("readout", now)
    elif phase == "readout" and elapsed >= config.WESTMINSTER_READOUT_SECONDS:
        _end_sequence()


def _maybe_restore_chime_fader(now):
    """Restores the ducked faders the instant the chime finishes playing.
    Deliberately called every frame regardless of state.westminster_active
    (2026-08-10 fix) -- a real bell sample (audio/HourClockBell/*.wav) can
    easily outlast the ~5.9s flash+particles+readout visual sequence (the
    old placeholder was a fixed 0.4s synthesized tone that never exposed
    this), and this check used to live inside _update_active() which stops
    being called the instant the visual sequence ends -- silently stranding
    the deck ducked forever if the chime was still playing at that point.
    Stays 0 (never fires) whenever Auto Voice was OFF at sequence start."""
    if state.westminster_chime_ends_at and now >= state.westminster_chime_ends_at:
        midi_driver.tween_channel_faders_to(
            state.westminster_pre_mute_volume, config.WESTMINSTER_AUDIO_RESTORE_SECONDS
        )
        state.westminster_chime_ends_at = 0.0


def update(now):
    """Per-frame poll, called from inputs/gamepad.py::process_events()
    alongside the other engines. Owns its own fader-tween pump (harmless if
    price_game_engine.update() already pumped it this frame -- update_fader_
    tween() is a no-op once no tween is in flight) so this module doesn't
    depend on that module's call ordering."""
    midi_driver.update_fader_tween(now)
    _maybe_restore_chime_fader(now)

    if state.westminster_active:
        _update_active(now)
        return

    lt = time.localtime(now)
    if lt.tm_min == 0 and lt.tm_sec < 2 and state.westminster_last_fired_hour != lt.tm_hour:
        state.westminster_last_fired_hour = lt.tm_hour
        _start_sequence(now)
