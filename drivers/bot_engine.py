"""Remote ADD BOT / REMOVE ALL BOTS (2026-11-25): stand-in teams for practice and demos.

ADD BOT starts one tools/phone_bots.py team on this machine (straight to 127.0.0.1, answer key read
from the app folder), named from the bots' list (the next name not already playing). REMOVE ALL BOTS
stops every bot process -- these and any left from before a restart -- and takes their teams off the
leaderboard. Bots are known by the "bot" flag the join handler sets from the bots' user agent.
The self test runs its own bots; while it runs, REMOVE ALL BOTS is refused (it would spoil the test).
"""
import os
import signal
import subprocess
import sys
import threading
import time

import config
from state import state

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG = os.path.join(_APP_DIR, "bots.log")
_procs = []


def _names():
    sys.path.insert(0, os.path.join(_APP_DIR, "tools"))
    try:
        import phone_bots
        return phone_bots.NAMES
    except Exception:
        return []


def _bot_ids():
    return [pid for pid, p in state.quiz_players.items() if p.get("bot")]


def count():
    return len(_bot_ids())


def add():
    names = _names()
    taken = {str(p.get("name", "")).lower() for p in state.quiz_players.values()}
    taken |= {n.lower() for proc, n in _procs if proc.poll() is None}   # started, maybe not joined yet
    offset = next((i for i, n in enumerate(names) if n.lower() not in taken
                   and n[:20].lower() not in taken), len(_procs) % max(1, len(names)))
    try:
        log = open(_LOG, "a", encoding="utf-8")
        log.write(f"\n===== ADD BOT {time.strftime('%Y-%m-%d %H:%M:%S')} ({names[offset] if names else '?'}) =====\n")
        log.flush()
        proc = subprocess.Popen(
            [sys.executable, "-u", os.path.join(_APP_DIR, "tools", "phone_bots.py"),
             "--teams", "1", "--offset", str(offset), "--skill", str(config.SELFTEST_BOT_SKILL),
             "--base", f"http://127.0.0.1:{config.WEB_REMOTE_PORT}", "--host", "", "--local-dir", _APP_DIR],
            stdout=log, stderr=subprocess.STDOUT, cwd=_APP_DIR, start_new_session=True)
    except Exception as exc:
        print(f"[BOTS] Couldn't start a bot: {exc}")
        return {"ok": False, "reason": str(exc)}
    name = names[offset] if names else "a bot"
    _procs.append((proc, name))
    print(f"[BOTS] Added '{name}' (pid {proc.pid}).")
    return {"ok": True, "name": name}


def _sweep():
    n = 0
    for pid in _bot_ids():
        state.quiz_players.pop(pid, None)
        state.show_cumulative_scores.pop(pid, None)
        n += 1
    return n


def remove_all():
    from drivers import selftest_engine
    if selftest_engine.status().get("active"):
        return {"ok": False, "reason": "The self test is running its bots -- stop it first (Setup page)."}
    for proc, _name in _procs:
        try:
            proc.send_signal(signal.SIGTERM)
        except Exception:
            pass
    _procs.clear()
    try:   # bots started before a restart (or by hand on this machine)
        subprocess.run(["pkill", "-TERM", "-f", "tools/phone_bots.py"], timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    n = _sweep()
    # a bot that polled between the signal and its exit would join again: sweep once more
    threading.Timer(4.0, _sweep).start()
    print(f"[BOTS] Removed all bots ({n} team(s)).")
    return {"ok": True, "removed": n}
