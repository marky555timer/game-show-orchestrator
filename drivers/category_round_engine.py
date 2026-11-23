"""drivers/category_round_engine.py
Category rounds: for the next few songs everything played comes from one
category -- a decade ("80s"), a genre ("Rock"), both ("80s Rock") or the
never-charted deep cuts -- and the phones show the category as a banner above
each question, "name that tune" style.

How it hooks in:
  * deck_orchestrator._apply_show_filters() calls filter_tracks() so the next
    song picked comes from the category (it falls back to the whole pool if the
    category has run dry, same as the Setup page's filters do).
  * update(now) is pumped from show_engine.update(): it counts song changes,
    stops filtering once the last category song has started, and ends the round
    when the song after it begins. In auto mode it starts a new round after a
    few ordinary songs.
  * web/remote_server.py's player_question() puts public_view() on the wire.
"""
import random
import time

import config
from state import state

AUTO_GAP_SONGS = 3        # ordinary songs between auto category rounds
_last_transition = None
_songs_since_round = 0

_DECADE_SHORT = {"1950s": "50s", "1960s": "60s", "1970s": "70s", "1980s": "80s",
                 "1990s": "90s", "2000s": "2000s", "2010s": "2010s", "2020s": "2020s"}


# ------------------------------------------------------------- the catalogue
def _meta_for(track):
    from drivers.music_library import sanitize_track_key
    from drivers.music_metadata_engine import get_metadata_for
    return get_metadata_for(sanitize_track_key(track["title"], track["artist"])) or {}


def _label(decade, genre, deep_cuts):
    if deep_cuts:
        return "Deep Cuts"
    parts = []
    if decade:
        parts.append(_DECADE_SHORT.get(decade, decade))
    if genre:
        parts.append(genre)
    elif decade:
        parts.append("Hits")
    return " ".join(parts)


def _key(decade, genre, deep_cuts):
    return "deep" if deep_cuts else f"{decade}|{genre}"


def _matches(meta, decade, genre, deep_cuts):
    if deep_cuts:
        return bool(meta.get("never_charted"))
    if decade and meta.get("decade") != decade:
        return False
    if genre and meta.get("genre") != genre:
        return False
    return bool(decade or genre)


def available_categories():
    """[{key, label, decade, genre, deep_cuts, count}] with at least
    CATEGORY_MIN_LIBRARY_SONGS songs, most songs first."""
    from drivers.music_library import music_library
    tracks = music_library.all_tracks()
    counts = {}

    def bump(decade, genre, deep):
        k = _key(decade, genre, deep)
        entry = counts.setdefault(k, {"key": k, "label": _label(decade, genre, deep), "decade": decade,
                                      "genre": genre, "deep_cuts": deep, "count": 0})
        entry["count"] += 1

    for t in tracks:
        meta = _meta_for(t)
        decade, genre = meta.get("decade") or "", meta.get("genre") or ""
        if decade:
            bump(decade, "", False)
        if genre:
            bump("", genre, False)
        if decade and genre:
            bump(decade, genre, False)
        if meta.get("never_charted"):
            bump("", "", True)
    out = [c for c in counts.values() if c["count"] >= config.CATEGORY_MIN_LIBRARY_SONGS]
    out.sort(key=lambda c: (-c["count"], c["label"]))
    return out


# --------------------------------------------------------------- the round
def active():
    return state.category_round is not None


def filtering():
    r = state.category_round
    return bool(r) and r["started"] < r["songs_total"]


def start(key, songs=None):
    """Begin a category round. Returns (ok, message)."""
    global _last_transition
    from drivers import show_runner
    if show_runner.mode() == "play":
        return False, "This show plays a fixed playlist, so there are no picks to theme."
    cat = next((c for c in available_categories() if c["key"] == key), None)
    if cat is None:
        return False, "That category has too few songs in the library."
    n = max(1, min(12, int(songs or config.CATEGORY_ROUND_SONGS)))
    state.category_round = {
        "key": cat["key"], "label": cat["label"], "decade": cat["decade"], "genre": cat["genre"],
        "deep_cuts": cat["deep_cuts"], "songs_total": n, "started": 0, "number": 0,
    }
    _last_transition = state.transition_count
    _drop_preload()
    print(f"[CATEGORY] '{cat['label']}' for {n} song(s).")
    return True, f"{cat['label']} for the next {n} songs."


def stop(reason="stopped"):
    global _songs_since_round
    if state.category_round:
        print(f"[CATEGORY] '{state.category_round['label']}' {reason}.")
    state.category_round = None
    _songs_since_round = 0
    _drop_preload()


def _drop_preload():
    try:
        from drivers import deck_orchestrator
        deck_orchestrator.drop_preload()
    except Exception as e:
        print(f"[CATEGORY] preload reset skipped: {e}")


def filter_tracks(candidates):
    """deck_orchestrator._apply_show_filters(): only the category's songs while
    the round is still handing out songs; everything otherwise."""
    if not filtering():
        return candidates
    r = state.category_round
    out = [t for t in candidates if _matches(_meta_for(t), r["decade"], r["genre"], r["deep_cuts"])]
    return out or candidates


def public_view():
    """What phones need: the label and which song of the round is playing."""
    r = state.category_round
    if not r or r["number"] <= 0:
        return None
    return {"label": r["label"], "song": min(r["number"], r["songs_total"]), "songs": r["songs_total"]}


# ------------------------------------------------------------------ frame
def update(now):
    """Pumped every frame from show_engine.update()."""
    global _last_transition, _songs_since_round
    if _last_transition is None:
        _last_transition = state.transition_count
    if state.transition_count == _last_transition:
        return
    _last_transition = state.transition_count
    if state.show_phase != "live" or state.medley_active:
        return
    r = state.category_round
    if r:
        if r["number"] >= r["songs_total"]:
            stop("finished")                       # the song after the last one has begun
        else:
            r["number"] += 1
            r["started"] += 1
            print(f"[CATEGORY] '{r['label']}' song {r['number']} of {r['songs_total']}.")
        return
    _songs_since_round += 1
    if state.category_auto and _songs_since_round >= AUTO_GAP_SONGS and not state.intermission_active:
        cats = [c for c in available_categories()
                if c["count"] >= config.CATEGORY_ROUND_SONGS + 2]
        if cats:
            # Favour the big, familiar categories without always picking the same one.
            pick = random.choice(cats[:max(3, len(cats) // 2)])
            start(pick["key"])
        _songs_since_round = 0
