"""drivers/medley_game.py
"This is <artist>" true/false game layered on Dance Medley mode.

Each hook gets one statement: "This is <Artist>". Across a medley exactly half
(rounded) are TRUE; a FALSE statement names the most plausible decoy artist
(same genre / era / energy / tempo neighborhood as the real one). The arcade
panel (green = TRUE, red = FALSE) and players on the web page answer with ONE
tap, which locks the answer; answers open after a short listen window
(MEDLEY_LISTEN_SECONDS) and close when the hook's segment ends.
Each correct answer is +1 point, and the FIRST correct answer to each
statement (fastest finger) +1 more -- added to the player's regular score as
a bonus and to a medley tally; ties on the tally go to the quicker total
answer time. When the medley ends the leaderboard shows on
the main screen under a "N total songs" headline.

The whole game is OFF whenever the master "suppress questions" flag
(state.questions_suppressed) is set, or the operator has switched it off
(state.medley_game_enabled): a medley then plays exactly as before.
"""
import random
import re
import threading
import time

import config
from state import state

_lock = threading.RLock()
_game = None  # dict while a medley's game is running, else None
_profiles = {"built": False, "data": {}}

PANEL_ID = "PANEL"


def enabled():
    return bool(state.medley_game_enabled and not state.questions_suppressed)


# --------------------------------------------------------------------------
# names + decoys
# --------------------------------------------------------------------------
def primary_artist(artist):
    """'Pitbull, AFROJACK, Ne-Yo' -> 'Pitbull'; keeps names that contain a
    comma on purpose ('Earth, Wind & Fire')."""
    a = str(artist or "").strip()
    if not a:
        return ""
    if "," in a and "&" not in a:
        a = a.split(",")[0]
    a = re.split(r"\s*(?:;|\(|\bfeat\.?\b|\bft\.?\b|\bfeaturing\b)", a, maxsplit=1, flags=re.I)[0]
    return a.strip(" -")


def _decade_num(label):
    m = re.match(r"^(\d{4})s$", str(label or "").strip())
    return int(m.group(1)) if m else None


def _build_profiles():
    """primary-artist (lowercase) -> {name, genre, decade, energy, bpm}."""
    from drivers.music_library import music_library, sanitize_track_key
    from drivers import music_metadata_engine as mme
    import json
    import os
    try:
        ana = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                          config.MEDLEY_ANALYSIS_FILE), encoding="utf-8"))
    except Exception:
        ana = {}
    out = {}
    for t in music_library.all_tracks():
        name = primary_artist(t["artist"])
        if not name or name.lower() in ("unknown artist", "various artists", "n/a"):
            continue
        meta = mme.get_metadata_for(sanitize_track_key(t["title"], t["artist"])) or {}
        a = ana.get(os.path.basename(t["path"]), {})
        bpm = None
        if a.get("hooks"):
            bpm = a["hooks"][0].get("bpm", a.get("bpm"))
        prof = out.setdefault(name.lower(), {"name": name, "genre": meta.get("genre"),
                                             "decade": _decade_num(meta.get("decade")),
                                             "energy": int(meta.get("energy_rank") or 0), "bpm": bpm})
        if prof["genre"] is None and meta.get("genre"):
            prof["genre"] = meta.get("genre")
    return out


def _profile_table():
    with _lock:
        if not _profiles["built"]:
            try:
                _profiles["data"] = _build_profiles()
            except Exception as e:
                print(f"[MEDLEY GAME] Could not build artist profiles: {e}")
                _profiles["data"] = {}
            _profiles["built"] = True
        return _profiles["data"]


def _plausibility(real, cand):
    """Higher = a more convincing wrong answer for `real`."""
    s = 0.0
    if real.get("genre") and cand.get("genre") == real["genre"]:
        s += 3.0
    rd, cd = real.get("decade"), cand.get("decade")
    if rd and cd:
        gap = abs(rd - cd)
        s += 2.5 if gap == 0 else 1.2 if gap == 10 else 0.0
    if abs((real.get("energy") or 0) - (cand.get("energy") or 0)) <= 1:
        s += 1.0
    rb, cb = real.get("bpm"), cand.get("bpm")
    if rb and cb:
        s += 1.5 if abs(rb - cb) <= 8 else 0.5 if abs(rb - cb) <= 18 else 0.0
    return s


def pick_decoy(seg, real_name, avoid, rng):
    """The most plausible wrong artist for this hook (a little randomness among
    the top few so repeats vary)."""
    from drivers import music_metadata_engine as mme
    table = _profile_table()
    meta = mme.get_metadata_for(seg.get("key", "")) or {}
    real = {"genre": meta.get("genre"), "decade": _decade_num(meta.get("decade")),
            "energy": int(seg.get("energy") or meta.get("energy_rank") or 0), "bpm": seg.get("tempo")}
    rl = real_name.lower()
    scored = []
    for low, cand in table.items():
        if low == rl or rl in low or low in rl or low in avoid:
            continue
        scored.append((_plausibility(real, cand) + rng.random() * 0.4, cand["name"]))
    if not scored:
        return None
    scored.sort(reverse=True)
    top = scored[:3]
    return rng.choice(top)[1] if rng.random() < 0.3 else top[0][1]


# --------------------------------------------------------------------------
# game lifecycle
# --------------------------------------------------------------------------
def new_game(plan, seed=None):
    """Called when a medley (re)starts."""
    global _game
    with _lock:
        state.medley_q = None
        state.medley_reveal = None
        state.medley_scores = {}
        state.medley_results = []
        state.medley_results_until = 0.0
        state.medley_songs_total = len(plan)
        n = len(plan)
        rng = random.Random(seed)
        truths = [True] * ((n + 1) // 2) + [False] * (n // 2)
        for _ in range(200):
            rng.shuffle(truths)
            run = best = 1
            for a, b in zip(truths, truths[1:]):
                run = run + 1 if a == b else 1
                best = max(best, run)
            if best <= 2:   # never more than two TRUE/FALSE in a row
                break
        # artists who actually appear in this medley are never used as decoys
        _game = {"truths": truths, "rng": rng, "qid": 0, "active": False,
                 "used_decoys": {primary_artist(s["artist"]).lower() for s in plan}}


def _grade_current(reveal):
    """Close out the open question (caller holds _lock)."""
    q = state.medley_q
    if q is None:
        return
    state.medley_q = None
    took = dict(q["times"])
    marks = {pid: ans == q["is_true"] for pid, ans in q["answers"].items()}
    if q["panel"] is not None:
        marks[PANEL_ID] = q["panel"] == q["is_true"]
    # fastest finger: the first correct answer earns one extra point
    right = [pid for pid, ok in marks.items() if ok]
    first = min(right, key=lambda pid: took.get(pid, 1e9)) if right else None
    for pid, ok in marks.items():
        _credit(pid, ok, took.get(pid, 0.0), bonus=pid == first)
    if first is not None:
        print(f"[MEDLEY GAME] Fastest finger: {state.medley_scores[first]['initials']} "
              f"({took.get(first, 0.0):.1f}s, +1 bonus).")
    if reveal:
        state.medley_reveal = {"qid": q["qid"], "shown": q["shown"], "real": q["real"], "was_true": q["is_true"],
                               "panel": q["panel"], "answers": dict(q["answers"]), "first": first,
                               "until": time.time() + config.MEDLEY_REVEAL_SECONDS}


def _credit(pid, correct, took=0.0, bonus=False):
    """`took` = seconds from the statement going up to the tap; summed over
    correct answers it breaks ties on the leaderboard (quicker wins)."""
    row = state.medley_scores.setdefault(
        pid, {"initials": "PNL" if pid == PANEL_ID else (state.quiz_players.get(pid, {}).get("initials", "???")),
              "points": 0, "answered": 0, "time": 0.0})
    row["answered"] += 1
    if correct:
        pts = 2 if bonus else 1
        row["points"] += pts
        row["time"] += took
        if pid != PANEL_ID and pid in state.quiz_players:
            state.quiz_players[pid]["score"] += pts  # bonus point(s) on the regular scoreboard


def begin_hook(i, seg, length):
    """A hook just started: grade the previous question, open a new one that
    stays answerable for this hook's whole segment."""
    with _lock:
        g = _game
        if g is None:
            return
        _grade_current(reveal=True)
        if not enabled():
            g["active"] = False
            return
        real = primary_artist(seg["artist"])
        if not real:
            g["active"] = False
            return
        is_true = g["truths"][i] if i < len(g["truths"]) else g["rng"].random() < 0.5
        shown = real
        if not is_true:
            decoy = pick_decoy(seg, real, g["used_decoys"], g["rng"])
            if decoy:
                shown = decoy
                g["used_decoys"].add(decoy.lower())
            else:
                is_true = True  # no plausible decoy available: fall back to a true statement
        g["qid"] += 1
        g["active"] = True
        now = time.time()
        # listen first: answers open after MEDLEY_LISTEN_SECONDS, and not
        # before the previous answer's reveal has left the LEDs
        opens = now + config.MEDLEY_LISTEN_SECONDS
        if state.medley_reveal is not None:
            opens = max(opens, state.medley_reveal["until"])
        state.medley_q = {"qid": g["qid"], "idx": i, "shown": shown, "real": real, "is_true": is_true,
                          "deadline": now + length, "started": now, "opens": min(opens, now + length),
                          "answers": {}, "panel": None, "times": {}}


def pause():
    """Transition editor opened: drop the open question without scoring."""
    with _lock:
        state.medley_q = None
        state.medley_reveal = None


def close_question():
    """Vocal drop: the last hook is over musically -- grade and reveal it now
    rather than letting it stay open through the bed."""
    with _lock:
        if _game is not None:
            _grade_current(reveal=True)


def finish(show_results=True, hold=False):
    """Medley over: grade the last question and (optionally) queue the
    main-screen results. hold=True keeps them up until the medley engine
    clears them (vocal-drop bed: until the host presses a panel button)."""
    global _game
    with _lock:
        if _game is None:
            return
        _grade_current(reveal=False)
        # ties: the quicker total answer time on correct answers wins
        rows = sorted(state.medley_scores.values(), key=lambda r: (-r["points"], r["time"], r["initials"]))
        if show_results and rows:
            state.medley_results = [{"initials": r["initials"], "points": r["points"]} for r in rows[:8]]
            state.medley_results_until = float("inf") if hold else time.time() + config.MEDLEY_RESULTS_SECONDS
        state.medley_q = None
        state.medley_reveal = None
        _game = None


# --------------------------------------------------------------------------
# answering
# --------------------------------------------------------------------------
def active():
    q = state.medley_q
    return enabled() and q is not None and time.time() < q["deadline"]


def answer_panel(value):
    """Arcade panel: green = TRUE, red = FALSE. First tap counts."""
    with _lock:
        q = state.medley_q
        if not enabled() or q is None or time.time() >= q["deadline"] or q["panel"] is not None:
            return False
        if time.time() < q["opens"]:
            return False  # still in the listen window: ignored, not locked in
        if bool(value) == q["is_true"] and not any(a == q["is_true"] for a in q["answers"].values()):
            q["first"] = PANEL_ID
        q["panel"] = bool(value)
        q["times"][PANEL_ID] = time.time() - q["opens"]
        return True


def answer_player(pid, qid, value):
    with _lock:
        q = state.medley_q
        if not enabled() or q is None or time.time() >= q["deadline"]:
            return {"ok": False, "reason": "closed"}
        if qid is not None and int(qid) != q["qid"]:
            return {"ok": False, "reason": "stale"}
        if pid not in state.quiz_players:
            return {"ok": False, "reason": "unknown player"}
        if pid in q["answers"]:
            # a phone's retry of an answer that already landed (first tap counts)
            return {"ok": True, "first": q.get("first") == pid}
        if time.time() < q["opens"]:
            return {"ok": False, "reason": "listen first"}
        # Fastest finger is final the moment the first right answer arrives
        # (same order _grade_current() ranks by), so tell that phone now.
        first = (bool(value) == q["is_true"] and not any(a == q["is_true"] for a in q["answers"].values())
                 and q["panel"] != q["is_true"])
        q["answers"][pid] = bool(value)
        q["times"][pid] = time.time() - q["opens"]
        if first:
            q["first"] = pid
        return {"ok": True, "first": first}


def player_view(pid):
    """What a phone needs: the open statement (with its answer, so the phone
    can reveal the instant the hook ends -- 2026-10-18), its own tap, the
    last reveal, and the results screen."""
    now = time.time()
    q = state.medley_q
    rv = state.medley_reveal
    out = {"enabled": enabled(), "active": False, "reveal": None, "results": None,
           "total_songs": state.medley_songs_total}
    if q is not None and enabled() and now < q["deadline"]:
        out.update({"active": True, "qid": q["qid"], "artist": q["shown"],
                    "remaining_seconds": max(0.0, q["deadline"] - now),
                    "total_seconds": max(1.0, q["deadline"] - q["started"]),
                    "answered": q["answers"].get(pid), "index": q["idx"] + 1,
                    "opens_in": max(0.0, q["opens"] - now),
                    # snappy answers: the phone reveals on its own at deadline_at
                    "opens_at": q["opens"], "deadline_at": q["deadline"],
                    "is_true": q["is_true"], "real": q["real"],
                    "my_first": q.get("first") == pid, "reveal_seconds": config.MEDLEY_REVEAL_SECONDS})
    if rv is not None and now < rv["until"]:
        mine = rv["answers"].get(pid)
        out["reveal"] = {"qid": rv["qid"], "shown": rv["shown"], "real": rv["real"], "was_true": rv["was_true"],
                         "you": mine, "correct": (None if mine is None else mine == rv["was_true"]),
                         "first": rv.get("first") == pid}
    if state.medley_results and now < state.medley_results_until:
        out["results"] = list(state.medley_results)
    row = state.medley_scores.get(pid)
    out["points"] = row["points"] if row else 0
    return out
