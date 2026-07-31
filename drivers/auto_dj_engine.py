import time

import mss
from PIL import Image

import config
from state import state
from drivers import deck_orchestrator
from drivers.rekordbox_driver import get_rekordbox_track, rb_driver
from audio.audio_engine import (
    play_station_announcement, stop_station_announcement, reset_announcement_volume,
)

# ==========================================
# AUTO-DJ (Section 4): TRACK-LENGTH AUTO-ADVANCE + VOICE-OVER TRANSITION
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
# Radio-DJ style overlapping transition: config.AUTODJ_PRE_SWITCH_SECONDS
# (15s) before the track ends, a random station-announcement voice-over
# (audio/announcements/, via audio.audio_engine.play_station_announcement)
# starts playing. The actual track transition -- deck-start MIDI sequence +
# TrackSearch, via deck_orchestrator.trigger_track_move() -- fires
# config.AUTODJ_ANNOUNCE_LEAD_SECONDS (2s) before that announcement clip
# finishes, so the VO bridges the end of the outgoing track into the start
# of the next one. If Auto-Announcement is toggled off (state.auto_announce_enabled,
# Gamepad Btn1), the VO step is skipped and the transition fires right at
# the 15s mark instead.
#
# update() is polled every frame from inputs/gamepad.py::process_events(),
# alongside the other per-frame engines (price_game_engine, mystery_band_engine).


# ==========================================
# PIXEL-SCANNING "DEAD AIR" FAILSAFE (Section 2)
# ==========================================
# Visual safety net that's completely independent of rekordbox.xml metadata
# and the track-duration timer above: samples the on-screen waveform strip
# just to the right of the deck centerlines
# (config.AUTODJ_DEAD_AIR_SCAN_RECT). If every pixel sampled is pure black
# (RGB sum == 0), nothing is loaded/queued there -- a strong signal that the
# normal metadata-driven auto-advance path has silently failed -- so, if
# Auto-DJ is active and hasn't already armed a transition this cycle, fire
# the track transition immediately rather than risk dead air.
_last_pixel_scan_at = 0.0


def _scan_dead_air_pixels():
    """Grabs config.AUTODJ_DEAD_AIR_SCAN_RECT and returns the sum of every
    R+G+B value across the sampled pixels (0 means the whole strip is pure
    black). Never raises -- a capture failure is treated as "not dead air"
    (returns 1) so a transient screen-grab error can never force a spurious
    transition."""
    try:
        with mss.mss() as sct:
            sct_img = sct.grab(config.AUTODJ_DEAD_AIR_SCAN_RECT)
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            return sum(r + g + b for r, g, b in img.getdata())
    except Exception as e:
        print(f"[AUTO-DJ] Dead-air pixel scan failed: {e}")
        return 1


def _dead_air_failsafe(track_key, now):
    """Returns True if it fired an immediate transition. Throttled to
    config.AUTODJ_DEAD_AIR_SCAN_INTERVAL_SECONDS -- a 1px-wide screen grab is
    cheap, but there's no need to do it every single frame."""
    global _last_pixel_scan_at
    if state.auto_dj_transition_at:
        return False  # a transition is already armed/triggered this cycle
    if now - _last_pixel_scan_at < config.AUTODJ_DEAD_AIR_SCAN_INTERVAL_SECONDS:
        return False
    _last_pixel_scan_at = now

    if _scan_dead_air_pixels() != 0:
        return False

    print("[AUTO-DJ] Dead-air failsafe: waveform pixel scan is pure black -- "
          "forcing immediate track transition.")
    _fire_transition(track_key)
    return True


def _lookup_duration(title):
    duration = rb_driver.db.get_duration(title)
    if duration and duration >= config.AUTODJ_MIN_PLAUSIBLE_DURATION_SECONDS:
        return float(duration)
    return config.AUTODJ_DEFAULT_TRACK_SECONDS


def _start_timer(track_key, title):
    state.auto_dj_track_key = track_key
    state.auto_dj_track_started_at = time.time()
    state.auto_dj_track_duration = _lookup_duration(title)
    state.auto_dj_announcement_played = False
    state.auto_dj_transition_at = 0.0
    print(f"[AUTO-DJ] Tracking {title!r} -- duration {state.auto_dj_track_duration:.0f}s "
          f"(transition sequence arms at -{config.AUTODJ_PRE_SWITCH_SECONDS:.0f}s).")


def toggle_auto_dj():
    """Gamepad Btn4, DJ mode only: flips Auto-DJ on/off and arms the panel-3
    "AUTO ON"/"AUTO OFF" confirmation overlay (rendered in
    graphics/matrix_canvas.py) for config.AUTODJ_TOGGLE_OVERLAY_SECONDS."""
    state.auto_dj_enabled = not state.auto_dj_enabled
    label = "AUTO ON" if state.auto_dj_enabled else "AUTO OFF"
    state.auto_dj_overlay_text = label
    state.auto_dj_overlay_until = time.time() + config.AUTODJ_TOGGLE_OVERLAY_SECONDS
    print(f"[AUTO-DJ] Btn4 toggle -> {label}")


def toggle_auto_announce():
    """Gamepad Btn1, DJ mode only: flips the Auto-Announcement station
    voice-over feature on/off and arms the panel-3 "v ON"/"v OFF"
    confirmation overlay for config.AUTO_ANNOUNCE_TOGGLE_OVERLAY_SECONDS.
    When off, Auto-DJ still auto-advances tracks at the same
    AUTODJ_PRE_SWITCH_SECONDS mark, it just skips the announcement overlay
    and fires the transition immediately instead of waiting on a VO clip.

    Instant Mute & Volume Reset (Section 4): switching OFF instantly kills
    whatever announcement clip is currently playing rather than letting it
    run out, and if a transition was scheduled off that clip's runtime
    (auto_dj_transition_at), fires it right now instead of waiting on audio
    that no longer exists. Switching back ON resets every cached
    announcement Sound to full volume (1.0) so a prior kill can never leave
    the next playback muted/ducked."""
    state.auto_announce_enabled = not state.auto_announce_enabled
    label = "v ON" if state.auto_announce_enabled else "v OFF"
    state.auto_announce_overlay_text = label
    state.auto_announce_overlay_until = time.time() + config.AUTO_ANNOUNCE_TOGGLE_OVERLAY_SECONDS

    if not state.auto_announce_enabled:
        stop_station_announcement()
        if state.auto_dj_transition_at:
            state.auto_dj_transition_at = time.time()
    else:
        reset_announcement_volume()

    print(f"[AUTO-DJ] Btn1 toggle -> Auto-Announcement {label}")


def notify_manual_track_move():
    """Called by inputs/gamepad.py whenever a manual Next/Prev is triggered
    (gamepad button, joystick axis, or keyboard shim): resets the
    auto-advance timer so Auto-DJ doesn't also fire a transition right on
    top of the manual one. The real per-track duration is re-armed on its
    own the moment the new track is confidently identified (see update()
    below), this just buys that identification window some slack.

    Testing Mode (Section 4): while Auto-Announcement is ON, also fire the
    station announcement VO here -- not just on Auto-DJ's own overlapping
    transition -- so voice viability can be verified on demand from every
    normal track switch, manual or automatic."""
    state.auto_dj_track_started_at = time.time()
    if state.auto_announce_enabled:
        play_station_announcement()
        print("[AUTO-DJ] Auto-Announcement testing mode -- VO fired on manual track switch.")
    print("[AUTO-DJ] Manual track move -- auto-advance timer reset.")


def _fire_transition(track_key):
    """Sends the deck-start MIDI sequence + TrackSearch (via
    deck_orchestrator.trigger_track_move, which itself now primes the
    target deck with Cue -> tick -> Play/Pause before searching) and
    re-arms the timer immediately so this doesn't fire again every frame
    while the crossfade plays out and OCR catches up to the new track."""
    deck_orchestrator.trigger_track_move("next")
    title, _artist = get_rekordbox_track()
    _start_timer(track_key, title)


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

    # Section 2: visual dead-air failsafe -- independent of the metadata/
    # timer logic below, checked every cycle before it.
    if _dead_air_failsafe(track_key, now):
        return

    # Phase 2: the announcement (or the no-VO fallback) has already fired
    # this cycle -- just wait for its scheduled overlap moment.
    if state.auto_dj_transition_at:
        if now >= state.auto_dj_transition_at:
            print("[AUTO-DJ] Overlap window elapsed -- firing track transition.")
            _fire_transition(track_key)
        return

    elapsed = now - state.auto_dj_track_started_at
    trigger_at = max(0.0, state.auto_dj_track_duration - config.AUTODJ_PRE_SWITCH_SECONDS)
    if elapsed < trigger_at:
        return

    if not state.auto_announce_enabled:
        # Auto-Announcement is off -- behave like the plain auto-advance:
        # fire the transition right at the trigger point, no VO.
        print(f"[AUTO-DJ] {state.auto_dj_track_duration:.0f}s track duration reached -- "
              f"auto-advancing (Auto-Announcement OFF).")
        _fire_transition(track_key)
        return

    # Phase 1: kick off the station announcement and schedule the actual
    # transition AUTODJ_ANNOUNCE_LEAD_SECONDS before it finishes.
    state.auto_dj_announcement_played = True
    duration = play_station_announcement()
    if duration <= 0.0:
        # No announcement clip available -- fall back to firing next frame
        # rather than stalling the show waiting on nothing.
        state.auto_dj_transition_at = now
        print("[AUTO-DJ] No station announcement available -- transitioning immediately.")
    else:
        lead = config.AUTODJ_ANNOUNCE_LEAD_SECONDS
        state.auto_dj_transition_at = now + max(0.0, duration - lead)
        print(f"[AUTO-DJ] Station announcement playing ({duration:.1f}s) -- track transition "
              f"scheduled in {state.auto_dj_transition_at - now:.1f}s (-{lead:.0f}s before VO ends).")
