"""drivers/web_sync.py
Keeps the web Show Designer (https://triviacade.com/play/designer/, the
triviacade-site repo's play/designer/) in step with this machine, so shows
can be made while the Pi is off (2026-10-18).

While the app runs on the Pi, a background thread, every
config.WEB_SYNC_INTERVAL_SECONDS and a few seconds after any local show edit:
  * shows    -- sends drivers/show_library.export(), merges what comes back
               (newest edit wins, deletes stick -- show_library.merge());
  * catalog  -- the song list the web page picks from (no audio), re-sent
               whenever it changes (catalog() below; also /api/shows/songs);
  * questions-- each song's saved questions, for the preview (questions());
  * tags     -- decade tags made on the web page are applied to the library;
  * login    -- the admin password hash and the current login "epoch" are
               kept in web_auth.json for web/remote_auth.py;
  * previews -- songs without a low-bitrate web preview are encoded with
               ffmpeg (Opus, mono) one at a time and uploaded under an
               opaque id (preview_id()), never the file name.

Off the Pi (the PC simulator) it does nothing: the PC's shows.json is test data.
The bulk of the previews was made on the PC by tools/web_previews.py.
"""
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import config
from state import state

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTH_FILE = os.path.join(_BASE, "web_auth.json")

_wake = threading.Event()
_lock = threading.Lock()
_auth = None            # {"salt", "iter", "hash", "epoch"} -- cached web_auth.json
_tags_applied = []      # web tag ids applied here, acknowledged on the next sync
_last_catalog_hash = ""
_status = {"last_ok": 0.0, "last_error": "", "previews_missing": 0}


# ------------------------------------------------------------------ ids
def preview_id(kind, name):
    """Opaque, stable id for a song's (kind "song") or a show-bed sound's
    (kind "bed") web preview -- the server never sees a file name."""
    key = config.TUNNEL_REDIRECT_SECRET.encode()
    return hmac.new(key, f"{kind}:{os.path.basename(name)}".encode(), hashlib.sha256).hexdigest()[:16]


# --------------------------------------------------------------- catalog
def _bed_files():
    """Show-bed sounds the preview player needs: name -> path."""
    out = {}
    for path in (config.SHOW_INTRO_MUSIC_PATH, config.SHOW_WHEEL_THEME_PATH, config.SHOW_LETS_PLAY_STAB_PATH):
        if os.path.isfile(path):
            out[os.path.basename(path)] = path
    roots = {_stab_root(d) for d in config.MUSIC_DECADES}
    for fn in sorted(os.listdir(config.GAME_MUSIC_DIR)) if os.path.isdir(config.GAME_MUSIC_DIR) else []:
        stem, ext = os.path.splitext(fn)
        if ext.lower() == ".wav" and (stem in roots or stem.split("-v")[0] in roots):
            out[fn] = os.path.join(config.GAME_MUSIC_DIR, fn)
    return out


def _stab_root(decade):
    """"1980s" -> "80s"; 2000s and later keep four digits (drivers/show_bed.py)."""
    return decade[2:] if decade.startswith("19") else decade


def catalog():
    """The Show Designer's song list plus what the browser preview player needs
    (also served as the Pi's own /api/shows/songs)."""
    from drivers import auto_dj_engine, show_bed
    from drivers.music_library import music_library, sanitize_track_key
    from drivers.music_metadata_engine import get_metadata_for
    songs = []
    for t in music_library.all_tracks():
        meta = get_metadata_for(sanitize_track_key(t["title"], t["artist"])) or {}
        fname = os.path.basename(t["path"])
        songs.append({
            "file": fname, "pid": preview_id("song", fname), "title": t["title"], "artist": t["artist"],
            "duration": round(t["duration"] or 0.0, 1),
            "cue_out": round(auto_dj_engine.planned_length(t["path"], t["duration"] or 0.0), 1),
            "decade": meta.get("decade") or "",
            "energy": meta.get("energy_rank"),
        })
    songs.sort(key=lambda x: (x["artist"].lower(), x["title"].lower()))
    stab = 6.0
    try:
        spin = max(config.SHOW_WHEEL_MIN_SPIN_SECONDS, show_bed.wheel_theme_seconds())
    except Exception:
        spin = config.SHOW_WHEEL_MIN_SPIN_SECONDS
    bed = {name: preview_id("bed", name) for name in _bed_files()}
    return {
        "songs": songs,
        "spin_seconds": round(spin + stab, 1),
        "spin_fade_seconds": config.SHOW_SPIN_DECK_FADE_SECONDS,
        "wheel_decades": config.SHOW_INTRO_WHEEL_DECADES,
        "bed": bed,
        "sim": {
            "intro_file": os.path.basename(config.SHOW_INTRO_MUSIC_PATH),
            "wheel_file": os.path.basename(config.SHOW_WHEEL_THEME_PATH),
            "lets_play_file": os.path.basename(config.SHOW_LETS_PLAY_STAB_PATH),
            "loop_in": config.SHOW_INTRO_LOOP_IN_SECONDS,
            "loop_out": config.SHOW_INTRO_LOOP_OUT_SECONDS,
            "duck": config.SHOW_INTRO_DUCK_LEVEL,
            "duck_ramp": config.SHOW_INTRO_DUCK_RAMP_SECONDS,
            "swell": config.SHOW_INTRO_SWELL_SECONDS,
            "release_fade": config.SHOW_INTRO_RELEASE_FADE_SECONDS,
            "hands_off_intro": config.SHOW_HANDS_OFF_INTRO_SECONDS,
            "stab_lead": config.SHOW_STAB_LEAD_SECONDS,
            "dovetail_fade": config.SHOW_SONG_DOVETAIL_FADE_SECONDS,
            "wheel_min_spin": config.SHOW_WHEEL_MIN_SPIN_SECONDS,
            "lets_play_at": config.SHOW_INTRO_LETS_PLAY_AT_SECONDS,
            "trivia_at": config.SHOW_INTRO_TRIVIA_AT_SECONDS,
            "cade_at": config.SHOW_INTRO_CADE_AT_SECONDS,
            "line2_at": config.SHOW_INTRO_LINE2_AT_SECONDS,
            "line2_out_at": config.SHOW_INTRO_LINE2_OUT_AT_SECONDS,
            "assemble": config.SHOW_INTRO_ASSEMBLE_SECONDS,
            "wheel_turns": config.SHOW_INTRO_WHEEL_TURNS,
            "wheel_blink": config.SHOW_INTRO_WHEEL_BLINK_PERIOD_SECONDS,
            "contact": config.SHOW_END_LED_CONTACT_TEXT,
            # questions: "Who is this?" teaser + the Game Mode loop's holds
            "mystery_sparkle": config.MYSTERY_SPARKLE_SECONDS,
            "mystery_solid": config.MYSTERY_SOLID_SECONDS,
            "mystery_step": config.MYSTERY_CASCADE_STEP_SECONDS,
            "mystery_timeout": config.MYSTERY_REVEAL_TIMEOUT_SECONDS,
            "reveal_blink": config.MYSTERY_REVEAL_BLINK_SECONDS,
            "cascade_step": config.QUESTION_CASCADE_STEP_SECONDS,
            "scorecard_hold": config.GAME_SCORECARD_HOLD_SECONDS,
            "scroll_speed": config.SCROLL_SPEED_PX_PER_SEC,
            "scroll_pause": config.SCROLL_PAUSE_SECONDS,
            "font": config.FONT_5X7,
            "marquee": _marquee_bulbs(),
        },
    }


def _marquee_bulbs():
    """config.MARQUEE_LAYOUT's runs expanded to one (x, y) per bulb, per loop
    (photo pixels) -- the preview draws the cabinet in those coordinates."""
    out = {}
    for seg, runs in config.MARQUEE_LAYOUT.items():
        pts = []
        for x0, y0, x1, y1, n in runs:
            for i in range(n):
                f = i / (n - 1) if n > 1 else 0.0
                pts.append([round(x0 + (x1 - x0) * f), round(y0 + (y1 - y0) * f)])
        out[seg] = pts
    return out


def questions():
    """Each song's saved questions (track_cache.json), keyed by file name -- the
    preview asks them so the host can learn them. Short = LED wording, long =
    phone wording; "stage" is the audit stage (drivers/question_audit.py)."""
    from drivers import factoid_engine
    from drivers.music_library import music_library
    snap = factoid_engine.track_engine.snapshot() if factoid_engine.track_engine else {}
    songs = {}
    for t in music_library.all_tracks():
        qs = []
        for q in snap.get(factoid_engine._sanitize_track_key(t["title"], t["artist"])) or []:
            if not isinstance(q, dict) or not q.get("choices"):
                continue
            qs.append({
                "q": q.get("question", ""), "ql": q.get("question_long", ""),
                "c": q.get("choices", []), "cl": q.get("choices_long") or [],
                "k": q.get("correct_index", -1), "full": q.get("full", ""),
                "correction": q.get("correction", ""), "cat": q.get("category", ""),
                "stage": (q.get("audit") or {}).get("stage", "generated"),
            })
        if qs:
            songs[os.path.basename(t["path"])] = qs
    return {"songs": songs, "timeout": config.QUESTION_TIMEOUT_SECONDS,
            "solo_excluded": sorted(config.PANEL_SOLO_EXCLUDED_CATEGORIES)}


def bed_path(name):
    """Pi's own preview player: a show-bed sound by file name (or None)."""
    return _bed_files().get(os.path.basename(name or ""))


def _catalog_hash(cat):
    return hashlib.sha256(json.dumps(cat, sort_keys=True).encode()).hexdigest()[:16]


# ------------------------------------------------------------------ login
def auth():
    """{"salt", "iter", "hash", "epoch"} or None (no password set yet)."""
    global _auth
    if _auth is None:
        try:
            with open(AUTH_FILE, "r", encoding="utf-8") as f:
                _auth = json.load(f)
        except (FileNotFoundError, ValueError):
            _auth = {}
    return _auth if _auth.get("hash") else None


def epoch():
    auth()
    return int(_auth.get("epoch") or 0)


def _save_auth(new):
    global _auth
    auth()
    merged = dict(_auth)
    merged.update({k: v for k, v in new.items() if v is not None})
    if merged == _auth:
        return
    if merged.get("epoch") != _auth.get("epoch") and _auth.get("epoch"):
        print(f"[WEB SYNC] The rig's public IP changed -- admin logins ended (epoch {merged.get('epoch')}).")
    if merged.get("hash") != _auth.get("hash"):
        print("[WEB SYNC] Admin password " + ("updated." if _auth.get("hash") else "received -- admin pages now need a login."))
    _auth = merged
    tmp = AUTH_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_auth, f)
    os.replace(tmp, AUTH_FILE)


def note_epoch(value):
    """drivers/tunnel_engine.py: the redirector's reply carries the epoch."""
    try:
        _save_auth({"epoch": int(value)})
    except (TypeError, ValueError):
        pass


# ------------------------------------------------------------------- http
def _api(action, body=None, data=None, timeout=20.0):
    url = f"{config.WEB_DESIGNER_API_URL}?a={action}"
    headers = {"X-Trivia-Key": config.TUNNEL_REDIRECT_SECRET}
    if data is not None:
        r = requests.post(url, data=data, headers=dict(headers, **{"Content-Type": "application/octet-stream"}),
                          timeout=timeout)
    else:
        r = requests.post(url, json=body or {}, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()


def verify_handoff(code):
    """web/remote_auth.py: a one-time code from triviacade.com/play/admin.php."""
    try:
        d = _api("pi/handoff", {"code": code}, timeout=8.0)
    except Exception as e:
        print(f"[WEB SYNC] Login hand-off check failed: {e}")
        return False
    if d.get("epoch"):
        note_epoch(d["epoch"])
    return bool(d.get("ok"))


# ------------------------------------------------------------------- sync
def _apply_tags(tags):
    from drivers.music_library import music_library, sanitize_track_key
    from drivers import music_metadata_engine, track_record
    by_file = {os.path.basename(t["path"]): t for t in music_library.all_tracks()}
    for tag in tags or []:
        if tag.get("id") in _tags_applied:
            continue
        t = by_file.get(tag.get("file"))
        if t is not None and tag.get("decade") in config.MUSIC_DECADES:
            music_metadata_engine.save_metadata_for(sanitize_track_key(t["title"], t["artist"]),
                                                    decade=tag["decade"])
            track_record.touch_edited(t["path"])
            print(f"[WEB SYNC] Decade from the web page: '{t['title']}' -> {tag['decade']}.")
        _tags_applied.append(tag.get("id"))


def sync_once():
    global _last_catalog_hash
    from drivers import show_library
    with _lock:
        local = show_library.export()
        d = _api("pi/sync", {"shows": local["shows"], "deleted": local["deleted"],
                             "tags_applied": list(_tags_applied)})
        del _tags_applied[:len(_tags_applied)]
        n = show_library.merge(d.get("shows"), d.get("deleted"))
        if n:
            print(f"[WEB SYNC] {n} change(s) from the web Show Designer.")
            if state.show_phase == "setup":
                from drivers import panel_menu
                panel_menu.refresh_saves()
        _apply_tags(d.get("tags"))
        _save_auth({**(d.get("auth") or {}), "epoch": d.get("epoch")})
        cat = catalog()
        h = _catalog_hash(cat)
        if h != d.get("catalog_hash"):
            cat["hash"] = h
            _api("pi/catalog", cat, timeout=60.0)
            print(f"[WEB SYNC] Song list sent to the web Show Designer ({len(cat['songs'])} songs).")
        _last_catalog_hash = h
        qs = questions()
        qh = _catalog_hash(qs)
        if qh != d.get("questions_hash"):
            qs["hash"] = qh
            _api("pi/questions", qs, timeout=60.0)
            print(f"[WEB SYNC] Questions sent to the web Show Designer ({len(qs['songs'])} songs).")
        _status["last_ok"] = time.time()
        _status["last_error"] = ""
        return cat, set(d.get("audio_have") or [])


def _ffmpeg():
    return shutil.which("ffmpeg")


def encode_preview(src, dst):
    """Low-bitrate mono preview (config.WEB_PREVIEW_*) -- Opus in a WebM file."""
    cmd = [_ffmpeg(), "-v", "error", "-y", "-i", src, "-vn", "-ac", "1", "-ar", "48000",
           "-c:a", "libopus", "-b:a", config.WEB_PREVIEW_BITRATE, "-application", "audio",
           "-f", "webm", dst]
    if sys.platform.startswith("linux"):
        cmd = ["nice", "-n", "19"] + cmd
    subprocess.run(cmd, check=True, timeout=300)


def _upload_previews(cat, have):
    """A few missing previews per pass, so a fresh batch never hogs the Pi."""
    if not _ffmpeg():
        return
    from drivers.music_library import music_library
    paths = {os.path.basename(t["path"]): t["path"] for t in music_library.all_tracks()}
    todo = [(s["pid"], paths.get(s["file"])) for s in cat["songs"] if s["pid"] not in have]
    todo += [(pid, bed_path(name)) for name, pid in cat["bed"].items() if pid not in have]
    todo = [(pid, p) for pid, p in todo if p and os.path.isfile(p)]
    _status["previews_missing"] = len(todo)
    for pid, src in todo[:config.WEB_PREVIEW_PER_PASS]:
        fd, tmp = tempfile.mkstemp(suffix=".webm")
        os.close(fd)
        try:
            encode_preview(src, tmp)
            with open(tmp, "rb") as f:
                _api(f"pi/audio-put&id={pid}", data=f.read(), timeout=120.0)
            print(f"[WEB SYNC] Preview uploaded: {os.path.basename(src)}")
        except Exception as e:
            print(f"[WEB SYNC] Preview failed for {os.path.basename(src)}: {e}")
            break
        finally:
            os.remove(tmp)


def _loop():
    time.sleep(config.WEB_SYNC_FIRST_DELAY_SECONDS)
    last_error = None
    while True:
        try:
            cat, have = sync_once()
            if last_error:
                print("[WEB SYNC] Back in touch with the web Show Designer.")
            last_error = None
            _upload_previews(cat, have)
        except Exception as e:
            msg = str(e)
            _status["last_error"] = msg
            if msg != last_error:
                print(f"[WEB SYNC] Sync failed: {msg}")
            last_error = msg
        _wake.wait(config.WEB_SYNC_INTERVAL_SECONDS)
        if _wake.is_set():
            _wake.clear()
            time.sleep(config.WEB_SYNC_EDIT_SETTLE_SECONDS)   # let a burst of edits finish
            _wake.clear()


def _on_local_change():
    _wake.set()


def status():
    return dict(_status)


def start():
    """main.py, once at startup. Pi only."""
    if requests is None or not config.WEB_DESIGNER_API_URL:
        return
    if not sys.platform.startswith("linux"):
        print("[WEB SYNC] Not on the Pi -- web Show Designer sync off.")
        return
    from drivers import show_library
    show_library.on_change(_on_local_change)
    threading.Thread(target=_loop, daemon=True, name="web-sync").start()
