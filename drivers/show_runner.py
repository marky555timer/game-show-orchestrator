"""drivers/show_runner.py
Runs a saved show (drivers/show_library.py) once drivers/show_engine.py has
started it. Two modes, picked by whether the show has songs yet:

  play    -- the playlist plays in order. Each song ends its saved way:
             "full" (Auto-DJ's normal cue-out), "quiz" (as soon as its
             questions + leaderboard are done) or "cut" (at `at` seconds).
             A spin item runs the wheel before the song after it, rigged to
             that song's decade. When the last song ends the show stops
             (outro). Mid-show deviations (a manual skip, an off-script song)
             are played live and never change the save.
  record  -- a new show (designed on the panel, or the hands-off default):
             songs are picked live from its decade range and appended to the
             save as they play, each with how it ended.

Hooks: deck_orchestrator._begin_move() asks intercept_next() before any
"next" move (that is where spins and the end of the playlist happen),
auto_dj_engine reports why it is moving on (note_ending), and update(now) is
pumped every frame from show_engine.update().
"""
import os
import random
import time

import config
from state import state
from drivers import show_library

_show = None         # the running show (a copy)
_mode = None         # "play" | "record" | None
_items = []          # play: [{"type": "song", "path", "ending", "at", ...} | {"type": "spin"}]
_pos = -1            # play: index into _items of the song playing now
_on_script = False   # the playing song is the scripted one (its ending is enforced)
_cur_path = ""
_pending_ending = ""  # why the move that is about to happen was made
_pending_at = None
_rec_count = 0        # record: songs appended so far
_cut_fired = False


# --------------------------------------------------------------- helpers
def _library_by_file():
    from drivers.music_library import music_library
    return {os.path.basename(t["path"]): t for t in music_library.all_tracks()}


def track_decade(track):
    from drivers.music_library import sanitize_track_key
    from drivers.music_metadata_engine import get_metadata_for
    meta = get_metadata_for(sanitize_track_key(track["title"], track["artist"])) or {}
    return meta.get("decade") or ""


def wheel_can_land(decade):
    from drivers import intro_wheel
    return intro_wheel.can_land(decade)


def _elapsed(now):
    return max(0.0, now - state.now_playing_started_at) if state.now_playing_started_at else 0.0


def running_name():
    return _show["name"] if _show else ""


def mode():
    return _mode


# ----------------------------------------------------------------- start
def begin(show):
    """show_engine.start_show(): this show is about to run."""
    global _show, _mode, _items, _pos, _on_script, _cur_path, _pending_ending, _pending_at
    global _rec_count, _cut_fired
    _show = show
    _pos, _on_script, _cut_fired = -1, False, False
    _cur_path = state.now_playing_path
    _pending_ending, _pending_at = "", None
    _rec_count = 0
    state.show_song_ending = ""
    _items = []
    if show_library.song_items(show):
        _mode = "play"
        lib = _library_by_file()
        for it in show["items"]:
            if it["type"] == "spin":
                _items.append({"type": "spin"})
                continue
            track = lib.get(it["file"])
            if track is None:
                print(f"[SHOW] '{it['file']}' is missing from the library -- skipped.")
                continue
            _items.append(dict(it, path=track["path"], track=track))
        # drop spins with no song after them, and back-to-back spins
        cleaned = []
        for i, it in enumerate(_items):
            if it["type"] == "spin":
                if not any(x["type"] == "song" for x in _items[i + 1:]):
                    continue
                if cleaned and cleaned[-1]["type"] == "spin":
                    continue
            cleaned.append(it)
        _items = cleaned
        print(f"[SHOW] Playing '{show['name']}' ({sum(1 for i in _items if i['type'] == 'song')} songs).")
    else:
        _mode = "record"
        print(f"[SHOW] Recording '{show['name']}' as it plays.")
    show_library.touch_played(show["name"])


def stop():
    """The show is over (outro) or abandoned -- nothing left to enforce."""
    global _show, _mode, _items
    _show, _mode, _items = None, None, []
    state.show_song_ending = ""


def _next_song_index(after):
    for i in range(after + 1, len(_items)):
        if _items[i]["type"] == "song":
            return i
    return None


def _spin_before(index):
    return index is not None and index > 0 and _items[index - 1]["type"] == "spin"


# ----------------------------------------------------------- show opening
def opening():
    """What the show opens with, after the intro theme: (decade, path).
    decade "" = no wheel. path None = let the normal pick choose."""
    if _mode == "play":
        first = _next_song_index(-1)
        if first is None:
            return "", None
        song = _items[first]
        decade = track_decade(song["track"]) if _spin_before(first) else ""
        if decade and not wheel_can_land(decade):
            print(f"[SHOW] Opening spin skipped -- the wheel has no {decade} slot yet.")
            decade = ""
        return decade, song["path"]
    if _mode == "record" and _show and _show.get("intro"):
        decades = [d for d in show_library.decades_between(_show["low"], _show["high"]) if wheel_can_land(d)]
        if not decades:
            return "", None
        for decade in random.sample(decades, len(decades)):
            path = _pick_from_decade(decade)
            if path:
                show_library.append_item(_show["name"], {"type": "spin"})
                return decade, path
    return "", None


def _pick_from_decade(decade):
    from drivers.music_library import music_library
    from drivers import deck_orchestrator
    pool = [t for t in music_library.all_tracks() if track_decade(t) == decade]
    pool = deck_orchestrator._apply_show_filters(pool) or pool
    return random.choice(pool)["path"] if pool else None


# ------------------------------------------------------------ deck hooks
def note_ending(reason):
    """drivers/auto_dj_engine.py, right before it fires a transition."""
    global _pending_ending
    _pending_ending = reason


def note_move():
    """drivers/deck_orchestrator.py::_begin_move() -- a song change is being
    triggered. Stamps how far into the song that happened (the new song only
    lands after the crossfade)."""
    global _pending_at
    if _mode == "record" and _pending_at is None and state.show_phase == "live":
        _pending_at = round(_elapsed(time.time()), 1)


def intercept_next():
    """deck_orchestrator._begin_move(), before a "next" move. Returns
    ("spin", decade, path) to run the wheel first, "stop" to end the show
    instead (the playlist is done), or None to move normally."""
    global _pending_ending
    if _mode != "play" or state.show_phase != "live":
        return None
    automatic = bool(_pending_ending)
    nxt = _next_song_index(_pos)
    if nxt is None:
        # Any "next" off the last scripted song ends the show -- not only an
        # auto-DJ one (when the track wasn't identified, auto-DJ never fires,
        # and the deck used to roll on into random songs).
        if _on_script:
            _pending_ending = ""
            return "stop"
        return None
    if _spin_before(nxt) and _on_script:
        song = _items[nxt]
        decade = track_decade(song["track"])
        if wheel_can_land(decade):
            return ("spin", decade, song["path"])
        print(f"[SHOW] Spin skipped -- the wheel has no {decade or 'undated'} slot yet.")
    return None


# ------------------------------------------------------------ song change
def _close_recorded(default_reason, use_now=False):
    global _pending_ending, _pending_at
    if _mode != "record" or not _rec_count:
        _pending_ending, _pending_at = "", None
        return
    reason = _pending_ending or default_reason
    at = None if use_now else _pending_at
    if at is None:
        at = round(_elapsed(time.time()), 1)
    if reason in ("quiz", "full"):
        show_library.set_last_song_ending(_show["name"], reason)
    else:
        show_library.set_last_song_ending(_show["name"], "cut", at)
    _pending_ending, _pending_at = "", None


def _on_song_change(path):
    global _pos, _on_script, _rec_count, _cut_fired, _pending_ending, _pending_at
    _cut_fired = False
    if _mode == "record":
        _close_recorded("cut")
        from drivers.deck_orchestrator import get_now_playing
        title, artist = get_now_playing()
        show_library.append_item(_show["name"], {
            "type": "song", "file": os.path.basename(path),
            "title": title or os.path.basename(path), "artist": artist or "", "ending": "full"})
        _rec_count += 1
        return
    if _mode != "play":
        return
    _pending_ending, _pending_at = "", None
    nxt = _next_song_index(_pos)
    if nxt is not None and _items[nxt]["path"] == path:
        _pos, _on_script = nxt, True
    else:
        # Later in the playlist first; then anywhere (a BACK onto an earlier
        # scripted song must re-sync, or the last song never ends the show).
        later = next((i for i in list(range(_pos + 1, len(_items))) + list(range(0, max(_pos, 0)))
                      if _items[i]["type"] == "song" and _items[i]["path"] == path), None)
        if later is not None:
            _pos, _on_script = later, True
        else:
            _on_script = False   # off-script: plays live, the script resumes after it
            print(f"[SHOW] Off-script song {os.path.basename(path)!r} -- playing it live.")
    upcoming = _next_song_index(_pos)
    from drivers import deck_orchestrator
    if upcoming is not None:
        deck_orchestrator.set_cued_track(_items[upcoming]["path"])


def ending_rule():
    """The saved ending of the song playing now ("" = live behaviour)."""
    if _mode != "play" or not _on_script or _pos < 0:
        return ""
    return _items[_pos].get("ending", "full")


def note_show_end():
    """drivers/show_engine.py::start_outro() -- the show stopped."""
    if _mode == "record":
        _close_recorded("cut", use_now=True)
        print(f"[SHOW] '{_show['name']}' saved: {_rec_count} song(s).")
    stop()


# ------------------------------------------------------------------ frame
def update(now):
    global _cur_path, _cut_fired, _pending_ending
    if _mode is None or state.show_phase != "live" or state.medley_active or state.show_spin_active:
        return
    path = state.now_playing_path
    if path and path != _cur_path:
        _cur_path = path
        _on_song_change(path)

    rule = ending_rule()
    state.show_song_ending = rule
    if rule != "cut" or _cut_fired:
        return
    at = _items[_pos].get("at", 0.0)
    from drivers import deck_orchestrator
    if _elapsed(now) < at or deck_orchestrator.has_pending_move():
        return
    _cut_fired = True
    _pending_ending = "cut"
    if _next_song_index(_pos) is None:
        from drivers import show_engine
        print(f"[SHOW] Last song cut at {at:.0f}s -- ending the show.")
        show_engine.stop_show()
        return
    from drivers import auto_dj_engine
    print(f"[SHOW] Cutting to the next song at {at:.0f}s.")
    auto_dj_engine.advance_now()
