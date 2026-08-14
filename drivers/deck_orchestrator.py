"""Deck orchestration: advances "next"/"back" track transitions using the
native audio/dj_engine.py instead of puppeting Rekordbox over MIDI.

Public API (trigger_track_move, update, has_pending_move, get_now_playing)
is unchanged in shape from the old MIDI-based version, so inputs/gamepad.py,
web/remote_server.py, and drivers/auto_dj_engine.py needed no logic changes
at their call sites -- only this module's internals changed, plus a small
import swap at the 3 call sites that used to import get_rekordbox_track from
drivers/rekordbox_driver.py (see 2026-08-07 migration).

state.deck1_track / deck2_track / deckN_confident / deckN_track_source keep
their existing meaning too -- populated here from an exact file-tag read
instead of an OCR guess, so every track is now "source": "native" and always
confident (there's nothing left to be unsure about once you're reading the
file directly instead of a screenshot of someone else's software).

trigger_announced_track_move() is the sweeper/VO-choreographed transition
(replaces audio_engine.py's retired play_station_announcement(), see
drivers/auto_dj_engine.py) -- used by Auto-DJ's own automatic transitions.
trigger_track_move() (manual gamepad/web "next"/"back") checks
state.auto_announce_enabled itself and picks announced-vs-plain to match
(2026-08-08) -- one setting governs every transition, not just Auto-DJ's.

Continuous preload (2026-08-07 -- fixes an audible stutter on "next"): a
transition used to decode its track AND (on first use of a given file) its
sweeper/announcement synchronously close to trigger time. The sweeper/
announcement decode in particular happened inside update(), on the main
per-frame thread, which could stall the ramp-scheduler thread (audio/
dj_engine.py) badly enough to produce an audible glitch right as the
transition started -- the one moment it's most audible. Fix, two parts:
  1. _warm_sound_cache() decodes every sweeper/announcement file into
     _sound_cache once, in the background, at import time. There are only a
     few dozen of these short files total, so holding all of them decoded is
     cheap, and it means _load_cached() is a plain dict lookup at every
     trigger from then on -- never a decode.
  2. _preloaded_track holds the *next* track, fully decoded, ready to go,
     maintained continuously: one gets queued at import time, and a new one
     starts decoding the moment the current one gets consumed by a trigger.
     Only ever one track ahead is held in RAM (not the whole library --
     that's ~17GB of decoded PCM for a library this size), matching the
     original "pre-decode during the prior track's runway" design goal.
  A trigger that finds nothing preloaded yet (e.g. two rapid manual presses)
  falls back to the old reactive background-load path rather than failing.

Track decode itself later moved out of the process entirely (2026-08-08):
buffer-size increases and dropping the decode thread's OS priority
(audio/dj_engine.py's _lower_current_thread_priority) both measurably
helped a dropout landing right when a decode starts, but neither fully
cleared it -- a thread in the same process can still compete with the
real-time audio callback/ramp-scheduler threads for CPU scheduling to some
degree no matter what priority hint it's given. _decode_process
(audio/dj_engine.py's DecodeProcess) runs decode in a genuinely separate OS
process instead, which cannot compete with them at all. Sweeper/
announcement decode stayed in-process (_load_cached/_warm_sound_cache) --
small one-shot files, not worth the IPC overhead.

Play history (2026-08-08 -- fixes "back" replaying random tracks instead of
the actual previous one): _history is every track played this session, in
order, with _history_index marking which entry is currently active. "back"
steps the index down and replays that exact track; "next" steps back up
through any history you'd stepped away from (so back-then-next returns you
to where you were) before falling through to picking a fresh track once
you're at the head. Only the head-of-history "pick fresh" path is preloaded
-- back/forward-through-history are rare, deliberate actions compared to the
continuous auto-DJ forward flow the preload exists for, so they just take
the small reactive-load path instead of needing their own preload buffer.
"""
import glob
import os
import random
import threading
import time

import config
from state import state
from drivers.music_library import music_library
from drivers.branding_engine import notify_deck_change
from drivers import lighting_engine
from audio.dj_engine import (
    DJEngine, DecodeProcess, load_out_of_process,
    SWEEPER_OVERLAP_SECONDS, _lower_current_thread_priority,
)

CROSSFADE_SECONDS = 1.5
_TRACK_HISTORY_SIZE = 8
_SWEEPER_HISTORY_SIZE = 3
_DECK_NAMES = {1: "deck1", 2: "deck2"}

dj_engine = DJEngine()
# Track decode runs in a separate OS process (audio/decode_worker.py), not
# just a background thread -- see DecodeProcess's docstring, 2026-08-08.
# Sweeper/announcement decode (small, one-shot, warmed once at startup) stays
# in-process via dj_engine.load() -- not worth the IPC overhead for files
# that are only ever decoded once each, for the whole session.
_decode_process = DecodeProcess()

_pending = None  # {"phase", target_deck, track, sound, busy_until, announced}
_sweeper_history = []
_announcement_deck = []       # shuffled remaining announcement paths for the current cycle
_last_announcement_played = None
_sound_cache = {}  # path -> loaded Sound, for the small fixed pool of sweepers/announcements

_preloaded_track = None  # {"track": ..., "sound": ...} once ready, else None
_preload_in_flight = False

_history = []  # every track played this session, in order
_history_index = -1  # index of the currently-active entry in _history

# Web remote "Cue" button (2026-08-12): operator picks a specific track from
# the Library page to play next, overriding both the normal random pick and
# a pending back-history replay. One-shot -- cleared the instant it's
# consumed by a "next" move, not sticky across multiple transitions. Holds
# the track's path (music_library's own stable identity), not a track dict,
# so it stays valid even if the library gets rescanned in between.
_cued_track_path = None


def _pick_next_track():
    tracks = music_library.all_tracks()
    if not tracks:
        return None
    recent_paths = {t["path"] for t in _history[-_TRACK_HISTORY_SIZE:]}
    candidates = [t for t in tracks if t["path"] not in recent_paths]
    if not candidates:
        candidates = tracks  # exhausted the no-repeat window -- everything's fair game again

    filtered = _apply_show_filters(candidates)
    if filtered:
        candidates = filtered
    # else: the Setup page's filters would eliminate every remaining
    # candidate for this pick -- ignore them just this once rather than
    # stall playback (same defensive philosophy as
    # config.QUIZ_GATE_EMPTY_CACHE_TIMEOUT_SECONDS elsewhere).

    return random.choice(candidates)


def _apply_show_filters(candidates):
    """Trivia Night show flow (2026-08-13): the Setup page's crowd-matching
    checkboxes, wiring drivers/music_metadata_engine.py's tags (previously
    unused -- "Phase 3, not yet wired") into actual track picking.
    Untagged tracks (or tracks with an untagged genre specifically) pass
    through unfiltered on a per-field basis -- only ~90% of the library is
    tagged, and treating "unknown" as "exclude" would silently starve the
    rotation for a partially-tagged library."""
    if not (state.show_exclude_explicit or state.show_exclude_slow_dance
            or state.show_exclude_never_charted or state.show_allowed_genres):
        return candidates  # no filters active -- skip the metadata lookup entirely

    from drivers.music_metadata_engine import get_metadata_for
    from drivers.music_library import sanitize_track_key

    out = []
    for t in candidates:
        meta = get_metadata_for(sanitize_track_key(t["title"], t["artist"]))
        if meta is None:
            out.append(t)
            continue
        if state.show_exclude_explicit and meta.get("explicit"):
            continue
        if state.show_exclude_slow_dance and meta.get("slow_dance"):
            continue
        if state.show_exclude_never_charted and meta.get("never_charted"):
            continue
        if state.show_allowed_genres and meta.get("genre") and meta["genre"] not in state.show_allowed_genres:
            continue
        out.append(t)
    return out


def set_cued_track(path):
    """Web remote Cue button/upload-auto-cue (web/remote_server.py): makes
    `path` (a music_library track's own "path" field) the next track a
    "next" move plays, overriding the normal random pick AND a pending
    back-history replay -- the operator explicitly chose this one, so it
    should win over passively stepping forward through where "back" had
    been. Resolved fresh (and consumed) inside _begin_move() at the moment
    "next" is actually pressed, not here, so it's correct regardless of
    whatever got speculatively preloaded in between (see _begin_move)."""
    global _cued_track_path
    _cued_track_path = path


def clear_cued_track():
    global _cued_track_path
    _cued_track_path = None


def get_cued_track():
    """Current cue, resolved against the live library (a cued file that's
    since been deleted resolves to None), for web/remote_server.py's
    /api/library/tracks status. Does NOT consume it -- purely a read."""
    if _cued_track_path is None:
        return None
    return next((t for t in music_library.all_tracks() if t["path"] == _cued_track_path), None)


def _pick_with_history(paths, history, history_size):
    if not paths:
        return None
    candidates = [p for p in paths if p not in history] or paths
    choice = random.choice(candidates)
    history.append(choice)
    if len(history) > history_size:
        history.pop(0)
    return choice


def _pick_sweeper():
    paths = glob.glob(os.path.join(config.SWEEPERS_DIR, "*.wav"))
    return _pick_with_history(paths, _sweeper_history, _SWEEPER_HISTORY_SIZE)


def _pick_announcement():
    """Shuffled-deck selection, not independent random.choice(): every
    announcement clip plays exactly once before any of them repeat -- like
    dealing through a shuffled deck of cards and reshuffling once it's
    empty. Plain random re-selection (the old "avoid the last N plays"
    window _pick_with_history uses for sweepers) can still resurface the
    same clip well before every other one has had a turn on a small
    announcements pool -- a deck guarantees full coverage first."""
    global _announcement_deck, _last_announcement_played
    paths = (glob.glob(os.path.join(config.ANNOUNCEMENTS_DIR, "*.wav"))
             or glob.glob(os.path.join(config.ANNOUNCEMENTS_DIR, "*.mp3")))
    if not paths:
        return None

    # Drop any stale entries left over from files removed/renamed since the
    # deck was last built.
    _announcement_deck = [p for p in _announcement_deck if p in paths]

    if not _announcement_deck:
        _announcement_deck = list(paths)
        random.shuffle(_announcement_deck)
        # Avoid dealing the clip that just finished the previous cycle
        # right back out as the first card of the new one.
        if len(_announcement_deck) > 1 and _announcement_deck[-1] == _last_announcement_played:
            _announcement_deck[-1], _announcement_deck[0] = _announcement_deck[0], _announcement_deck[-1]

    choice = _announcement_deck.pop()
    _last_announcement_played = choice
    return choice


def _load_cached(path):
    sound = _sound_cache.get(path)
    if sound is None:
        sound = dj_engine.load(path)
        _sound_cache[path] = sound
    return sound


def _warm_sound_cache():
    """Decodes every sweeper/announcement file once, in the background, so
    no transition ever has to decode one synchronously -- see module
    docstring."""
    def _load_all():
        _lower_current_thread_priority()
        paths = (glob.glob(os.path.join(config.SWEEPERS_DIR, "*.wav"))
                  + glob.glob(os.path.join(config.ANNOUNCEMENTS_DIR, "*.wav"))
                  + glob.glob(os.path.join(config.ANNOUNCEMENTS_DIR, "*.mp3")))
        for path in paths:
            _load_cached(path)
        print(f"[DECK ORCHESTRATOR] Warmed {len(paths)} sweeper/announcement files into cache.")

    threading.Thread(target=_load_all, daemon=True).start()


def _start_preloading_next_track():
    """Kicks off decoding the next track in the background, if nothing is
    already preloaded or in flight. Safe/idempotent to call any time."""
    global _preloaded_track, _preload_in_flight
    if _preloaded_track is not None or _preload_in_flight:
        return
    track = _pick_next_track()
    if track is None:
        return
    _preload_in_flight = True

    def _load():
        global _preloaded_track, _preload_in_flight
        _lower_current_thread_priority()
        sound = load_out_of_process(track["path"], _decode_process)
        _preloaded_track = {"track": track, "sound": sound}
        _preload_in_flight = False
        print(f"[DECK ORCHESTRATOR] Preloaded next track: '{track['title']}'")

    threading.Thread(target=_load, daemon=True).start()


_warm_sound_cache()
_start_preloading_next_track()


def preview_announcement():
    """On-demand "what does an announcement sound like" preview -- replaces
    audio_engine.py's old play_station_announcement() testing-mode call in
    drivers/auto_dj_engine.py::notify_manual_track_move(). Doesn't touch
    either deck. Returns True if something played.

    Also fires the announcement text banner (2026-08-10) -- previously only
    a real "announced" song transition set state.announcement_banner_from/
    until, so an operator testing via this preview button (or the real
    transition simply happening while nobody was looking at the matrix,
    since the banner only holds for ANNOUNCEMENT_BANNER_HOLD_SECONDS) had
    no reliable way to actually see the banner and confirm the saved text
    was showing up at all."""
    sweeper_path, announcement_path = _pick_sweeper(), _pick_announcement()
    if not sweeper_path or not announcement_path:
        print("[DECK ORCHESTRATOR] Preview requested but no sweeper/announcement files available.")
        return False
    announcement_sound = _load_cached(announcement_path)
    dj_engine.preview_announcement(_load_cached(sweeper_path), announcement_sound)
    state.announcement_banner_from = time.time()
    state.announcement_banner_until = state.announcement_banner_from + config.ANNOUNCEMENT_BANNER_HOLD_SECONDS
    return True


def _begin_reactive_move(inactive, track, announced, direction, label, sweeper_only=False):
    """Loads `track` in the background and starts the transition once it's
    ready -- used for history navigation and as the no-preload fallback,
    neither of which have a pre-decoded Sound sitting around waiting."""
    global _pending
    _pending = {
        "phase": "loading",
        "target_deck": inactive,
        "track": track,
        "sound": None,
        "busy_until": None,
        "announced": announced,
        "sweeper_only": sweeper_only,
    }

    def _load_in_background():
        _lower_current_thread_priority()
        sound = load_out_of_process(track["path"], _decode_process)
        if _pending is not None:  # a newer trigger could have superseded this one
            _pending["sound"] = sound
            _pending["phase"] = "ready"

    threading.Thread(target=_load_in_background, daemon=True).start()
    kind = "sweeper-only " if sweeper_only else ("announced " if announced else "")
    print(f"[DECK ORCHESTRATOR] {direction.upper()} -> {label} {kind}"
          f"'{track['title']}' onto Deck {inactive} in the background.")


def _begin_move(direction, announced, sweeper_only=False):
    global _pending, _preloaded_track, _history_index, _cued_track_path
    if _pending is not None:
        print("[DECK ORCHESTRATOR] Move already in flight -- ignoring duplicate trigger.")
        return

    inactive = 2 if state.active_deck == 1 else 1

    if direction == "back":
        if _history_index <= 0:
            print("[DECK ORCHESTRATOR] BACK -> no earlier track in history.")
            return
        _history_index -= 1
        track = _history[_history_index]
        state.deck_change_count += 1
        notify_deck_change()
        _begin_reactive_move(inactive, track, announced, direction, "replaying", sweeper_only)
        return

    # Cue (2026-08-12): resolved fresh right here, at the moment "next" is
    # actually committed to -- not when the cue was set, and not inside
    # _pick_next_track()/_start_preloading_next_track() -- so it's correct
    # no matter what got speculatively preloaded in the meantime (a preload
    # kicked off before the cue existed, mid-decode when the cue landed,
    # whatever). Wins over BOTH history-forward-replay and a matching/
    # mismatched preload: the operator explicitly picked this song, so it
    # always plays next, full stop. A stale cue (file deleted since) quietly
    # falls through to the normal pick below instead of erroring.
    if _cued_track_path is not None:
        cued_path, _cued_track_path = _cued_track_path, None
        cued_track = next((t for t in music_library.all_tracks() if t["path"] == cued_path), None)
        if cued_track is not None:
            if _preloaded_track is not None and _preloaded_track["track"]["path"] == cued_path:
                # The lucky case -- whatever had already preloaded happens
                # to BE the cued track (e.g. cued right after a transition,
                # before the random preload picked anything else).
                preload, _preloaded_track = _preloaded_track, None
                track, sound = preload["track"], preload["sound"]
                _history.append(track)
                _history_index = len(_history) - 1
                state.deck_change_count += 1
                notify_deck_change()
                _pending = {
                    "phase": "ready", "target_deck": inactive, "track": track,
                    "sound": sound, "busy_until": None, "announced": announced,
                    "sweeper_only": sweeper_only,
                }
                print(f"[DECK ORCHESTRATOR] NEXT -> cued '{track['title']}' (already preloaded).")
                return
            # Preload (if any) is for a different track -- discard it (it's
            # simply not going to be used this transition) and fall back to
            # the same reactive background-load path a rapid double-press
            # already uses, just for the cued track specifically instead of
            # a random pick.
            _preloaded_track = None
            _history.append(cued_track)
            _history_index = len(_history) - 1
            state.deck_change_count += 1
            notify_deck_change()
            _begin_reactive_move(inactive, cued_track, announced, direction, "cued", sweeper_only)
            return
        print(f"[DECK ORCHESTRATOR] Cued track no longer in the library -- "
              f"falling back to a normal pick.")

    # "next": step forward through any history first (so back-then-next
    # returns you to where you were) before picking something new.
    if _history_index < len(_history) - 1:
        _history_index += 1
        track = _history[_history_index]
        state.deck_change_count += 1
        notify_deck_change()
        _begin_reactive_move(inactive, track, announced, direction, "replaying", sweeper_only)
        return

    if _preloaded_track is not None:
        preload, _preloaded_track = _preloaded_track, None
        track, sound = preload["track"], preload["sound"]
        _history.append(track)
        _history_index = len(_history) - 1

        state.deck_change_count += 1
        notify_deck_change()

        _pending = {
            "phase": "ready",
            "target_deck": inactive,
            "track": track,
            "sound": sound,
            "busy_until": None,
            "announced": announced,
            "sweeper_only": sweeper_only,
        }
        print(f"[DECK ORCHESTRATOR] {direction.upper()} -> using preloaded '{track['title']}' "
              f"onto Deck {inactive} (already decoded, no load-time hitch).")
        # Next-track preload is kicked off from update()'s "ready" phase below,
        # not here -- see module docstring re: SDL_mixer channel-call contention.
        return

    # Fallback: nothing preloaded yet (e.g. two manual presses faster than
    # preload can keep up). Same reactive background-load path as before --
    # rare in practice since a preload starts the instant the previous one
    # is consumed, but this keeps a move from just being dropped.
    track = _pick_next_track()
    if track is None:
        print("[DECK ORCHESTRATOR] No tracks in the music library. Nothing to play.")
        return
    _history.append(track)
    _history_index = len(_history) - 1

    state.deck_change_count += 1
    notify_deck_change()
    _begin_reactive_move(inactive, track, announced, direction, "no preload ready, loading", sweeper_only)


def trigger_track_move(direction):
    """Manual gamepad/web "next"/"back". Announced (sweeper+VO, same
    choreography as Auto-DJ's own transitions) when state.auto_announce_enabled
    is on, otherwise a plain crossfade -- one setting governs every
    transition, manual or automatic (2026-08-08)."""
    _begin_move(direction, announced=state.auto_announce_enabled)


def trigger_announced_track_move():
    """Sweeper + VO + duck/swell choreographed transition -- Auto-DJ's
    overlapping station-announcement handoff (drivers/auto_dj_engine.py)."""
    _begin_move("next", announced=True)


def trigger_sweeper_only_track_move():
    """Sweeper "whoosh" transition with NO spoken announcement, regardless
    of state.auto_announce_enabled -- used for the Trivia Night show
    flow's first-song handoff (drivers/show_engine.py::_finish_intro()),
    where the sweeper's attention-grabbing transition is still wanted but
    a spoken announcement would step on the live host's own intro. The LED
    banner for this specific transition is forced to "GET READY" (see
    state.announcement_banner_text_override) since there's no announcement
    file to caption it from. Every later transition that night goes back
    to the normal trigger_track_move()/Auto-DJ behavior, unaffected by
    this."""
    _begin_move("next", announced=False, sweeper_only=True)


def update(now):
    """Per-frame pump -- called once per frame from
    inputs/gamepad.py::process_events(), same as under the old MIDI version."""
    global _pending
    if _pending is None:
        return

    if _pending["phase"] == "loading":
        return  # background decode thread still working

    if _pending["phase"] == "ready":
        target_deck = _pending["target_deck"]
        outgoing_deck = state.active_deck
        track = _pending["track"]
        outgoing_name, incoming_name = _DECK_NAMES[outgoing_deck], _DECK_NAMES[target_deck]

        dj_engine.play_deck(incoming_name, _pending["sound"], volume=0.0)

        fade_duration = CROSSFADE_SECONDS
        played_announcement = False
        played_sweeper_only = False
        # Reset every transition (2026-08-11) -- only set to a real future
        # timestamp in the announced/sweeper-only branches below. A plain
        # crossfade has no announcement to wait for, so the mystery teaser
        # should arm immediately for it, same as always.
        state.mystery_defer_until = 0.0
        if _pending.get("sweeper_only"):
            # Trivia Night show flow's first-song handoff (2026-08-14):
            # sweeper "whoosh" plays, but deliberately no spoken
            # announcement -- see trigger_sweeper_only_track_move(). Timed
            # off the sweeper's own length rather than an announcement's,
            # since there isn't one here.
            sweeper_path = _pick_sweeper()
            if sweeper_path:
                sweeper_sound = _load_cached(sweeper_path)
                fade_duration = max(CROSSFADE_SECONDS, sweeper_sound.get_length())
                dj_engine.play_sweeper_only(
                    sweeper_sound, outgoing_deck=outgoing_name, incoming_deck=incoming_name,
                    crossfade_seconds=fade_duration,
                )
                played_sweeper_only = True
                state.mystery_defer_until = now + fade_duration
            else:
                print("[DECK ORCHESTRATOR] Sweeper-only move requested but no sweeper files "
                      "available -- falling back to a plain crossfade.")
                dj_engine.crossfade_decks(outgoing_name, incoming_name, CROSSFADE_SECONDS)
        elif _pending["announced"]:
            # _load_cached() below is guaranteed to hit cache -- _warm_sound_cache()
            # decoded every sweeper/announcement file at import time -- so this
            # never blocks, unlike before (2026-08-07 fix, see module docstring).
            sweeper_path, announcement_path = _pick_sweeper(), _pick_announcement()
            if sweeper_path and announcement_path:
                sweeper_sound = _load_cached(sweeper_path)
                announcement_sound = _load_cached(announcement_path)
                dj_engine.play_sweeper_and_announcement(
                    sweeper_sound, announcement_sound,
                    outgoing_deck=outgoing_name, incoming_deck=incoming_name,
                )
                fade_duration = SWEEPER_OVERLAP_SECONDS + announcement_sound.get_length()
                played_announcement = True
                # Per-file caption table (drivers/announcement_engine.py,
                # 2026-08-10 redesign): the banner shows whichever caption
                # is on file for THIS specific announcement clip, not one
                # global value -- record which file just played so the
                # banner render and the admin "Last Announcement Text"
                # field both know which row to read/write.
                state.last_announcement_filename = os.path.basename(announcement_path)
                # Mystery-teaser defer point (2026-08-11, re-attempt after
                # reverting a broader version of this same idea): exactly
                # when the announcement VO itself is expected to finish
                # (sweeper overlap + VO length -- the same fade_duration
                # value, not the longer full crossfade/swell-to-100%
                # completion that overshot last time). drivers/
                # mystery_band_engine.py waits for this specific point
                # before arming the "Who is this?" teaser / starting the
                # client answer clock.
                state.mystery_defer_until = now + fade_duration
            else:
                print("[DECK ORCHESTRATOR] Announced move requested but no sweeper/announcement "
                      "files available -- falling back to a plain crossfade.")
                dj_engine.crossfade_decks(outgoing_name, incoming_name, CROSSFADE_SECONDS)
        else:
            dj_engine.crossfade_decks(outgoing_name, incoming_name, CROSSFADE_SECONDS)

        # The DMX blackout stage holds for the whole VO/sweeper on an
        # announced or sweeper-only move (fade_duration set in whichever
        # branch above ran, same value the mystery-teaser defer point
        # uses), so the twinkles fade up as it ends rather than over the
        # top of it. A plain crossfade passes None and gets the standard
        # short blackout.
        lighting_engine.trigger_song_transition(
            fade_duration if (played_announcement or played_sweeper_only) else None
        )

        # Lazy import: graphics.matrix_canvas imports get_now_playing from
        # this module at its own module scope, so importing it back at THIS
        # module's top level would be circular.
        from graphics import matrix_canvas
        wipe_hold = fade_duration + 2.0
        matrix_canvas.trigger_board_wipe(wipe_hold)

        # Announcement tag-phrase banner (top 2 panels): fills the dark
        # board-wipe space immediately, starting the instant the transition
        # begins (2026-08-10 fix -- previously waited until AFTER the wipe
        # finished, so it never actually appeared "in" the dark space the
        # operator wanted it filling), only when a real announcement VO
        # actually played this transition (not a plain crossfade). Held for
        # at least the wipe's own duration so it doesn't vanish before the
        # screen even comes back. Sweeper-only transitions get the same
        # banner treatment but with fixed "GET READY" text (see
        # state.announcement_banner_text_override) since there's no
        # announcement file here to caption it from.
        if played_announcement:
            state.announcement_banner_text_override = ""  # clear any stale sweeper-only override
            state.announcement_banner_from = now
            state.announcement_banner_until = now + max(config.ANNOUNCEMENT_BANNER_HOLD_SECONDS, wipe_hold)
        elif played_sweeper_only:
            state.announcement_banner_text_override = "GET READY"
            state.announcement_banner_from = now
            state.announcement_banner_until = now + max(config.ANNOUNCEMENT_BANNER_HOLD_SECONDS, wipe_hold)

        deck_track = (track["title"], track["artist"])
        if target_deck == 1:
            state.deck1_track, state.deck1_confident, state.deck1_track_source = deck_track, True, "native"
        else:
            state.deck2_track, state.deck2_confident, state.deck2_track_source = deck_track, True, "native"

        state.active_deck = target_deck
        state.now_playing_duration = track["duration"]
        # DMX pace follows the track's own stored tempo (ID3 BPM tag) when it
        # has one. Otherwise it resets to config.TEMPO_DEFAULT_BPM (120)
        # rather than carrying the previous song's tempo forward
        # (changed 2026-08-11) -- inheriting an unrelated track's BPM looked
        # like a bug on the floor, and 120 is a neutral starting point.
        #
        # This is only the floor, not the last word: a saved per-track tempo
        # lands a frame later via light_prefs_engine.apply_prefs_for(), and
        # an online BPM lookup can still overwrite it asynchronously
        # (drivers/factoid_engine.py) as long as the operator hasn't tapped
        # a tempo in the meantime -- which is what tempo_operator_set, reset
        # here for the incoming track, tracks.
        state.tempo_operator_set = False
        if track.get("bpm"):
            state.dj_tempo_period = 60.0 / track["bpm"]
        else:
            state.dj_tempo_period = config.TEMPO_DEFAULT_PERIOD_SECONDS
        # Deliberately NOT setting state.factoid_track_key here (removed
        # 2026-08-09) -- drivers/factoid_engine.py::ensure_prefetch() is the
        # sole owner of that field and derives it itself from state.deckN_track
        # (already set above) one frame later. Setting it here too raced with
        # that: ensure_prefetch's "if key != state.factoid_track_key: reset
        # state.track_question_queue to this track's own cached questions"
        # check would see the key as already matching (since this line beat
        # it to the write) and skip the reset -- so the quiz kept serving
        # whatever was left in the queue from the PREVIOUS track instead of
        # the new one. That's why questions looked unrelated to the song
        # actually playing. ensure_prefetch runs one frame after this either
        # way (~25ms at 40fps) -- not setting it here costs nothing.

        _pending["busy_until"] = now + fade_duration
        _pending["phase"] = "fading"
        print(f"[DECK ORCHESTRATOR] Transitioning to Deck {target_deck}: '{track['title']}'")
        return

    if _pending["phase"] == "fading" and now >= _pending["busy_until"]:
        print(f"[DECK ORCHESTRATOR] Transition complete -- Active Deck: {state.active_deck}")
        _pending = None
        # Deliberately only now, not right when the transition fires (moved
        # here 2026-08-07): a background pygame.mixer.Sound() decode of a
        # full track is real, sustained CPU/C-call work (~0.6s+ measured on
        # an 8-minute track) -- even on its own thread, starting it anywhere
        # near the transition's own fades risked stealing enough scheduling
        # time from the ramp-scheduler thread to audibly stall a fade in
        # progress. Waiting until the fade is fully done removes that
        # overlap entirely, at the cost of a slightly smaller preload
        # runway (still several seconds before the next likely trigger).
        _start_preloading_next_track()


def has_pending_move():
    return _pending is not None


def get_now_playing():
    """(title, artist) for the deck currently up on the crossfader -- drop-in
    replacement for the old drivers.rekordbox_driver.get_rekordbox_track()."""
    return state.deck1_track if state.active_deck == 1 else state.deck2_track
