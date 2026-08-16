"""drivers/hot_track_engine.py
"Hot track" feature (2026-08-15): an audience YouTube import (drivers/
youtube_import_engine.py) gets priority AI trivia prefetch, a confirmation
flash on panels 3-6 (graphics/matrix_canvas.py::_draw_hot_track_flash) the
instant that trivia is ready, and a guaranteed identify-quiz fallback if
Haiku hasn't produced anything by the time the track actually starts
playing. Gated on the CURRENTLY PLAYING track having at least
config.HOT_TRACK_MIN_REMAINING_SECONDS left when the import job starts --
see arm(). A separate, tighter config.HOT_TRACK_CUE_BARRIER_SECONDS gate,
checked at download-completion time (place_cue()), decides whether the
import can safely take the very next transition slot or needs to wait one
more (drivers/deck_orchestrator.py's 2-deep cue queue).

Deliberately separate from drivers/mystery_band_engine.py's teaser/reveal
state machine (check_new_track/_start_mystery): that machine is a
one-shot-per-track-key latch wired to the LIVE deck's own per-frame
ensure_prefetch/check_new_track polling, not designed for arming an
arbitrary not-yet-playing track on demand. Only its question-BUILDING
helper (build_identify_fallback_question) is reused here.

update() is polled every frame from inputs/gamepad.py::process_events(), in
the same per-frame slot mystery_band_engine.check_new_track() already
occupies (right after ensure_prefetch()) -- state.factoid_track_key must
already reflect whatever's playing THIS frame before either engine reads it.

Only one job is ever tracked at a time -- youtube_import_engine.py's own
single-job guard (start_import() rejects a second import while one's
running) means there's no concurrent-arm case to handle here.
"""
import time

import config
from state import state
from drivers import factoid_engine
from drivers import deck_orchestrator
from drivers.music_library import sanitize_track_key

_armed_key = ""                    # "" = no job armed
_armed_title = ""
_armed_artist = ""
_armed_at_deck_change_count = -1   # state.deck_change_count snapshot at arm() -- staleness check
_flash_fired = False
_cue_placed = False                # place_cue() is one-shot per job


def _remaining_seconds(now=None):
    """Seconds left on the currently-playing (Auto-DJ-tracked) track,
    clamped >= 0. Same formula duplicated inline in drivers/auto_dj_engine.py,
    web/remote_server.py, and graphics/overlay_panel.py -- not refactored
    there in this change, just given one canonical home here since this is
    the first module that needs to gate real decisions off it."""
    now = now if now is not None else time.time()
    return max(0.0, state.auto_dj_track_duration - (now - state.auto_dj_track_started_at))


def arm(title, artist):
    """Called from youtube_import_engine._run_import() the instant the
    probed title/artist are known. Returns True if the currently-playing
    track has enough runway for the hot-track treatment (and kicks off
    priority prefetch); False means "skip the whole feature for this
    import" -- final for this job, not re-armed later from a fresh
    remaining-time read."""
    global _armed_key, _armed_title, _armed_artist
    global _armed_at_deck_change_count, _flash_fired, _cue_placed

    if _remaining_seconds() < config.HOT_TRACK_MIN_REMAINING_SECONDS:
        _armed_key = ""
        return False

    _armed_title, _armed_artist = title, artist
    _armed_key = sanitize_track_key(title, artist)
    _armed_at_deck_change_count = state.deck_change_count
    _flash_fired = False
    _cue_placed = False

    factoid_engine.ensure_priority_prefetch(title, artist)
    print(f"[HOT TRACK] Armed priority prefetch for {title!r} - {artist!r} "
          f"({_remaining_seconds():.0f}s runway on the current track).")
    return True


def cancel():
    """Import failed after arm() succeeded, or staleness was detected --
    drops the armed job so update()/place_cue() stop tracking a track that
    will never play (or already has, under different context)."""
    global _armed_key
    _armed_key = ""


def place_cue(dest_path):
    """Called once the download+convert is done and dest_path is real.
    Decides slot 0 ("next") vs slot 1 ("after next") using LIVE remaining-
    time data at THIS moment, not whatever was true at arm() time -- and
    re-checks staleness (has the gate track already transitioned away?).
    No-op if arm() never returned True, or this has already run once for
    this job."""
    global _cue_placed
    if not _armed_key or _cue_placed:
        return
    _cue_placed = True

    if state.deck_change_count != _armed_at_deck_change_count:
        print("[HOT TRACK] Gate track already changed since import started -- "
              "not forcing a cue placement, added to library only.")
        return

    if _remaining_seconds() >= config.HOT_TRACK_CUE_BARRIER_SECONDS:
        deck_orchestrator.set_cued_track(dest_path)
        print(f"[HOT TRACK] Placed in slot 0 (next) -- "
              f"{_remaining_seconds():.0f}s still on the clock.")
    else:
        deck_orchestrator.queue_cued_track_after_next(dest_path)
        print(f"[HOT TRACK] Only {_remaining_seconds():.0f}s left -- "
              f"placed in slot 1 (after next) instead.")


def update(now):
    """Per-frame poll (inputs/gamepad.py::process_events())."""
    global _flash_fired
    if not _armed_key:
        return

    # Staleness re-check every frame, not just at arm()/place_cue() -- if
    # the gate track has already transitioned away, drop the job outright
    # rather than act on outdated context.
    if state.deck_change_count != _armed_at_deck_change_count:
        cancel()
        return

    cached = factoid_engine.cached_question_count(_armed_title, _armed_artist)

    if not _flash_fired and cached > 0:
        state.hot_track_flash_started_at = now
        _flash_fired = True
        print(f"[HOT TRACK] Trivia ready for {_armed_title!r} -- firing confirmation flash.")

    activated = (state.factoid_track_key == _armed_key)
    if activated:
        # Skip if the normal Mystery Band teaser is already covering (or
        # about to cover, if deferred behind an announcement VO) this exact
        # moment with its own identify quiz -- avoid a redundant double-apply.
        mystery_covering = state.mystery_active or now < state.mystery_defer_until
        if not state.track_question_queue and not mystery_covering and cached == 0:
            from drivers import mystery_band_engine  # lazy: same circular-
            # import shape factoid_engine.load_exhausted_fallback_question()
            # already sidesteps this way.
            question = mystery_band_engine.build_identify_fallback_question(_armed_artist)
            if question:
                factoid_engine.apply_mystery_identify_question(question)
                print(f"[HOT TRACK] No trivia ready by playback -- "
                      f"applied identify-quiz fallback for {_armed_title!r}.")
            # else: no artist known for this import at all -- nothing to
            # fall back to, the track just plays with whatever the normal
            # prefetch eventually produces, same as any unknown-artist track.
        cancel()  # job's done either way (happy path or fallback applied)
