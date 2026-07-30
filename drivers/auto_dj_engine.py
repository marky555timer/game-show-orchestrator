import time

import config
from state import state
from drivers import deck_orchestrator
from drivers.rekordbox_driver import get_rekordbox_track, rb_driver

# ==========================================
# AUTO-DJ (Section 4): TRACK-LENGTH AUTO-ADVANCE
# ==========================================
# Auto-DJ is on by default (state.auto_dj_enabled, seeded from
# config.AUTODJ_ENABLED_BY_DEFAULT). Gamepad Btn4 (inputs/gamepad.py,
# JOYBUTTONDOWN index 3, DJ mode only) toggles it via toggle_auto_dj().
#
# Track length comes from rekordbox.xml's TotalTime attribute (already
# parsed by drivers/rekordbox_driver.py -- this is the show's only real
# source of track-duration metadata, since tracks are driven via MIDI/OCR
# rather than played directly from a known file path). A missing/implausibly
# short TotalTime falls back to config.AUTODJ_DEFAULT_TRACK_SECONDS.
#
# update() is polled every frame from inputs/gamepad.py::process_events(),
# alongside the other per-frame engines (price_game_engine, mystery_band_engine).


def _lookup_duration(title):
    duration = rb_driver.db.get_duration(title)
    if duration and duration >= config.AUTODJ_MIN_PLAUSIBLE_DURATION_SECONDS:
        return float(duration)
    return config.AUTODJ_DEFAULT_TRACK_SECONDS


def _start_timer(track_key, title):
    state.auto_dj_track_key = track_key
    state.auto_dj_track_started_at = time.time()
    state.auto_dj_track_duration = _lookup_duration(title)
    print(f"[AUTO-DJ] Tracking {title!r} -- duration {state.auto_dj_track_duration:.0f}s "
          f"(auto-advance at -{config.AUTODJ_PRE_SWITCH_SECONDS:.0f}s).")


def toggle_auto_dj():
    """Gamepad Btn4, DJ mode only: flips Auto-DJ on/off and arms the panel-3
    "AUTO ON"/"AUTO OFF" confirmation overlay (rendered in
    graphics/matrix_canvas.py) for config.AUTODJ_TOGGLE_OVERLAY_SECONDS."""
    state.auto_dj_enabled = not state.auto_dj_enabled
    label = "AUTO ON" if state.auto_dj_enabled else "AUTO OFF"
    state.auto_dj_overlay_text = label
    state.auto_dj_overlay_until = time.time() + config.AUTODJ_TOGGLE_OVERLAY_SECONDS
    print(f"[AUTO-DJ] Btn4 toggle -> {label}")


def notify_manual_track_move():
    """Called by inputs/gamepad.py whenever a manual Next/Prev is triggered
    (gamepad button, joystick axis, or keyboard shim): resets the
    auto-advance timer so Auto-DJ doesn't also fire a transition right on
    top of the manual one. The real per-track duration is re-armed on its
    own the moment the new track is confidently identified (see update()
    below), this just buys that identification window some slack."""
    state.auto_dj_track_started_at = time.time()
    print("[AUTO-DJ] Manual track move -- auto-advance timer reset.")


def update(now):
    track_key = state.factoid_track_key  # "" until a track is confidently identified
    if not track_key:
        return

    if track_key != state.auto_dj_track_key:
        title, _artist = get_rekordbox_track()
        _start_timer(track_key, title)
        return

    if not state.auto_dj_enabled or state.mode != state.MODE_DJ:
        return
    if deck_orchestrator.has_pending_move():
        return  # a crossfade (manual or auto) is already in flight

    elapsed = now - state.auto_dj_track_started_at
    trigger_at = max(0.0, state.auto_dj_track_duration - config.AUTODJ_PRE_SWITCH_SECONDS)
    if elapsed >= trigger_at:
        print(f"[AUTO-DJ] {state.auto_dj_track_duration:.0f}s track duration reached -- auto-advancing.")
        deck_orchestrator.trigger_track_move("next")
        # Re-arm immediately so this doesn't fire again every frame while
        # the crossfade plays out and OCR catches up to the new track.
        title, _artist = get_rekordbox_track()
        _start_timer(track_key, title)
