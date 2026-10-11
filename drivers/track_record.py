"""drivers/track_record.py
Per-track "record" layer over the stores that already exist, plus the two new
ones this feature needs:

  track_clips.json   {file name: {"in", "out", "bpm", "edited_at"}} -- the
                     human-approved medley clip (seconds in the file). A
                     track with a clip is GREEN in the File Manager.
  track_stats.json   {file name: {"last_played", "last_edited"}} -- unix
                     seconds. last_played is stamped when a track starts
                     (normal play or as a medley hook); last_edited whenever
                     tags, light prefs or the clip change.

Everything else (tags in music_metadata.csv, looks in light_prefs.csv, cue-outs
in track_cues*.json, beat analysis in medley_analysis.json) stays where it is;
audit() just compares the library against all of them.
"""
import json
import os
import threading
import time

import config

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIPS_FILE = os.path.join(_BASE, "track_clips.json")
STATS_FILE = os.path.join(_BASE, "track_stats.json")

_lock = threading.RLock()
_clips = {"mtime": 0.0, "data": {}}
_stats = {"data": None, "dirty": False, "last_flush": 0.0}
STATS_FLUSH_SECONDS = 20.0


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


# ---------------------------------------------------------------- clips
def clips():
    """All saved clips (re-read when the file changes on disk)."""
    with _lock:
        try:
            mt = os.path.getmtime(CLIPS_FILE)
        except OSError:
            mt = 0.0
        if mt != _clips["mtime"]:
            _clips["data"] = _read(CLIPS_FILE)
            _clips["mtime"] = mt
        return _clips["data"]


def get_clip(name):
    return clips().get(name)


def save_clip(name, t_in, t_out, bpm=None):
    with _lock:
        data = dict(clips())
        data[name] = {"in": round(float(t_in), 3), "out": round(float(t_out), 3),
                      "bpm": round(float(bpm), 3) if bpm else None, "edited_at": time.time()}
        _write(CLIPS_FILE, data)
        _clips["data"], _clips["mtime"] = data, os.path.getmtime(CLIPS_FILE)
    touch_edited(name)
    return data[name]


def delete_clip(name):
    with _lock:
        data = dict(clips())
        if name not in data:
            return False
        del data[name]
        _write(CLIPS_FILE, data)
        _clips["data"], _clips["mtime"] = data, os.path.getmtime(CLIPS_FILE)
    touch_edited(name)
    return True


# ---------------------------------------------------------------- stats
def _stats_data():
    if _stats["data"] is None:
        _stats["data"] = _read(STATS_FILE)
    return _stats["data"]


def _touch(name, field):
    with _lock:
        _stats_data().setdefault(name, {})[field] = time.time()
        _stats["dirty"] = True
        # stamps from playback are frequent; flush lazily, edits flush at once
        if field == "last_edited" or time.time() - _stats["last_flush"] > STATS_FLUSH_SECONDS:
            flush()


def touch_played(path_or_name):
    _touch(os.path.basename(str(path_or_name)), "last_played")


def touch_edited(path_or_name):
    _touch(os.path.basename(str(path_or_name)), "last_edited")


def flush():
    with _lock:
        if _stats["dirty"] and _stats["data"] is not None:
            try:
                _write(STATS_FILE, _stats["data"])
                _stats["dirty"] = False
            except Exception as e:
                print(f"[TRACK RECORD] stats flush failed: {e}")
        _stats["last_flush"] = time.time()


def stats_for(name):
    with _lock:
        return dict(_stats_data().get(name, {}))


# ---------------------------------------------------------------- audit
def _sig(path):
    st = os.stat(path)
    return f"{st.st_size}:{int(st.st_mtime)}"


def audit(tracks, metadata_for, prefs_for, key_for):
    """Compare every library track against each per-track store.

    tracks        music_library.all_tracks()
    metadata_for  track_key -> tag entry or None
    prefs_for     track_key -> light prefs entry or None
    key_for       (title, artist) -> track_key

    Returns {"total", "categories": {name: {"label", "fixable", "files": [...]}}}
    where `files` are the tracks that still need that item."""
    from drivers import music_metadata_engine as mme
    analysis = _read(os.path.join(_BASE, config.MEDLEY_ANALYSIS_FILE))
    cues = _read(os.path.join(_BASE, config.TRACK_CUES_FILE))
    cat = {
        "tags": {"label": "Tags missing or incomplete (genre, energy, decade...)", "fixable": True, "files": []},
        "medley_analysis": {"label": "Beat analysis missing or out of date", "fixable": True, "files": []},
        "cues": {"label": "Cue-out analysis missing or out of date", "fixable": True, "files": []},
        "lights": {"label": "No saved light design", "fixable": False, "files": []},
        "clips": {"label": "Medley clip not human-approved yet", "fixable": False, "files": []},
    }
    saved_clips = clips()
    for t in tracks:
        name = os.path.basename(t["path"])
        key = key_for(t["title"], t["artist"])
        try:
            sig = _sig(t["path"])
        except OSError:
            continue
        meta = metadata_for(key)
        if meta is None or not mme.is_complete(meta):
            cat["tags"]["files"].append(name)
        a = analysis.get(name)
        if not a or a.get("sig") != sig or ("bpm" not in a and "error" not in a):
            cat["medley_analysis"]["files"].append(name)
        c = cues.get(name)
        if not c or c.get("sig") != sig:
            cat["cues"]["files"].append(name)
        p = prefs_for(key)
        if not p or all(v in (None, -1) for k, v in p.items() if k.endswith(("_index", "_id"))):
            cat["lights"]["files"].append(name)
        if name not in saved_clips:
            cat["clips"]["files"].append(name)
    return {"total": len(tracks), "categories": cat}
