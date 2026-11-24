"""drivers/selftest_engine.py
Self-looping SELF TEST (2026-11-23, Setup page): plays whole shows back to back with no host, to learn
how long a show lasts, what the linger slider does to that, and to shake out hangs.

  * START SELF TEST closes any running show and starts the show picked on the Setup page.
  * Each run: load the show, start the intro at once, and at the intro's pause (the theme looping for
    the host) wait SELFTEST_INTRO_PAUSE_SECONDS, then move on into round 1.
  * The linger slider is set for the whole run: 0% on the first, +20% on each replay, up to 100%.
  * After the 100% run, the next untested show (no finished run yet) is tested the same way, until
    every show has been.
  * Teams: tools/phone_bots.py runs on this machine (SELFTEST_BOT_TEAMS bots, medium IQ, against
    127.0.0.1), so trivia rounds reach their goals. Cliffhanger AUTO HOST and auto-advance after the
    quiz are switched on for the test; the host's own settings come back when it ends.
  * A solo round that waits for the host (Tipsy Simon / host-picks) is ended after a moment and noted.
  * Watchdog: no progress (song, round, intermission, clip, phase) for SELFTEST_STALL_SECONDS -> nudge
    (next song), then end the round, then give the run up and start it again (once), noting each.
  * Results (selftest.json): one row per run -- show, linger, minutes, per-part minutes, status, notes.
    A test still running when the app restarts carries on (that run starts again).

Pumped from drivers/show_engine.py::update(); the web routes only post requests (thread-safe).
"""
import json
import os
import signal
import subprocess
import sys
import time

import config
from state import state

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(_APP_DIR, config.SELFTEST_FILE)
_BOT_LOG = os.path.join(_APP_DIR, "selftest_bots.log")
LINGER_STEPS = (0, 20, 40, 60, 80, 100)

_d = {"active": False, "show": "", "linger": 0, "results": [], "saved": {}, "bot_pid": 0,
      "retry": 0}
_rt = {"step": "", "at": 0.0, "run": None, "request": None, "boot": time.time(), "loaded": False,
       "proc": None, "sig": None, "sig_at": 0.0, "stalls": 0, "solo_wait": 0.0, "seg": None, "seg_at": 0.0,
       "tries": 0}


# ------------------------------------------------------------------ storage
def _load():
    if _rt["loaded"]:
        return
    _rt["loaded"] = True
    try:
        with open(_PATH, encoding="utf-8") as f:
            _d.update(json.load(f))
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[SELFTEST] Couldn't read {_PATH}: {e}")
    if _d.get("active"):
        _rt["step"] = "resume"
        _rt["at"] = time.time() + config.SELFTEST_RESUME_DELAY_SECONDS
        print(f"[SELFTEST] Still on after a restart -- '{_d['show']}' at {_d['linger']}% linger starts "
              f"again in {config.SELFTEST_RESUME_DELAY_SECONDS:.0f}s.")


def _save():
    try:
        tmp = _PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_d, f, indent=1)
        os.replace(tmp, _PATH)
    except Exception as e:
        print(f"[SELFTEST] Couldn't save {_PATH}: {e}")


def active():
    _load()
    return bool(_d.get("active"))


# ------------------------------------------------------------------ requests (web thread)
def request_start(show_name):
    _rt["request"] = ("start", show_name)
    return True


def request_cliff_demo():
    """2026-11-23: interrupt the running test show and jump to its Cliffhanger round for a demo (AUTO
    HOST off so the host runs it). The run is then logged as "demo" and played again."""
    _rt["request"] = ("cliff_demo",)
    return True


def request_stop():
    _rt["request"] = ("stop",)
    return True


def clear_results():
    _load()
    if _d.get("active"):
        return False
    _d["results"] = []
    _save()
    return True


# ------------------------------------------------------------------ status (web thread)
def _fmt_min(sec):
    return round(sec / 60.0, 1)


def status():
    _load()
    run = _rt["run"]
    cur = None
    if _d.get("active"):
        cur = {"show": _d["show"], "linger": _d["linger"], "step": _rt["step"],
               "minutes": _fmt_min(time.time() - run["started_at"]) if run else 0,
               "part": (_rt["seg"] or "") if run else "",
               "notes": list(run["notes"][-5:]) if run else [],
               "queue": _untested(), "teams": len(state.quiz_players)}
    rows = []
    base = {}
    for r in _d["results"]:
        if r.get("status") == "done" and r.get("linger") == 0:
            base[r["show"]] = r["minutes"]
    for r in _d["results"]:
        row = dict(r)
        b = base.get(r["show"])
        row["vs0"] = round(r["minutes"] - b, 1) if (b is not None and r.get("status") == "done") else None
        rows.append(row)
    return {"active": bool(_d.get("active")), "current": cur, "results": rows,
            "linger_steps": list(LINGER_STEPS)}


# ------------------------------------------------------------------ bots
def _bots_alive():
    p = _rt["proc"]
    if p is not None:
        return p.poll() is None
    pid = _d.get("bot_pid") or 0
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _start_bots():
    if _bots_alive():
        return
    try:
        log = open(_BOT_LOG, "a", encoding="utf-8")
        log.write(f"\n===== self-test bots {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
        log.flush()
        _rt["proc"] = subprocess.Popen(
            [sys.executable, "-u", os.path.join(_APP_DIR, "tools", "phone_bots.py"),
             "--teams", str(config.SELFTEST_BOT_TEAMS), "--skill", str(config.SELFTEST_BOT_SKILL),
             "--base", f"http://127.0.0.1:{config.WEB_REMOTE_PORT}", "--host", "", "--local-dir", _APP_DIR],
            stdout=log, stderr=subprocess.STDOUT, cwd=_APP_DIR, start_new_session=True)
        _d["bot_pid"] = _rt["proc"].pid
        _save()
        print(f"[SELFTEST] {config.SELFTEST_BOT_TEAMS} bot teams started (pid {_d['bot_pid']}, log {_BOT_LOG}).")
    except Exception as e:
        print(f"[SELFTEST] Couldn't start the bot teams: {e}")


def _stop_bots():
    pid = _rt["proc"].pid if _rt["proc"] is not None else (_d.get("bot_pid") or 0)
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)    # the bots mark themselves away, then exit
        except OSError:
            pass
    _rt["proc"] = None
    _d["bot_pid"] = 0


# ------------------------------------------------------------------ settings held for the test
def _hold_settings():
    from drivers import cliffhanger_engine
    if not _d.get("saved"):
        _d["saved"] = {"linger": state.autoadv_linger_pct, "auto_advance": bool(state.auto_advance_after_quiz),
                       "auto_dj": bool(state.auto_dj_enabled), "cliff_auto": bool(cliffhanger_engine.auto_host())}
    state.auto_advance_after_quiz = True
    state.auto_dj_enabled = True
    if not cliffhanger_engine.auto_host():
        cliffhanger_engine.set_auto_host(True)


def _restore_settings():
    from drivers import cliffhanger_engine, auto_dj_engine
    sv = _d.get("saved") or {}
    if sv:
        auto_dj_engine.set_linger(sv.get("linger", 0))
        state.auto_advance_after_quiz = sv.get("auto_advance", state.auto_advance_after_quiz)
        state.auto_dj_enabled = sv.get("auto_dj", state.auto_dj_enabled)
        if cliffhanger_engine.auto_host() != sv.get("cliff_auto", False):
            cliffhanger_engine.set_auto_host(sv.get("cliff_auto", False))
    _d["saved"] = {}


# ------------------------------------------------------------------ queue
def _tested(name):
    return any(r.get("show") == name and r.get("status") == "done" for r in _d["results"])


def _untested():
    from drivers import show_library
    names = sorted((s.get("name") or "" for s in show_library.all_shows(order="created")), key=str.lower)
    return [n for n in names if n and n != _d["show"] and not _tested(n)]


def _next_untested():
    left = _untested()
    return left[0] if left else None


# ------------------------------------------------------------------ run lifecycle
def _note(msg):
    print(f"[SELFTEST] {msg}")
    run = _rt["run"]
    if run is not None:
        run["notes"].append(f"{_fmt_min(time.time() - run['started_at'])}m: {msg}")


def _begin(show_name):
    from drivers import show_library
    _load()
    if not show_library.get(show_name):
        print(f"[SELFTEST] No show called {show_name!r}.")
        return
    if _d.get("active"):
        _close_run("stopped", "restarted from the Setup page")
    _d.update(active=True, show=show_name, linger=LINGER_STEPS[0], retry=0)
    _hold_settings()
    _save()
    _start_bots()
    print(f"[SELFTEST] Started: '{show_name}' first, then every untested show; linger "
          f"{'/'.join(str(x) for x in LINGER_STEPS)}%.")
    _close_show()


def _close_show():
    """Closes whatever is running, then the next run launches."""
    from drivers import show_engine
    if state.show_phase != "setup" or state.show_background:
        show_engine.back_to_menu()
    _rt["step"] = "launch"
    _rt["at"] = time.time() + config.SELFTEST_GAP_SECONDS
    _rt["run"] = None


def _launch(now):
    from drivers import show_engine, auto_dj_engine
    _hold_settings()
    auto_dj_engine.set_linger(_d["linger"])
    if not show_engine.start_show(_d["show"]):
        _rt["tries"] += 1
        if _rt["tries"] >= 4:
            print(f"[SELFTEST] '{_d['show']}' won't start (phase {state.show_phase}) -- going back to the menu.")
            _rt["tries"] = 0
            _close_show()
        else:
            _rt["at"] = now + 5
        return
    _rt["tries"] = 0
    show_engine.begin_intro()
    _rt["run"] = {"show": _d["show"], "linger": _d["linger"], "started_at": now, "segments": [],
                  "notes": [], "tc0": state.transition_count, "intro_wait_at": 0.0}
    _rt.update(step="intro", seg=None, seg_at=now, sig=None, sig_at=now, stalls=0, solo_wait=0.0)
    print(f"[SELFTEST] Run: '{_d['show']}' at {_d['linger']}% linger "
          f"({len(state.quiz_players)} teams).")


def _segment_key():
    if state.show_phase == "intro":
        return "Intro"
    if state.show_phase == "outro":
        return "Outro"
    if state.intermission_active:
        return f"Intermission {state.round_index}"
    if state.win_sequence_active or state.game_winner_player_id:
        return f"R{state.round_index}"          # the celebration counts with the round just won
    names = {"trivia": "Trivia", "wait_how_much": "Wait How Much", "fastest_finger": "Fastest Finger",
             "solo_challenge": state.round_solo_game.title() if state.round_solo_game else "Solo"}
    return f"R{state.round_index + 1} {names.get(state.round_type, state.round_type)}"


def _flush_segment(now):
    """Adds the time spent in the current part to the run's list (a round's win celebration, keyed
    "R<n>", folds into that round's "R<n> <type>" entry)."""
    run, key = _rt["run"], _rt["seg"]
    if key is None:
        return
    segs = run["segments"]
    sec = now - _rt["seg_at"]
    if segs and (segs[-1]["label"] == key or segs[-1]["label"].startswith(key + " ")):
        segs[-1]["sec"] += sec
    else:
        segs.append({"label": key, "sec": sec})
    _rt["seg_at"] = now


def _track_segment(now):
    key = _segment_key()
    if key != _rt["seg"]:
        _flush_segment(now)
        _rt["seg"], _rt["seg_at"] = key, now


def _close_run(status, note=""):
    run = _rt["run"]
    if run is None:
        return
    now = time.time()
    _flush_segment(now)
    if note:
        _note(note)
    total = now - run["started_at"]
    row = {"show": run["show"], "linger": run["linger"], "minutes": _fmt_min(total), "status": status,
           "finished": time.strftime("%Y-%m-%d %H:%M"),
           "parts": [{"label": s["label"], "min": _fmt_min(s["sec"])} for s in run["segments"] if s["sec"] >= 5],
           "songs": max(0, state.transition_count - run["tc0"]),
           "rounds": len(state.round_results), "winner": state.show_outro_message or "",
           "notes": run["notes"][-12:]}
    _d["results"].append(row)
    del _d["results"][:-200]
    _save()
    _rt["run"] = None
    print(f"[SELFTEST] Run over ({status}): '{row['show']}' at {row['linger']}% linger -- "
          f"{row['minutes']} min, {row['songs']} songs. " + "; ".join(f"{p['label']} {p['min']}" for p in row["parts"]))


def _advance():
    """The run finished: next linger step, else the next untested show, else done."""
    _d["retry"] = 0
    steps = list(LINGER_STEPS)
    i = steps.index(_d["linger"]) if _d["linger"] in steps else len(steps) - 1
    if i + 1 < len(steps):
        _d["linger"] = steps[i + 1]
    else:
        nxt = _next_untested()
        if not nxt:
            _finish("every show tested")
            return
        _d["show"], _d["linger"] = nxt, steps[0]
        print(f"[SELFTEST] Next untested show: '{nxt}'.")
    _save()
    _close_show()


def _finish(why):
    from drivers import show_engine
    print(f"[SELFTEST] Finished -- {why}.")
    _stop_bots()
    _restore_settings()
    _d["active"] = False
    _rt["step"] = ""
    _save()
    if state.show_phase != "setup":
        show_engine.back_to_menu()


# ------------------------------------------------------------------ Cliffhanger demo
def _cliff_index():
    for i, r in enumerate(state.rounds or []):
        if (r or {}).get("type") == "cliffhanger":
            return i
    return None


def _start_cliff_demo():
    from drivers import round_engine, cliffhanger_engine
    idx = _cliff_index()
    if idx is None:
        print("[SELFTEST] This show has no Cliffhanger round -- no demo.")
        return
    run = _rt["run"]
    run["demo"] = idx
    _note("interrupted for a Cliffhanger demo (this run will be played again)")
    if cliffhanger_engine.auto_host():
        cliffhanger_engine.set_auto_host(False)


def _update_cliff_demo(now):
    """Waits for the break after the interrupted round, then starts the Cliffhanger at once. The
    watchdog stays off while the host runs it."""
    from drivers import win_sequence_engine, round_engine, medley_engine
    idx = _rt["run"]["demo"]
    if (state.show_phase == "live" and state.round_index != idx and not state.intermission_active
            and not (state.win_sequence_active or state.game_winner_player_id)):
        if state.medley_active:
            medley_engine.stop("Cliffhanger demo")      # ends a Fastest Finger round with its leader
        else:
            round_engine.end_round("operator")
        return
    if state.intermission_active and state.round_index != idx:
        state.round_index = idx
        win_sequence_engine._end_intermission("Cliffhanger demo")
        print(f"[SELFTEST] Cliffhanger demo: round {idx + 1} now.")


# ------------------------------------------------------------------ watchdog
def _signature():
    from drivers import solo_challenge
    return (state.show_phase, state.round_index, bool(state.intermission_active), state.transition_count,
            state.medley_pos, state.round_special_count, bool(solo_challenge.active()),
            bool(state.win_sequence_active), int(state.show_intro_waiting))


def _watchdog(now):
    from drivers import round_engine, deck_orchestrator, solo_challenge
    from drivers import medley_engine
    # Fastest Finger's vocal-drop bed holds for the host's panel button (2026-11-23: the first run sat
    # there). Play the host: let the winner screen stand a moment, then end the bed.
    if medley_engine.in_bed_hold():
        if not _rt.get("bed_at"):
            _rt["bed_at"] = now
        elif now - _rt["bed_at"] >= config.SELFTEST_BED_HOLD_SECONDS:
            _rt["bed_at"] = 0.0
            _note("Fastest Finger bed: pressed the host's button")
            medley_engine.end_bed("self test")
        return
    _rt["bed_at"] = 0.0
    # A solo round that waits for the host to pick a team / game: nobody will. End it.
    if (state.show_phase == "live" and state.round_type == "solo_challenge"
            and state.round_solo_game != "cliffhanger" and not solo_challenge.active()
            and not (state.intermission_active or state.win_sequence_active or state.game_winner_player_id)):
        if not _rt["solo_wait"]:
            _rt["solo_wait"] = now
        elif now - _rt["solo_wait"] >= config.SELFTEST_SOLO_WAIT_SECONDS:
            _rt["solo_wait"] = 0.0
            _note(f"{state.round_name}: a solo round needs the host -- ended it")
            round_engine.end_round("selftest")
        return
    _rt["solo_wait"] = 0.0
    sig = _signature()
    if sig != _rt["sig"]:
        _rt["sig"], _rt["sig_at"], _rt["stalls"] = sig, now, 0
        return
    if now - _rt["sig_at"] < config.SELFTEST_STALL_SECONDS:
        return
    _rt["stalls"] += 1
    _rt["sig_at"] = now
    where = (f"phase {state.show_phase}, {_segment_key()}, mode {state.mode}, song "
             f"{os.path.basename(state.now_playing_path or '') or '-'}")
    if _rt["stalls"] == 1:
        _note(f"HANG? nothing moved for {config.SELFTEST_STALL_SECONDS / 60:.0f} min ({where}) -- next song")
        deck_orchestrator.trigger_track_move("next")
    elif _rt["stalls"] == 2 and state.show_phase == "live":
        _note(f"HANG: still stuck ({where}) -- ending the round")
        round_engine.end_round("selftest")
    else:
        _note(f"HANG: stuck for good ({where})")
        _close_run("hung")
        if _d.get("retry", 0) < 1:
            _d["retry"] = _d.get("retry", 0) + 1
            print("[SELFTEST] Starting that run again.")
            _save()
            _close_show()
        else:
            _advance()


# ------------------------------------------------------------------ frame
def update(now):
    _load()
    req, _rt["request"] = _rt["request"], None
    if req:
        if req[0] == "start":
            _begin(req[1])
        elif req[0] == "cliff_demo" and _d.get("active") and _rt["run"] is not None:
            _start_cliff_demo()
        elif req[0] == "stop" and _d.get("active"):
            _close_run("stopped", "stopped from the Setup page")
            _finish("stopped from the Setup page")
    if not _d.get("active"):
        return
    step = _rt["step"]
    if step == "resume":
        if now >= _rt["at"]:
            _hold_settings()
            _start_bots()
            _close_show()
        return
    if step == "launch":
        if now >= _rt["at"] and state.show_phase == "setup":
            _launch(now)
        elif now >= _rt["at"] + 30:
            _close_show()           # something put a show back up meanwhile: close it again
        return
    run = _rt["run"]
    if run is None:
        return
    if not _bots_alive() and now - run["started_at"] > 10:
        _note("bot teams had stopped -- started them again")
        _start_bots()
    if state.show_background or state.show_phase in ("setup", "dark"):
        if state.show_background and run.get("demo") is not None:
            from drivers import cliffhanger_engine
            _close_run("demo", "Cliffhanger demo over -- playing this run again")
            cliffhanger_engine.set_auto_host(True)
            _close_show()
        elif state.show_background:
            _close_run("done")
            _advance()
        else:
            _close_run("aborted", f"the show left the stage (phase {state.show_phase})")
            _close_show()
        return
    _track_segment(now)
    if step == "intro":
        if state.show_phase == "intro" and state.show_intro_waiting:
            if not run["intro_wait_at"]:
                run["intro_wait_at"] = now
            elif now - run["intro_wait_at"] >= config.SELFTEST_INTRO_PAUSE_SECONDS:
                from drivers import show_engine
                show_engine.skip_intro()
                _rt["step"] = "running"
        elif state.show_phase in ("live", "outro"):
            _rt["step"] = "running"        # a show with no intro goes straight in
    if run.get("demo") is not None:
        _update_cliff_demo(now)
        return
    if now - run["started_at"] > config.SELFTEST_MAX_RUN_SECONDS:
        _close_run("hung", f"over {config.SELFTEST_MAX_RUN_SECONDS / 3600:.0f} h")
        _advance()
        return
    _watchdog(now)
