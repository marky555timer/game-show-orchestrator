"""
End-of-night results archive (2026-11-16).

Every show night gets a session id (state.show_session_id). When the outro
starts, snapshot() freezes the final standings and each team's answers into
results/<session>.json, so a phone can keep showing "Final Scores" and
"View answers" after the next show starts or the app restarts.

The shareable board is public (names + emoji + totals, no player ids); a
team's own answers are only returned when its player id is supplied.
Old files are deleted after RESULTS_KEEP_DAYS.
"""
import json
import os
import re
import sys
import threading
import time
import uuid

import config
from state import state
from drivers import player_profile

_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")
_ID_RE = re.compile(r"^[a-z0-9]{6,32}$")
KEEP_DAYS = getattr(config, "RESULTS_KEEP_DAYS", 30)


def new_session_id():
    return uuid.uuid4().hex[:10]


def valid_id(session_id):
    return bool(session_id) and bool(_ID_RE.match(session_id))


def _path(session_id):
    return os.path.join(_DIR, session_id + ".json")


def snapshot():
    """Freeze tonight's results. Safe to call twice (it just rewrites the file)."""
    if not state.show_session_id:
        state.show_session_id = new_session_id()
    sid = state.show_session_id

    def total(pid, p):
        return state.show_cumulative_scores.get(pid, 0) + p.get("score", 0)

    ranked = sorted(state.quiz_players.items(), key=lambda kv: total(*kv), reverse=True)
    # Teams that never answered anything and have no points aren't worth listing.
    ranked = [(pid, p) for pid, p in ranked if total(pid, p) > 0 or p.get("asked", 0) > 0]
    questions = [{
        "n": h["n"], "question": h["question"], "category": h.get("category", ""),
        "choices": h["choices"], "correct_index": h["correct_index"],
    } for h in state.quiz_history]
    players = {}
    for i, (pid, p) in enumerate(ranked):
        players[pid] = {
            "rank": i + 1,
            "name": p.get("name") or p["initials"],
            "emoji": p.get("emoji") or player_profile.DEFAULT_EMOJI,
            "score": total(pid, p),
            "correct": p.get("correct", 0), "asked": p.get("asked", 0),
            "best_streak": p.get("best_streak", 0),
            "picks": {str(h["n"]): h["picks"].get(pid) for h in state.quiz_history},
        }
    data = {
        "session": sid, "ended_at": time.time(), "show": state.show_selected or "",
        "questions": questions, "players": players,
    }
    try:
        os.makedirs(_DIR, exist_ok=True)
        tmp = _path(sid) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, _path(sid))
        print(f"[RESULTS] Saved {sid}: {len(players)} teams, {len(questions)} questions.")
    except OSError as exc:
        print(f"[RESULTS] Could not save {sid}: {exc}")
        return None
    cleanup()
    from drivers import selftest_engine
    if selftest_engine.active():
        print(f"[RESULTS] {sid}: self-test show -- not uploaded to the site.")
        return sid
    _upload_async(sid, data)
    return sid


# ------------------------------------------------ permanent copy on triviacade.com
def _uploaded_marker(session_id):
    return _path(session_id) + ".up"


def site_link(session_id, player_id=""):
    """The permanent triviacade.com address of a night's results ("" until the
    copy has actually been uploaded)."""
    if not (valid_id(session_id) and getattr(config, "RESULTS_PUBLIC_URL", "")
            and os.path.exists(_uploaded_marker(session_id))):
        return ""
    link = f"{config.RESULTS_PUBLIC_URL}?r={session_id}"
    if player_id and re.match(r"^[0-9a-f]{32}$", player_id):
        link += f"&p={player_id}"
    return link


def _upload_async(session_id, data):
    # Only the real show machines talk to the site (not the PC simulator), and
    # not a backup Pi that isn't running the show.
    if not (sys.platform.startswith("linux") and getattr(config, "RESULTS_PUBLIC_URL", "")):
        return
    threading.Thread(target=_upload, args=(session_id, data), daemon=True,
                     name="results-upload").start()


def _upload(session_id, data):
    try:
        import requests
        from drivers import failover_engine
        if not failover_engine.may_publish_tunnel():
            return
    except Exception:
        return
    # WiFi at a venue can be flaky: a few tries over about ten minutes.
    for delay in (0, 15, 60, 180, 360):
        time.sleep(delay)
        try:
            r = requests.post(config.RESULTS_PUBLIC_URL,
                              json={"secret": config.TUNNEL_REDIRECT_SECRET, "results": data}, timeout=10)
            if r.status_code == 200 and (r.json() or {}).get("ok"):
                try:
                    os.makedirs(_DIR, exist_ok=True)
                    with open(_uploaded_marker(session_id), "w") as f:
                        f.write(str(time.time()))
                except OSError as exc:
                    print(f"[RESULTS] {session_id} uploaded, but the marker could not be written: {exc}")
                else:
                    print(f"[RESULTS] {session_id} is on triviacade.com.")
                return   # the site has it: never retry
            print(f"[RESULTS] upload of {session_id}: HTTP {r.status_code}")
            if r.status_code in (400, 403, 404, 413):
                return   # retrying can't fix these
        except Exception as exc:
            print(f"[RESULTS] upload of {session_id} failed: {exc}")
    print(f"[RESULTS] gave up uploading {session_id}.")


def load(session_id):
    if not valid_id(session_id):
        return None
    try:
        with open(_path(session_id), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def public_view(session_id, player_id=""):
    """The board for anyone with the link, plus the caller's own tally + answers."""
    data = load(session_id)
    if data is None:
        return None
    players = data.get("players", {})
    board = [{"rank": p["rank"], "name": p["name"], "emoji": p["emoji"], "score": p["score"],
              "me": pid == player_id}
             for pid, p in sorted(players.items(), key=lambda kv: kv[1]["rank"])][:30]
    out = {"ok": True, "session": data["session"], "ended_at": data.get("ended_at", 0),
           "link": site_link(session_id), "my_link": site_link(session_id, player_id),
           "teams": len(players), "asked": len(data.get("questions", [])), "board": board,
           "mine": None}
    me = players.get(player_id) if player_id else None
    if me:
        answers = []
        for q in data["questions"]:
            pick = me["picks"].get(str(q["n"]))
            ch, ci = q["choices"], q["correct_index"]
            answers.append({
                "n": q["n"], "question": q["question"], "category": q.get("category", ""),
                "correct": ch[ci] if 0 <= ci < len(ch) else "",
                "yours": ch[pick] if pick is not None and 0 <= pick < len(ch) else None,
                "right": pick is not None and pick == ci,
            })
        asked = len(answers)
        right = sum(1 for a in answers if a["right"])
        out["mine"] = {
            "rank": me["rank"], "score": me["score"], "name": me["name"], "emoji": me["emoji"],
            "asked": asked, "correct": right,
            "percent": round(100.0 * right / asked) if asked else 0,
            "best_streak": me.get("best_streak", 0), "answers": answers,
        }
    return out


def cleanup():
    cutoff = time.time() - KEEP_DAYS * 86400
    try:
        for name in os.listdir(_DIR):
            fp = os.path.join(_DIR, name)
            if name.endswith((".json", ".up")) and os.path.getmtime(fp) < cutoff:
                os.remove(fp)
    except OSError:
        pass
