"""Show-length estimate (2026-11-25): how long a saved show will take, worked out from its playlist and
rounds the same way the Show Designer's round preview does (web/static/designer.html predictPlaylist),
with the self test's per-round numbers (config.SHOW_EST_*, show_library.preview_estimates()).

Stamped onto the show on every save (show_library: show["est"]) so a brand-new show has a number on
the Setup page straight away, before anyone has run a self test on it. A guide, not a promise: the
self test found a small or slow crowd adds about a quarter.

  est = {"by_linger": {"0": min, "20": min, ...}, "shortest": min, "at": unix time}
"""
import math
import os
import time

import config

LINGERS = (0, 20, 40, 60, 80, 100)


def _track_index():
    from drivers.music_library import music_library
    return {os.path.basename(t["path"]): t for t in music_library.all_tracks()}


def _song_seconds(item, tracks):
    t = tracks.get(item.get("file"))
    if not t:
        return 0.0
    try:
        from drivers import auto_dj_engine
        length = auto_dj_engine.planned_length(t["path"], float(t.get("duration") or 0))
    except Exception:
        length = float(t.get("duration") or 0)
    if item.get("ending") == "cut" and item.get("at"):
        length = min(length, float(item["at"]))
    return length


def minutes(show, linger, est=None, tracks=None):
    """Estimated minutes for `show` at `linger` percent."""
    from drivers import show_library
    from state import state
    E = est or show_library.preview_estimates()
    tracks = tracks if tracks is not None else _track_index()
    lg = max(0.0, min(100.0, float(linger))) / 100.0
    lens = [_song_seconds(it, tracks) for it in show.get("items") or [] if it.get("type") == "song"]
    lens = [x for x in lens if x > 0]
    settings = show.get("settings") or {}
    total = (E["intro_min"] * 60 if show.get("intro") else 0.0) + \
            (E["outro_min"] * 60 if show.get("outro", True) else 0.0)
    q_s = E["trivia_quiz_s"]

    def trivia_song(L, item_ending=None):
        q = min(L, q_s)
        return q + (L - q) * lg

    rounds = show.get("rounds") or []
    if not rounds:
        # A plain playlist: every song, a quiz under each when the show asks questions.
        for L in lens:
            total += trivia_song(L) if show.get("questions") else L
        return total / 60.0

    p = 0
    carry = 0.0
    goal_default = int(settings.get("win_score") or 0) or int(state.game_win_score or 1000)
    int_min = float(settings.get("intermission_minutes") or 0) or float(E["intermission_min"])

    def take():
        nonlocal p
        if p < len(lens):
            p += 1
            return lens[p - 1]
        return None

    for ri, r in enumerate(rounds):
        kind = r.get("type") or "trivia"
        if kind == "trivia":
            want = math.ceil((r.get("goal") or goal_default) / max(1, E["trivia_pps"]))
            if r.get("songs"):
                want = min(want, int(r["songs"]))
            carry = 0.0
            for _ in range(want):
                L = take()
                if L is None:
                    L = 230.0          # past the playlist's end: a typical song
                total += trivia_song(L)
        elif kind == "wait_how_much":
            qs = int(r.get("songs") or 0) or config.ROUND_PRICE_QUESTIONS_DEFAULT
            budget = E["whm_s"] * qs / 10.0
            total += budget + 30
            budget -= carry
            carry = 0.0
            while budget > 0:
                L = take()
                if L is None:
                    break
                budget -= L
            carry = max(0.0, -budget)
        elif kind == "fastest_finger":
            total += E["ff_min"] * 60
            carry = 0.0
        elif kind == "cliffhanger":
            total += E["cliff_min"] * 60
            carry = 0.0
            for _ in range(1 + int(E.get("cliff_songs") or 5)):
                take()
        else:
            total += E["simon_min"] * 60
            carry = 0.0
            take()
        if ri == len(rounds) - 1:
            break
        # the intermission after this round fills its timer (its last song may run up to a minute over)
        nxt = rounds[ri + 1].get("type")
        opens_on_intro = nxt in ("wait_how_much", "fastest_finger")
        left = int_min * 60 - carry
        carry = 0.0
        while left > E["early_end_s"]:
            L = take()
            if L is None:
                left = 0
                break
            if opens_on_intro and L > left:
                carry = L - left
                left = 0
                break
            left -= L
        total += int_min * 60 + (min(-left, E["max_overrun_s"]) if left < 0 else 0)
    return total / 60.0


def compute(show):
    from drivers import show_library
    E = show_library.preview_estimates()
    tracks = _track_index()
    by = {str(l): round(minutes(show, l, E, tracks), 1) for l in LINGERS}
    return {"by_linger": by, "shortest": by["0"], "at": time.time()}


def stamp(show):
    """show["est"] = compute(show); never lets an estimate problem break a save."""
    try:
        show["est"] = compute(show)
    except Exception as exc:
        print(f"[SHOWS] Length estimate for '{show.get('name')}' failed: {exc!r}")
    return show
