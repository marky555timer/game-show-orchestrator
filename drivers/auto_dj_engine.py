import time

import config
from state import state
from drivers import deck_orchestrator
from drivers.deck_orchestrator import get_now_playing as get_rekordbox_track

# ==========================================
# AUTO-DJ (Section 4): TRACK-LENGTH AUTO-ADVANCE + VOICE-OVER TRANSITION
# ==========================================
# Auto-DJ is on by default (state.auto_dj_enabled, seeded from
# config.AUTODJ_ENABLED_BY_DEFAULT). Gamepad Btn4 (inputs/gamepad.py,
# JOYBUTTONDOWN index 3, DJ mode only) toggles it via toggle_auto_dj().
#
# Track length comes from state.now_playing_duration -- the exact length of
# the file drivers/deck_orchestrator.py just loaded, since playback reads
# audio/music/ directly now instead of guessing at what Rekordbox has
# loaded. A missing/implausibly short duration falls back to
# config.AUTODJ_DEFAULT_TRACK_SECONDS.
#
# Radio-DJ style overlapping transition (2026-08-07): config.AUTODJ_PRE_SWITCH_SECONDS
# (15s) before the track ends, deck_orchestrator.trigger_announced_track_move()
# fires -- ONE call that loads the next track and runs the full sweeper -> VO ->
# deck-duck -> deck-swell choreography (audio/dj_engine.py::play_sweeper_and_announcement),
# already timed internally to land the swell back to 100% exactly as the VO
# ends. There's no more separate "fire VO now, fire deck move later" scheduling
# dance -- the old two-phase auto_dj_transition_at handoff existed only because
# the old VO system and the MIDI deck move were two uncoordinated pieces that
# had to be manually synced from here; the new engine coordinates them itself.
# If Auto-Announcement is toggled off (state.auto_announce_enabled, Gamepad
# Btn1), trigger_track_move() (plain crossfade, no VO) fires instead.
#
# update() is polled every frame from inputs/gamepad.py::process_events(),
# alongside the other per-frame engines (price_game_engine, mystery_band_engine).


# ==========================================
# FLX4 USB AUDIO "DEAD AIR" FAILSAFE -- REMOVED
# ==========================================
# Direct hardware sampling (audio/dead_air_sniffer.py, sounddevice + RMS)
# was removed: Rekordbox takes exclusive ASIO control of the Pioneer
# DDJ-FLX4, so any attempt to open the physical/virtual audio interface
# alongside it fails with [PaErrorCode -9985] Device unavailable. Auto-DJ
# now relies strictly on the software track-duration timer + deck
# playback state/metadata below to drive transitions.

# Single-trigger guard (race-condition fix): _start_timer() bumps
# _transition_cycle every time it opens a fresh tracking window for the
# current track. _fire_transition() only acts the first time it's called
# within a given cycle -- whichever signal (timer or FLX4 dead-air) gets
# there first wins, and any other signal that fires in the same window is
# a no-op instead of sending a second "Next Track".
_transition_cycle = 0
_last_fired_cycle = -1


def _lookup_duration(title):
    # state.now_playing_duration is the exact length of the track deck_orchestrator
    # just loaded -- no fuzzy title lookup needed now that we read the file
    # directly instead of guessing from a rekordbox.xml match (2026-08-07).
    duration = state.now_playing_duration
    if duration and duration >= config.AUTODJ_MIN_PLAUSIBLE_DURATION_SECONDS:
        return float(duration)
    return config.AUTODJ_DEFAULT_TRACK_SECONDS


def _start_timer(track_key, title):
    global _transition_cycle
    _transition_cycle += 1  # single-trigger guard: opens a fresh transition window
    state.auto_dj_track_key = track_key
    state.auto_dj_track_started_at = time.time()
    state.auto_dj_track_duration = _lookup_duration(title)
    state.auto_dj_announcement_played = False
    state.auto_dj_transition_at = 0.0
    print(f"[AUTO-DJ] Tracking {title!r} -- duration {state.auto_dj_track_duration:.0f}s "
          f"(transition sequence arms at -{config.AUTODJ_PRE_SWITCH_SECONDS:.0f}s).")


def toggle_auto_dj():
    """Web remote's Auto-DJ toggle (web/remote_server.py -- the only trigger
    since 2026-08-12, when the physical Btn4 binding was removed for being
    too easy to bump by accident): flips Auto-DJ on/off and arms the
    panel-3 "a ON"/"a OFF" confirmation overlay (rendered in
    graphics/matrix_canvas.py) for config.AUTODJ_TOGGLE_OVERLAY_SECONDS."""
    state.auto_dj_enabled = not state.auto_dj_enabled
    label = "a ON" if state.auto_dj_enabled else "a OFF"
    state.auto_dj_overlay_text = label
    state.auto_dj_overlay_until = time.time() + config.AUTODJ_TOGGLE_OVERLAY_SECONDS
    print(f"[AUTO-DJ] Toggle -> {label}")


def toggle_auto_announce():
    """Gamepad Btn1, DJ mode only: flips the Auto-Announcement station
    voice-over feature on/off and arms the panel-3 "v ON"/"v OFF"
    confirmation overlay for config.AUTO_ANNOUNCE_TOGGLE_OVERLAY_SECONDS.
    When off, Auto-DJ still auto-advances tracks at the same
    AUTODJ_PRE_SWITCH_SECONDS mark, it just skips the announcement and fires
    a plain crossfade instead.

    Instant Mute (Section 4): switching OFF instantly kills whatever
    sweeper/announcement is currently playing rather than letting it run
    out (dj_engine.stop_announcement()). No volume-reset step needed on the
    way back ON any more -- play_sweeper_and_announcement() and
    preview_announcement() both explicitly set their channels to full volume
    every time they play, so there's no stale-ducked-volume state that could
    carry over from a prior mute (the old cached-Sound-object volume model
    this replaced didn't have that guarantee, which is why it needed an
    explicit reset step)."""
    state.auto_announce_enabled = not state.auto_announce_enabled
    label = "v ON" if state.auto_announce_enabled else "v OFF"
    state.auto_announce_overlay_text = label
    state.auto_announce_overlay_until = time.time() + config.AUTO_ANNOUNCE_TOGGLE_OVERLAY_SECONDS

    if not state.auto_announce_enabled:
        deck_orchestrator.dj_engine.stop_announcement()

    print(f"[AUTO-DJ] Btn1 toggle -> Auto-Announcement {label}")


def notify_manual_track_move():
    """Called by inputs/gamepad.py whenever a manual Next/Prev is triggered
    (gamepad button, joystick axis, or keyboard shim): resets the
    auto-advance timer so Auto-DJ doesn't also fire a transition right on
    top of the manual one. The real per-track duration is re-armed on its
    own the moment the new track is confidently identified (see update()
    below), this just buys that identification window some slack.

    No longer also fires a VO preview here (removed 2026-08-07) -- a manual
    joystick move is now just a clean crossfade, nothing extra layered on
    top. The preview is reachable on demand from the web remote instead
    (web/remote_server.py's /api/announcement/preview,
    drivers/deck_orchestrator.py::preview_announcement()), not tied to
    every real track switch."""
    state.auto_dj_track_started_at = time.time()
    print("[AUTO-DJ] Manual track move -- auto-advance timer reset.")


def _fire_transition(track_key):
    """Plain crossfade, no VO (Auto-Announcement OFF, or the manual/no-VO
    path). Re-arms the timer immediately so this doesn't fire again every
    frame while the crossfade plays out.

    Single-trigger guard: refuses to act twice within the same
    _transition_cycle (see the module-level comment above)."""
    global _last_fired_cycle
    if _last_fired_cycle == _transition_cycle:
        print("[AUTO-DJ] Transition already fired for this track cycle -- ignoring duplicate trigger.")
        return
    _last_fired_cycle = _transition_cycle

    deck_orchestrator.trigger_track_move("next")
    title, _artist = get_rekordbox_track()
    _start_timer(track_key, title)


def _fire_announced_transition(track_key):
    """Sweeper -> VO -> deck-duck -> deck-swell transition -- one call now
    does what used to take two coordinated phases (see module docstring)."""
    global _last_fired_cycle
    if _last_fired_cycle == _transition_cycle:
        print("[AUTO-DJ] Transition already fired for this track cycle -- ignoring duplicate trigger.")
        return
    _last_fired_cycle = _transition_cycle

    state.auto_dj_announcement_played = True
    deck_orchestrator.trigger_announced_track_move()
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

    # Auto-DJ keeps running (timers, announcements, transitions) regardless
    # of state.mode -- Quiz Mode gameplay must never suspend it, so the only
    # gate left here is the on/off toggle itself.
    if not state.auto_dj_enabled:
        return
    if deck_orchestrator.has_pending_move():
        return  # a crossfade (manual or auto) is already in flight

    elapsed = now - state.auto_dj_track_started_at
    trigger_at = max(0.0, state.auto_dj_track_duration - config.AUTODJ_PRE_SWITCH_SECONDS)
    if elapsed < trigger_at:
        return

    if not state.auto_announce_enabled:
        print(f"[AUTO-DJ] {state.auto_dj_track_duration:.0f}s track duration reached -- "
              f"auto-advancing (Auto-Announcement OFF).")
        _fire_transition(track_key)
        return

    print(f"[AUTO-DJ] {state.auto_dj_track_duration:.0f}s track duration reached -- "
          f"firing announced transition.")
    _fire_announced_transition(track_key)
