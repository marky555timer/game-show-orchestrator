"""drivers/net_health_engine.py
Connection health for the phone game (2026-10-18).

Phones answer optimistically (web/static/play.html), so ordinary tunnel lag
is hidden from the players -- this module catches the abnormal kind, where
the game itself is in jeopardy:

  * every phone times its own /api/player/question polls and reports the
    last round trip on the next one (record_rtt()); a page going to the
    background says so first ("away", a locked phone is not lag), so most
    phones going quiet at once means they can't reach the game;
  * the Pi pings its router and the internet every few seconds;
  * the cloudflared tunnel's status (drivers/tunnel_engine.py).

snapshot() is the admin page's view (web/remote_server.py's
/api/gamepad/status). level() is "ok" | "warn" | "bad", debounced so one
blip doesn't flash a warning. While it is "bad" with phones signed up:
graphics/matrix_canvas.py blinks a warning sign in panel 2's corner and
holding() keeps the next question from starting (factoid queue advance and
the "Who is this?" teaser) until it recovers, unless the admin pressed
CONTINUE ANYWAY (override_hold()). Music and lights carry on regardless.
"""
import collections
import re
import statistics
import subprocess
import sys
import threading
import time

import config
from state import state

_lock = threading.Lock()
_rtts = {}                      # player_id -> deque of recent round trips (ms)
_pings = {"router": collections.deque(maxlen=config.NET_PING_WINDOW),
          "internet": collections.deque(maxlen=config.NET_PING_WINDOW)}  # ms, or None = lost
_router_ip = None
_started_at = time.time()

_eff = {"level": "ok", "raw": "ok", "raw_since": time.time()}
_override = {"on": False}
_silence_latch = set()          # phones caught going quiet together (see _evaluate)
_cache = {"at": 0.0, "snap": None}

_TIME_RE = re.compile(r"time[=<]\s*([\d.]+)\s*ms", re.I)
_RANK = {"ok": 0, "warn": 1, "bad": 2}


# ------------------------------------------------------------------ input
def record_rtt(player_id, ms):
    """A phone's last poll round trip (play.html sends it with the next poll)."""
    try:
        ms = float(ms)
    except (TypeError, ValueError):
        return
    if not 0 < ms < 120000:
        return
    with _lock:
        d = _rtts.get(player_id)
        if d is None:
            d = _rtts[player_id] = collections.deque(maxlen=5)
        d.append(ms)


def override_hold():
    """Admin CONTINUE ANYWAY: stop holding questions for this spell of
    trouble. Clears itself once the network is back to normal."""
    _override["on"] = True
    _cache["at"] = 0.0
    print("[NET] Admin chose to continue despite network trouble -- questions not held.")


# ---------------------------------------------------------------- pinging
def _default_gateway():
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                parts = line.split()
                if len(parts) > 2 and parts[1] == "00000000":
                    g = int(parts[2], 16)
                    return ".".join(str((g >> s) & 0xFF) for s in (0, 8, 16, 24))
    except OSError:
        pass
    return None


def _ping(host):
    """Round trip in ms, or None if lost."""
    if sys.platform.startswith("win"):
        cmd = ["ping", "-n", "1", "-w", "1000", host]
    else:
        cmd = ["ping", "-c", "1", "-W", "1", host]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return None
    m = _TIME_RE.search(out or "")
    return float(m.group(1)) if m else None


def _ping_loop():
    global _router_ip
    while True:
        if _router_ip is None or int(time.time()) % 60 < config.NET_PING_INTERVAL_SECONDS:
            _router_ip = _default_gateway()
        if _router_ip:
            r = _ping(_router_ip)
            with _lock:
                _pings["router"].append(r)
        i = _ping(config.NET_INTERNET_PING_HOST)
        with _lock:
            _pings["internet"].append(i)
        time.sleep(config.NET_PING_INTERVAL_SECONDS)


def start():
    """main.py, once at startup."""
    if not config.NET_HEALTH_ENABLED:
        return
    threading.Thread(target=_ping_loop, daemon=True, name="net-health").start()


# --------------------------------------------------------------- judging
def _ping_stats(samples):
    if not samples:
        return {"loss": 0.0, "rtt_ms": None, "samples": 0}
    got = [s for s in samples if s is not None]
    return {"loss": round(1 - len(got) / len(samples), 3),
            "rtt_ms": round(statistics.median(got)) if got else None, "samples": len(samples)}


def _phone_status(rtt, silent, away):
    """ok | slow | bad (by round trip) | silent (stopped polling) | away
    (the page told us it went to the background -- a locked phone, not lag)."""
    if away and silent >= 3.0:
        return "away"
    if silent >= config.NET_PHONE_SILENT_SECONDS:
        return "silent"
    if rtt is not None and rtt >= config.NET_PHONE_BAD_MS:
        return "bad"
    if rtt is not None and rtt >= config.NET_PHONE_SLOW_MS:
        return "slow"
    return "ok"


def _evaluate(now):
    players = []
    with _lock:
        rtts = {pid: list(d) for pid, d in _rtts.items()}
        router = _ping_stats(list(_pings["router"]))
        internet = _ping_stats(list(_pings["internet"]))
        for pid in [p for p in _rtts if p not in state.quiz_players]:
            del _rtts[pid]
    for pid, p in list(state.quiz_players.items()):
        silent = now - p.get("last_seen", 0.0)
        if silent > config.NET_PHONE_FORGET_SECONDS:
            continue  # left the game (closed the page long ago)
        r = rtts.get(pid)
        rtt = round(statistics.median(r)) if r else None
        players.append({"player_id": pid, "initials": p.get("initials", "???"), "rtt_ms": rtt,
                        "silent_s": round(silent, 1),
                        "status": _phone_status(rtt, silent, p.get("away", False))})

    reasons = []
    raw = "ok"

    def worse(level, why):
        nonlocal raw
        reasons.append(why)
        if _RANK[level] > _RANK[raw]:
            raw = level

    # Round trips, among phones that are polling right now.
    polling = [p for p in players if p["status"] in ("ok", "slow", "bad")]
    lagging = [p for p in polling if p["status"] != "ok"]
    bad = [p for p in polling if p["status"] == "bad"]
    if len(polling) >= config.NET_MIN_PHONES_FOR_SYSTEM and len(lagging) >= config.NET_SYSTEM_SHARE * len(polling):
        worse("bad" if len(bad) >= config.NET_SYSTEM_SHARE * len(polling) else "warn",
              f"{len(lagging)} of {len(polling)} phones lagging")
    # Mass silence: most of the phones that were polling a moment ago all
    # stopped at once. One phone locking its screen is staggered and says
    # "away" first; an outage cuts everyone off together.
    # The phones caught going quiet are latched, so an outage longer than
    # the look-back still counts until most of them are back.
    by_id = {p["player_id"]: p for p in players}
    if not _silence_latch:
        recent = [p for p in players if p["status"] != "away" and p["silent_s"] <= config.NET_SILENCE_LOOKBACK_SECONDS]
        quiet = [p for p in recent if p["status"] == "silent"]
        if len(recent) >= config.NET_MIN_PHONES_FOR_SYSTEM and len(quiet) >= config.NET_SILENCE_SHARE * len(recent):
            _silence_latch.update(p["player_id"] for p in recent)
    if _silence_latch:
        latched = [by_id[pid] for pid in _silence_latch if pid in by_id and by_id[pid]["status"] != "away"]
        quiet = [p for p in latched if p["status"] == "silent"]
        if len(latched) >= config.NET_MIN_PHONES_FOR_SYSTEM and len(quiet) >= config.NET_SILENCE_SHARE * len(latched):
            worse("bad", f"{len(quiet)} of {len(latched)} phones stopped reaching the game")
            lagging = lagging + quiet
        else:
            _silence_latch.clear()
    if router["samples"] >= 5:
        if router["loss"] >= config.NET_ROUTER_LOSS_BAD:
            worse("bad", f"Pi losing {router['loss']:.0%} of packets to the router")
        elif router["loss"] >= config.NET_ROUTER_LOSS_WARN:
            worse("warn", f"Pi losing {router['loss']:.0%} of packets to the router")
    if internet["samples"] >= 5:
        if internet["loss"] >= config.NET_INTERNET_LOSS_BAD:
            worse("bad", f"internet losing {internet['loss']:.0%} of packets")
        elif internet["loss"] >= config.NET_INTERNET_LOSS_WARN:
            worse("warn", f"internet losing {internet['loss']:.0%} of packets")
        elif internet["rtt_ms"] and internet["rtt_ms"] >= config.NET_INTERNET_SLOW_MS:
            worse("warn", f"internet slow ({internet['rtt_ms']} ms)")
    try:
        from drivers import tunnel_engine
        tunnel = tunnel_engine.get_status()
        if tunnel_engine.get_current_tunnel_url() == "" and tunnel not in ("disabled", "not_installed") \
                and now - _started_at > config.NET_TUNNEL_GRACE_SECONDS:
            worse("bad", "tunnel down -- phones can't reach the game")
    except Exception:
        tunnel = "unknown"

    # Debounce: worse only after it has lasted NET_RED_CONFIRM_SECONDS,
    # better only after NET_GREEN_CONFIRM_SECONDS.
    if raw != _eff["raw"]:
        _eff["raw"], _eff["raw_since"] = raw, now
    held = now - _eff["raw_since"]
    if _RANK[raw] > _RANK[_eff["level"]] and held >= config.NET_RED_CONFIRM_SECONDS:
        _eff["level"] = raw
        print(f"[NET] Network {raw.upper()}: {'; '.join(reasons)}")
    elif _RANK[raw] < _RANK[_eff["level"]] and held >= config.NET_GREEN_CONFIRM_SECONDS:
        _eff["level"] = raw
        print(f"[NET] Network back to {raw.upper()}.")
    if _eff["level"] == "ok":
        _override["on"] = False

    level = _eff["level"]
    holding = (level == "bad" and config.NET_HOLD_QUESTIONS and not _override["on"]
               and any(p["status"] != "away" for p in players))
    if level == "bad":
        headline = "NETWORK TROUBLE: " + ("; ".join(reasons) or "recovering")
    elif level == "warn":
        headline = "Network shaky: " + ("; ".join(reasons) or "recovering")
    else:
        headline = ""
    order = {"bad": 0, "silent": 1, "slow": 2, "ok": 3, "away": 4}
    players.sort(key=lambda p: (order.get(p["status"], 9), p["initials"]))
    return {"level": level, "raw": raw, "headline": headline, "reasons": reasons, "holding": holding,
            "override": _override["on"], "phones_total": len(players), "phones_lagging": len(lagging),
            "players": players, "router": router, "internet": internet, "tunnel": tunnel}


_eval_lock = threading.Lock()


def snapshot():
    """Read by the render loop every frame and by web threads -- recomputed at
    most twice a second."""
    with _eval_lock:
        now = time.time()
        if _cache["snap"] is None or now - _cache["at"] >= 0.5:
            _cache["snap"] = _evaluate(now)
            _cache["at"] = now
        return _cache["snap"]


def level():
    return snapshot()["level"] if config.NET_HEALTH_ENABLED else "ok"


def holding():
    """True while questions should wait for the network to recover."""
    return config.NET_HEALTH_ENABLED and snapshot()["holding"]


def show_on_panels():
    """The machine's own warning sign: only when phones are in the game."""
    return config.NET_HEALTH_ENABLED and bool(state.quiz_players) and snapshot()["level"] == "bad"
