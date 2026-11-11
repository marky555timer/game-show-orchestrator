"""drivers/failover_engine.py
Backup-Pi failover through triviacade.com (2026-11-08).

Both Pis run this same code. Every config.FAILOVER_REPORT_SECONDS each one
checks in with the site's play/failover.php, which remembers which Pi OWNS
the show (the one in charge) and the owner's latest report of where the
show is: the show, the song, players, scores, round and what has been asked.

  * The owner sends that report with every check-in, and is the only Pi that
    points the guests' QR redirect (play/update.php) at itself.
  * The other Pi just reads it: its admin remote shows the owner's position
    and a TAKE CONTROL button. Pressing it claims the show, restores the
    players and scores, and resumes on the NEXT song (show_engine.resume_from).
  * A Pi that finds out someone else has taken over stands down: back to the
    panel menu, music off, QR redirect left alone, show starts refused, until
    TAKE CONTROL is pressed on it.

Roles: a Pi is the primary unless failover_role.json (next to config.py, not
in git) says {"role": "backup", "name": "Backup Pi"}. The primary claims the
show by itself whenever it is running one and nobody else is in charge; the
backup only ever takes over on command and never hands-off starts a show.

No internet = no change: a Pi only stands down on a definite answer from
the site that another live Pi owns the show.
"""
import json
import queue
import socket
import sys
import threading
import time
import uuid

import config
from state import state

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

_lock = threading.Lock()
_wake = threading.Event()
_actions = queue.Queue()     # main-thread work: ("stand_down",) | ("takeover", snapshot)

_enabled = False
_role = "primary"
_name = ""
_device = ""

_view = {}                   # last reply from failover.php (+ "reached_at" monotonic)
_tried = False               # at least one check-in attempt has finished (ok or not)
_last_error = ""
_snapshot = None             # latest local report, built on the main thread
_snapshot_built_at = 0.0
_take_requested = False      # TAKE CONTROL pressed, claim not yet sent
_claim_wanted = False        # primary running a show with nobody in charge
_stood_down_epoch = -1       # epoch we last stood down for (once per takeover)
_last_message = ""           # last takeover/stand-down result, for the remote
_last_message_at = 0.0


# ------------------------------------------------------------- identity
def _load_role():
    global _role, _name
    try:
        with open(config.FAILOVER_ROLE_PATH, encoding="utf-8") as f:
            d = json.load(f)
        _role = "backup" if d.get("role") == "backup" else "primary"
        _name = str(d.get("name") or "")[:40]
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[FAILOVER] Couldn't read {config.FAILOVER_ROLE_PATH}: {e}")
    if not _name:
        _name = "Backup Pi" if _role == "backup" else "Main Pi"


def _device_id():
    # Hostname + the network card's MAC: a cloned SD card keeps the hostname
    # and every file, but not the hardware address.
    host = socket.gethostname().split(".")[0][:24] or "pi"
    return f"{host}-{uuid.getnode():012x}"[-40:]


def role():
    return _role


def enabled():
    return _enabled


# ----------------------------------------------------------- ownership
def _fresh_view():
    """The last reply, if it is recent enough to act on."""
    with _lock:
        v = dict(_view)
    if not v or time.monotonic() - v.get("reached_at", 0) > config.FAILOVER_ALIVE_SECONDS:
        return None
    return v


def i_own():
    v = _fresh_view()
    return bool(v) and v.get("owner") == _device


def other_owns():
    """True when the site says another Pi, still checking in, runs the show."""
    if not _enabled:
        return False
    v = _fresh_view()
    return bool(v) and bool(v.get("owner")) and v["owner"] != _device and v.get("owner_alive", False)


def start_allowed():
    """show_engine.start_show(): refuse while another live Pi has the show."""
    return not other_owns()


def hands_off_allowed():
    """panel_menu: the backup never starts a show by itself."""
    return not (_enabled and _role == "backup") and not other_owns()


def may_publish_tunnel():
    """tunnel_engine: only the Pi in charge points the guests' QR at itself."""
    if not _enabled:
        return True
    with _lock:
        v = dict(_view)
    if not v:
        # Not heard from the site: a just-booted primary waits for its first
        # answer (the backup may have the show); if the site can't be reached
        # it carries on publishing as it always has.
        return _role == "primary" and _tried
    owner = v.get("owner", "")
    if owner == _device:
        return True
    if not owner or not v.get("owner_alive", False):
        return _role == "primary"
    return False


def _show_running():
    return state.show_phase in ("dark", "intro", "live", "countdown", "outro")


# ------------------------------------------------------------ snapshot
def _build_snapshot():
    from drivers import show_runner, deck_orchestrator
    title, artist = deck_orchestrator.get_now_playing()
    elapsed = time.time() - state.now_playing_started_at if state.now_playing_started_at else 0.0
    players = {pid: {"initials": p.get("initials", ""), "score": p.get("score", 0),
                     "avatar_color": p.get("avatar_color", "")}
               for pid, p in list(state.quiz_players.items())}
    return {
        "v": 1,
        "phase": state.show_phase,
        "background": bool(state.show_background),
        "medley": bool(state.medley_active),
        "show": show_runner.snapshot(),
        "song": {"title": title or "", "artist": artist or "",
                 "file": state.now_playing_path.rsplit("/", 1)[-1] if state.now_playing_path else "",
                 "elapsed": round(max(0.0, elapsed), 1),
                 "duration": round(float(state.auto_dj_track_duration or 0), 1)},
        "round": state.game_round_number,
        "players": players,
        "cumulative": dict(state.show_cumulative_scores),
        "asked_artists": sorted(state.asked_artists),
        "asked_questions": sorted(state.asked_question_texts),
        "settings": {k: bool(getattr(state, k, False)) for k in (
            "questions_suppressed", "listen_enabled", "show_outro_enabled", "auto_advance_after_quiz",
            "auto_dj_enabled", "auto_announce_enabled", "sweepers_enabled", "sfx_enabled")},
        "linger_pct": state.autoadv_linger_pct,
    }


# ------------------------------------------------------- remote button
def request_take():
    """Admin remote TAKE CONTROL."""
    global _take_requested
    if not _enabled:
        return {"ok": False, "message": "Failover isn't running on this Pi."}
    if i_own():
        return {"ok": True, "message": "This Pi already has the show."}
    _take_requested = True
    _wake.set()
    return {"ok": True, "message": "Taking control..."}


def _note(msg):
    global _last_message, _last_message_at
    _last_message, _last_message_at = msg, time.time()
    print(f"[FAILOVER] {msg}")


def status():
    """Admin remote panel."""
    with _lock:
        v = dict(_view)
        err = _last_error
    devices = v.get("devices") or {}
    other = None
    for dev_id, d in devices.items():
        # A Pi not heard from in 12 hours is retired/unplugged (or a test).
        if dev_id != _device and d.get("age", 1e9) < 12 * 3600:
            if other is None or d.get("age", 1e9) < other.get("age", 1e9):
                other = dict(d, id=dev_id)
    owner = v.get("owner", "")
    snap = v.get("snapshot") or {}
    show = snap.get("show") or {}
    song = snap.get("song") or {}
    return {
        "enabled": _enabled,
        "reachable": bool(v) and time.monotonic() - v.get("reached_at", 0) < config.FAILOVER_ALIVE_SECONDS,
        "error": err,
        "me": {"id": _device, "name": _name, "role": _role},
        "i_own": owner == _device and bool(owner),
        "owner": owner,
        "owner_name": (devices.get(owner) or {}).get("name", "") if owner else "",
        "owner_alive": bool(v.get("owner_alive")),
        "other": other,
        "taking": _take_requested,
        "message": _last_message if time.time() - _last_message_at < 120 else "",
        "backup_admin_url": config.FAILOVER_BACKUP_ADMIN_URL,
        "position": None if not snap else {
            "age": v.get("snapshot_age"),
            "phase": snap.get("phase"),
            "background": snap.get("background"),
            "medley": snap.get("medley"),
            "show": show.get("name", ""),
            "song_number": show.get("song_number", 0),
            "song_count": show.get("song_count", 0),
            "mode": show.get("mode", ""),
            "title": song.get("title", ""),
            "artist": song.get("artist", ""),
            "elapsed": song.get("elapsed", 0),
            "duration": song.get("duration", 0),
            "next_title": show.get("next_title", ""),
            "next_artist": show.get("next_artist", ""),
            "players": len(snap.get("players") or {}),
            "round": snap.get("round", 1),
            "leader": _leader(snap),
        },
    }


def _leader(snap):
    players = snap.get("players") or {}
    totals = {pid: int(p.get("score", 0) or 0) + int((snap.get("cumulative") or {}).get(pid, 0) or 0)
              for pid, p in players.items()}
    if not totals or max(totals.values()) <= 0:
        return ""
    top = max(totals, key=totals.get)
    return f"{players[top].get('initials', '?')} ({totals[top]})"


# --------------------------------------------------------- network loop
def _post(action, snapshot=None, force=False):
    from drivers import tunnel_engine
    url = tunnel_engine.get_current_tunnel_url()
    body = {"secret": config.TUNNEL_REDIRECT_SECRET, "action": action, "device": _device,
            "name": _name, "role": _role, "version": config.APP_VERSION,
            "admin_url": url or "", "force": force}
    if snapshot is not None:
        body["snapshot"] = snapshot
    r = requests.post(config.FAILOVER_URL, json=body, timeout=6.0)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    d = r.json()
    if not d.get("ok"):
        raise RuntimeError(d.get("error") or "refused")
    return d


def _handle_reply(d, claimed_now):
    """Background thread: decide what the reply means (main thread acts)."""
    global _stood_down_epoch, _claim_wanted
    owner = d.get("owner", "")
    epoch = d.get("epoch", 0)
    if claimed_now:
        if owner == _device:
            snap = d.get("snapshot")
            _actions.put(("takeover", snap or {}))
        return
    if owner and owner != _device and d.get("owner_alive"):
        # Someone else runs the show: if we are running one, stand down once.
        if _show_running() and _stood_down_epoch != epoch:
            _stood_down_epoch = epoch
            _actions.put(("stand_down", (d.get("devices") or {}).get(owner, {}).get("name", "the other Pi")))
        _claim_wanted = False
        return
    # Nobody (alive) in charge: a primary running a show claims it.
    _claim_wanted = _role == "primary" and _show_running() and owner != _device


def _loop():
    global _view, _last_error, _take_requested, _claim_wanted, _tried
    time.sleep(2.0)
    logged = ""
    while True:
        claimed_now = False
        try:
            mine = i_own() or (_role == "primary" and not _view)
            snap = _snapshot if mine else None
            if _take_requested:
                d = _post("claim", snapshot=None, force=True)
                _take_requested = False
                claimed_now = True
            elif _claim_wanted:
                d = _post("claim", snapshot=_snapshot, force=False)
                _claim_wanted = False
                if d.get("owner") == _device:
                    _note("Running a show and nobody else is in charge -- this Pi has the show.")
            else:
                d = _post("report", snapshot=snap)
            d["reached_at"] = time.monotonic()
            with _lock:
                _view = d
                _last_error = ""
            _handle_reply(d, claimed_now)
            if logged != "ok":
                print(f"[FAILOVER] Checking in with {config.FAILOVER_URL} as {_name!r} ({_role}).")
                logged = "ok"
        except Exception as e:
            if claimed_now or _take_requested:
                _take_requested = False
                _note(f"TAKE CONTROL failed -- couldn't reach triviacade.com ({e}).")
            with _lock:
                _last_error = str(e)
            if logged != str(e):
                print(f"[FAILOVER] Check-in failed: {e}")
                logged = str(e)
        _tried = True
        _wake.wait(config.FAILOVER_REPORT_SECONDS)
        _wake.clear()


def start():
    """main.py, once at startup. Pi only."""
    global _enabled, _device
    if not config.FAILOVER_ENABLED or requests is None or not config.FAILOVER_URL:
        return
    if not sys.platform.startswith("linux"):
        print("[FAILOVER] Not on the Pi -- failover off.")
        return
    _load_role()
    _device = _device_id()
    _enabled = True
    threading.Thread(target=_loop, daemon=True, name="failover").start()
    print(f"[FAILOVER] This Pi is {_name!r} ({_role}, id {_device}).")


# ----------------------------------------------------------- main thread
def update(now):
    """Pumped every frame from show_engine.update()."""
    global _snapshot, _snapshot_built_at
    if not _enabled:
        return
    try:
        action = _actions.get_nowait()
    except queue.Empty:
        action = None
    if action:
        _run_action(action)
    if now - _snapshot_built_at >= 2.0:
        _snapshot_built_at = now
        try:
            _snapshot = _build_snapshot()
        except Exception as e:
            print(f"[FAILOVER] Snapshot failed: {e}")


def _run_action(action):
    from drivers import show_engine, tunnel_engine
    kind = action[0]
    if kind == "stand_down":
        who = action[1]
        if _show_running() or state.show_background:
            show_engine.back_to_menu()
        _note(f"{who} took over the show -- this Pi stood down (TAKE CONTROL to run it here again).")
    elif kind == "takeover":
        snap = action[1] or {}
        try:
            msg = show_engine.resume_from(snap) if snap else "Nothing to resume -- this Pi is now in charge."
        except Exception as e:
            import traceback
            traceback.print_exc()
            msg = f"Took control, but resuming the show failed: {e}"
        _note(msg)
        # Guests' QR now points here -- only once the players are restored.
        threading.Thread(target=tunnel_engine.publish_now, daemon=True).start()
        _wake.set()   # send our first report straight away
