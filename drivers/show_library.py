"""drivers/show_library.py
Saved shows (2026-10-17, docs/panel_show_maker_design.md). One format for
everything: a saved show is a name, a few settings and an ordered playlist.

  {"id": "3f9a0c1e7b2d", "name": "RedLime", "created_at": ..., "updated_at": ...,
   "updated_ts": 1760750000.0, "last_played": ...,
   "intro": true, "questions": false, "low": "1950s", "high": "1980s",
   "settings": {"win_score": 15, ...},
   "items": [{"type": "spin"},
             {"type": "song", "file": "06 Venus.mp3", "title": "Venus",
              "artist": "Bananarama", "ending": "quiz"},          # full | quiz | cut
             {"type": "song", "file": ..., "ending": "cut", "at": 94.3}]}

A spin is rigged to land on the decade of the song right after it. A show
with no songs yet is filled in as it plays (drivers/show_runner.py records
it); a show with songs plays them in order and stops after the last one.

Written by the panel menu, the show runner (main thread) and the Show
Designer page (web threads), so every access goes through _lock.
shows.json lives in the repo root; the old show_replay.json is imported once.

Sync with the web Show Designer (triviacade.com/play/designer, drivers/
web_sync.py): every show has a permanent "id" and an "updated_ts" (epoch
seconds, bumped on every edit); deleted ids are kept in "deleted" {id: ts}.
merge() applies the other side's copy: newest edit wins, a delete newer than
the last edit wins, last_played keeps the later of the two.
"""
import copy
import json
import os
import random
import re
import threading
import time
import uuid

import config

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FILE = os.path.join(_BASE, "shows.json")
_OLD_REPLAY_FILE = os.path.join(_BASE, "show_replay.json")

NAME_MAX = 10
ENDINGS = ("full", "quiz", "cut")
# Ascending, for the designer's LOW / HIGH decade pages and the web page.
DECADES = ["1950s", "1960s", "1970s", "1980s", "1990s", "2000s", "2010s", "2020s"]

DEFAULTS = {"intro": True, "questions": False, "low": "1950s", "high": "1980s"}
_SETTING_KEYS = ("win_score", "intermission_minutes", "exclude_explicit",
                 "exclude_slow_dance", "exclude_never_charted", "allowed_genres")

_ADJECTIVES = ["Red", "Blue", "Gold", "Jade", "Ruby", "Pink", "Teal", "Bold", "Cool",
               "Wild", "Neon", "Lazy", "Hot", "Funky", "Lucky", "Sunny", "Happy",
               "Big", "Tiny", "Swift", "Velvet", "Silver", "Cosmic", "Groovy", "Retro",
               "Disco", "Mellow", "Zesty", "Shiny", "Jolly"]
_NOUNS = ["Lime", "Fox", "Owl", "Moon", "Star", "Wave", "Bee", "Cat", "Jet", "Yak",
          "Fig", "Kiwi", "Lynx", "Mint", "Puma", "Ram", "Rose", "Sky", "Sun", "Tiger",
          "Comet", "Pearl", "Robin", "Mango", "Otter", "Panda", "Disc", "Drum", "Note", "Echo"]

_lock = threading.RLock()
_shows = None   # list of show dicts, loaded lazily
_deleted = {}   # id -> epoch seconds it was deleted (sync tombstones)
_TOMBSTONE_DAYS = 120
_listeners = []  # called (no args) after any local change -- drivers/web_sync.py


def decade_label(decade):
    """"1980s" -> "80s"; 2000s and later keep all four digits ("2000s")."""
    if decade and decade[:2] == "19" and len(decade) == 5:
        return decade[2:]
    return decade or ""


def decades_between(low, high):
    try:
        a, b = DECADES.index(low), DECADES.index(high)
    except ValueError:
        return list(DECADES)
    if a > b:
        a, b = b, a
    return DECADES[a:b + 1]


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _new_id():
    return uuid.uuid4().hex[:12]


def _touch(s):
    """An edit: the newest edit wins when syncing."""
    s["updated_at"] = _now()
    s["updated_ts"] = round(time.time(), 3)


def on_change(fn):
    _listeners.append(fn)


# ------------------------------------------------------------------ disk
def _load():
    global _shows, _deleted
    if _shows is not None:
        return
    _shows = []
    try:
        with open(_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _shows = [s for s in data.get("shows", []) if s.get("name")]
        _deleted = {k: float(v) for k, v in (data.get("deleted") or {}).items()}
        if _backfill_ids():
            _save(notify=False)
        return
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[SHOWS] Could not read {_FILE}: {e}")
        return
    _import_old_replay()


def _backfill_ids():
    """Shows saved before sync existed get an id and an edit time."""
    changed = False
    for s in _shows:
        if not s.get("id"):
            s["id"] = _new_id()
            changed = True
        if not s.get("updated_ts"):
            try:
                s["updated_ts"] = time.mktime(time.strptime(s.get("updated_at") or "", "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                s["updated_ts"] = round(time.time(), 3)
            changed = True
    return changed


def _save(notify=True):
    cutoff = time.time() - _TOMBSTONE_DAYS * 86400
    for k in [k for k, ts in _deleted.items() if ts < cutoff]:
        del _deleted[k]
    tmp = _FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"shows": _shows, "deleted": _deleted}, f, indent=1)
        os.replace(tmp, _FILE)
    except Exception as e:
        print(f"[SHOWS] Could not save {_FILE}: {e}")
    if notify:
        for fn in _listeners:
            try:
                fn()
            except Exception as e:
                print(f"[SHOWS] change listener failed: {e}")


def _import_old_replay():
    """One-time: the 2026-10-16 show_replay.json becomes the first saved show."""
    try:
        with open(_OLD_REPLAY_FILE, "r", encoding="utf-8") as f:
            old = json.load(f)
    except Exception:
        return
    songs = old.get("songs") or []
    if not songs:
        return
    items = [{"type": "spin"}] if old.get("wheel") else []
    for s in songs:
        ending = s.get("ending", "full")
        item = {"type": "song", "file": os.path.basename(s.get("path", "")),
                "title": s.get("title", ""), "artist": s.get("artist", "")}
        if ending in ("manual", "stop"):
            item["ending"], item["at"] = "cut", s.get("at", 0.0)
        else:
            item["ending"] = ending if ending in ENDINGS else "full"
        items.append(item)
    st = old.get("settings") or {}
    show = _blank(_unique_name())
    show.update({
        "intro": not st.get("show_skip_intro", False),
        "questions": not st.get("show_suppress_questions", True),
        "items": items,
        "created_at": (old.get("recorded_at") or _now()),
        "last_played": (old.get("recorded_at") or ""),
    })
    _shows.append(show)
    _save()
    print(f"[SHOWS] Imported the last recorded show as '{show['name']}' ({len(songs)} songs).")


def _blank(name):
    now = _now()
    return {"id": _new_id(), "name": name, "created_at": now, "updated_at": now,
            "updated_ts": round(time.time(), 3), "last_played": "",
            "intro": DEFAULTS["intro"], "questions": DEFAULTS["questions"],
            "low": DEFAULTS["low"], "high": DEFAULTS["high"],
            "settings": current_settings(), "items": []}


def current_settings():
    from state import state
    return {
        "win_score": state.game_win_score,
        "intermission_minutes": state.intermission_minutes,
        "exclude_explicit": state.show_exclude_explicit,
        "exclude_slow_dance": state.show_exclude_slow_dance,
        "exclude_never_charted": state.show_exclude_never_charted,
        "allowed_genres": list(state.show_allowed_genres),
    }


def apply_settings(show):
    """Loads a show's game settings into state (drivers/show_engine.py)."""
    from state import state
    st = show.get("settings") or {}
    state.game_win_score = max(1, int(st.get("win_score", state.game_win_score)))
    state.intermission_minutes = max(1, int(st.get("intermission_minutes", state.intermission_minutes)))
    state.show_exclude_explicit = bool(st.get("exclude_explicit", False))
    state.show_exclude_slow_dance = bool(st.get("exclude_slow_dance", False))
    state.show_exclude_never_charted = bool(st.get("exclude_never_charted", False))
    state.show_allowed_genres = [g for g in st.get("allowed_genres", []) if g in config.MUSIC_GENRES]
    state.show_allowed_decades = decades_between(show.get("low"), show.get("high"))
    state.show_require_decade = True


# ----------------------------------------------------------------- names
def valid_name(name):
    return bool(re.fullmatch(r"[A-Za-z0-9 ]{1,%d}" % NAME_MAX, name or "")) and name.strip() == name


def _taken(name):
    return any(s["name"].lower() == name.lower() for s in _shows)


def _unique_name():
    for _ in range(500):
        name = random.choice(_ADJECTIVES) + random.choice(_NOUNS)
        if len(name) <= NAME_MAX and not _taken(name):
            return name
    n = 2
    while _taken(f"Show{n}"):
        n += 1
    return f"Show{n}"


# ---------------------------------------------------------------- access
def _find(name):
    for s in _shows:
        if s["name"] == name:
            return s
    return None


def all_shows(order="played"):
    """Copies, newest first. order="played": most recently played first (panel
    Load Show list); order="created": newest created first (web page)."""
    with _lock:
        _load()
        shows = copy.deepcopy(_shows)
    if order == "played":
        shows.sort(key=lambda s: (s.get("last_played") or "", s.get("created_at") or ""), reverse=True)
    else:
        shows.sort(key=lambda s: s.get("created_at") or "", reverse=True)
    return shows


def get(name):
    with _lock:
        _load()
        s = _find(name)
        return copy.deepcopy(s) if s else None


def last_played():
    shows = all_shows("played")
    return shows[0] if shows else None


def create(intro=None, questions=None, low=None, high=None, items=None):
    with _lock:
        _load()
        show = _blank(_unique_name())
        if intro is not None:
            show["intro"] = bool(intro)
        if questions is not None:
            show["questions"] = bool(questions)
        if low in DECADES:
            show["low"] = low
        if high in DECADES:
            show["high"] = high
        if DECADES.index(show["low"]) > DECADES.index(show["high"]):
            show["low"], show["high"] = show["high"], show["low"]
        if items is not None:
            show["items"] = _clean_items(items)
        _shows.append(show)
        _save()
        print(f"[SHOWS] New show '{show['name']}'.")
        return copy.deepcopy(show)


def _clean_items(items):
    out = []
    for it in items or []:
        kind = it.get("type")
        if kind == "spin":
            out.append({"type": "spin"})
        elif kind == "song" and it.get("file"):
            ending = it.get("ending") if it.get("ending") in ENDINGS else "full"
            item = {"type": "song", "file": os.path.basename(str(it["file"])),
                    "title": str(it.get("title", "")), "artist": str(it.get("artist", "")),
                    "ending": ending}
            if ending == "cut":
                try:
                    item["at"] = max(1.0, round(float(it.get("at", 60.0)), 1))
                except (TypeError, ValueError):
                    item["at"] = 60.0
            out.append(item)
    return out


def update(name, fields):
    """Edits a show in place (keeps its name). Returns the show or None."""
    with _lock:
        _load()
        s = _find(name)
        if s is None:
            return None
        if "intro" in fields:
            s["intro"] = bool(fields["intro"])
        if "questions" in fields:
            s["questions"] = bool(fields["questions"])
        for key in ("low", "high"):
            if fields.get(key) in DECADES:
                s[key] = fields[key]
        if DECADES.index(s["low"]) > DECADES.index(s["high"]):
            s["low"], s["high"] = s["high"], s["low"]
        if "items" in fields:
            s["items"] = _clean_items(fields["items"])
        if isinstance(fields.get("settings"), dict):
            st = s.setdefault("settings", {})
            for key in _SETTING_KEYS:
                if key in fields["settings"]:
                    st[key] = fields["settings"][key]
        _touch(s)
        _save()
        return copy.deepcopy(s)


def append_item(name, item):
    """Show runner recording: adds one playlist item."""
    with _lock:
        _load()
        s = _find(name)
        if s is None:
            return
        s["items"].extend(_clean_items([item]))
        _touch(s)
        _save()


def set_last_song_ending(name, ending, at=None):
    with _lock:
        _load()
        s = _find(name)
        if s is None:
            return
        songs = [it for it in s["items"] if it["type"] == "song"]
        if not songs:
            return
        last = songs[-1]
        last["ending"] = ending if ending in ENDINGS else "full"
        last.pop("at", None)
        if last["ending"] == "cut":
            last["at"] = max(1.0, round(float(at or 0.0), 1))
        _touch(s)
        _save()


def rename(old, new):
    new = (new or "").strip()
    with _lock:
        _load()
        s = _find(old)
        if s is None:
            return "Show not found."
        if not valid_name(new):
            return f"Names are 1-{NAME_MAX} letters or numbers."
        if new.lower() != old.lower() and _taken(new):
            return "That name is taken."
        s["name"] = new
        _touch(s)
        _save()
    from state import state
    if state.show_selected == old:
        state.show_selected = new
    return None


def delete(name):
    with _lock:
        _load()
        s = _find(name)
        if s is None:
            return False
        _shows.remove(s)
        _deleted[s["id"]] = round(time.time(), 3)
        _save()
        return True


def duplicate(name):
    with _lock:
        _load()
        s = _find(name)
        if s is None:
            return None
        copy_ = copy.deepcopy(s)
        now = _now()
        copy_.update({"id": _new_id(), "name": _unique_name(), "created_at": now, "updated_at": now,
                      "updated_ts": round(time.time(), 3), "last_played": ""})
        _shows.append(copy_)
        _save()
        return copy.deepcopy(copy_)


def touch_played(name):
    with _lock:
        _load()
        s = _find(name)
        if s is None:
            return
        s["last_played"] = _now()
        _save()


def song_items(show):
    return [it for it in (show or {}).get("items", []) if it.get("type") == "song"]


# ------------------------------------------------------------------ sync
def export():
    """Everything the web side needs: {"shows": [...], "deleted": {id: ts}}."""
    with _lock:
        _load()
        return {"shows": copy.deepcopy(_shows), "deleted": dict(_deleted)}


def merge(remote_shows, remote_deleted):
    """Applies the other side's shows (drivers/web_sync.py). Newest edit wins
    per id; a delete wins when it is newer than the last edit; last_played
    keeps the later one. Two different shows that end up with the same name
    (made on both sides) -- the newer-created one gets a fresh name.
    Returns the number of changes made here."""
    changed = 0
    with _lock:
        _load()
        for k, ts in (remote_deleted or {}).items():
            try:
                ts = float(ts)
            except (TypeError, ValueError):
                continue
            if ts > _deleted.get(k, 0.0):
                _deleted[k] = ts
        by_id = {s["id"]: s for s in _shows}
        for r in remote_shows or []:
            rid = r.get("id")
            if not rid or not r.get("name"):
                continue
            mine = by_id.get(rid)
            if mine is None:
                new = _from_remote(r)
                _shows.append(new)
                by_id[rid] = new
                changed += 1
                continue
            played = max(mine.get("last_played") or "", r.get("last_played") or "")
            if float(r.get("updated_ts") or 0) > float(mine.get("updated_ts") or 0):
                mine.clear()
                mine.update(_from_remote(r))
                changed += 1
            if (mine.get("last_played") or "") != played:
                mine["last_played"] = played
                changed += 1
        for s in list(_shows):
            if _deleted.get(s["id"], 0.0) >= float(s.get("updated_ts") or 0):
                _shows.remove(s)
                changed += 1
            else:
                _deleted.pop(s["id"], None)
        seen = set()
        for s in sorted(_shows, key=lambda x: (x.get("created_at") or "", x["id"])):
            if s["name"].lower() in seen:
                s["name"] = _unique_name()
                _touch(s)
                changed += 1
            seen.add(s["name"].lower())
        if changed:
            _save(notify=False)
    return changed


def _from_remote(r):
    s = _blank(r.get("name") if valid_name(r.get("name")) else _unique_name())
    for key in ("id", "created_at", "updated_at", "updated_ts", "last_played"):
        if r.get(key) is not None:
            s[key] = r[key]
    s["intro"] = bool(r.get("intro", s["intro"]))
    s["questions"] = bool(r.get("questions", s["questions"]))
    for key in ("low", "high"):
        if r.get(key) in DECADES:
            s[key] = r[key]
    if isinstance(r.get("settings"), dict):
        s["settings"] = {k: r["settings"][k] for k in _SETTING_KEYS if k in r["settings"]}
    s["items"] = _clean_items(r.get("items"))
    return s
