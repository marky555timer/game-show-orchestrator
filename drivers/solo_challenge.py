"""drivers/solo_challenge.py
Solo challenges (2026-11-20, docs/rounds_design.md build step 4).

One player takes over the device for their team. ALL OR NOTHING: win = +SOLO_STAKES to the team's
NIGHT score, lose = -SOLO_STAKES (the night score never goes below 0). The host picks the team and
the challenge on the admin page (Rounds card -> Solo challenge) and starts it; phones show
"<Team> is taking the challenge!" and then the result.

A challenge can be launched any time the show is live and idle (DJ mode, no question / medley /
price game up), including during an intermission. When the running ROUND is a `solo_challenge`
round (drivers/round_engine.py), the result also ends that round (straight to the intermission, or
the outro after the last round).

Challenges are a registry (CHALLENGES) so Cliffhanger (step 5) and Space Invaders (later) are one
entry each. Simon: the existing hardware Simon game (drivers/simon_engine.py); clearing
config.SOLO_SIMON_WIN_ROUNDS rounds wins, a miss or timing out loses.

The stakes go into state.show_cumulative_scores (the night tally), not the round score, so they can
never trigger a trivia round's goal.
"""
import time

import config
from state import state

CHALLENGES = {
    "simon": {"name": "Tipsy Simon", "available": True,
              "rule": f"clear {config.SOLO_SIMON_WIN_ROUNDS} rounds"},
    "cliffhanger": {"name": "Cliffhanger", "available": True,
                    "rule": f"guess the year of {config.CLIFF_SONGS} songs -- over {config.CLIFF_SPACES} years off in total and he falls"},
}

# phase: "" (idle) | "playing" | "result"
_s = {"phase": "", "challenge": "", "player_id": "", "team": "", "result": "", "delta": 0,
      "started_at": 0.0, "result_until": 0.0, "won": False, "detail": ""}


def active():
    """A challenge is being played (its result screen counts too)."""
    return _s["phase"] in ("playing", "result")


def playing():
    return _s["phase"] == "playing"


def _team_name(pid):
    p = state.quiz_players.get(pid) or {}
    return p.get("name") or p.get("initials") or "???"


def _idle_reason():
    if state.show_phase != "live":
        return "No show is live."
    if active():
        return "A challenge is already running."
    if state.win_sequence_active:
        return "A round is ending -- wait for the intermission."
    if state.mode != state.MODE_DJ:
        return "Something is on screen (a question or a game) -- wait for it to finish."
    if state.medley_active or state.price_game_active or state.mystery_active:
        return "A medley / price game / question is running -- wait for it to finish."
    return None


def start(challenge, player_id):
    """Admin START CHALLENGE. Returns (ok, message)."""
    spec = CHALLENGES.get(challenge)
    if not spec or not spec["available"]:
        return False, f"{(spec or {}).get('name', challenge)} isn't available yet."
    if player_id not in state.quiz_players:
        return False, "Pick a team that is playing tonight."
    why = _idle_reason()
    if why:
        return False, why
    team = _team_name(player_id)
    _s.update(phase="playing", challenge=challenge, player_id=player_id, team=team, result="", delta=0,
              started_at=time.time(), result_until=0.0, won=False, detail="")
    print(f"[SOLO] {team} takes the {spec['name']} challenge for +/-{config.SOLO_STAKES}.")
    if challenge == "simon":
        from drivers import simon_engine
        state.trivia_confirm_active = False
        simon_engine.enter_simon_hardware()
    elif challenge == "cliffhanger":
        from drivers import cliffhanger_engine
        ok, why = cliffhanger_engine.start()
        if not ok:
            _s["phase"] = ""
            return False, why
    state.set_message(f"{team.upper()} TAKES THE CHALLENGE!", 3.0)
    return True, f"{team} is playing {spec['name']} for {config.SOLO_STAKES} points."


def cancel():
    """Admin CANCEL: stop the challenge with no points either way."""
    if not playing():
        return False
    print(f"[SOLO] {_s['team']}'s challenge cancelled by the host -- no points change.")
    _s["phase"] = ""
    if _s["challenge"] == "simon" and state.mode == state.MODE_SIMON:
        from drivers import simon_engine
        simon_engine.exit_simon()
    if _s["challenge"] == "cliffhanger":
        from drivers import cliffhanger_engine
        cliffhanger_engine.cancel()
    state.set_message("CHALLENGE CANCELLED", 2.0)
    return True


def apply_stakes(player_id, delta):
    """Adds `delta` to the team's NIGHT score (cumulative + round), never below 0. Returns the change
    actually applied."""
    player = state.quiz_players.get(player_id) or {}
    round_pts = int(player.get("score", 0))
    cum = int(state.show_cumulative_scores.get(player_id, 0))
    new_cum = max(-round_pts, cum + int(delta))
    state.show_cumulative_scores[player_id] = new_cum
    return new_cum - cum


def _finish(won, detail=""):
    if not playing():
        return
    delta = apply_stakes(_s["player_id"], config.SOLO_STAKES if won else -config.SOLO_STAKES)
    _s.update(phase="result", won=won, delta=delta, detail=detail, result="win" if won else "lose",
              result_until=time.time() + config.SOLO_RESULT_SECONDS)
    team = _s["team"]
    print(f"[SOLO] {team} {'WON' if won else 'lost'} {_s['challenge']} ({detail}) -- night score {delta:+d}.")
    if won:
        try:
            from audio.audio_engine import load_sound, play_processed_sound
            play_processed_sound(load_sound(config.resource_path("audio", "sound_effects", "bigWin.wav")), volume=1.0)
        except Exception as e:
            print(f"[SOLO] bigWin.wav failed: {e}")
        state.set_message(f"{team.upper()} WINS {config.SOLO_STAKES}!", config.SOLO_RESULT_SECONDS)
    else:
        state.set_message(f"{team.upper()} LOSES {abs(delta)}", config.SOLO_RESULT_SECONDS)
    from drivers import round_engine
    if state.round_type == "solo_challenge":
        round_engine.end_solo_round(team, won, delta, _s["challenge"])


def finish_external(won, detail=""):
    """A challenge engine that decides its own result (drivers/cliffhanger_engine.py)."""
    _finish(won, detail)


def holds_song():
    """The challenge keeps the song where it is (no auto-DJ moves): Cliffhanger's listening / climbing."""
    if not playing() or _s["challenge"] != "cliffhanger":
        return False
    from drivers import cliffhanger_engine
    return cliffhanger_engine.holds_song()


def simon_round_cleared(rounds_cleared):
    """drivers/simon_engine.py::press(), every cleared Simon round. True = the challenge is won and
    Simon has been closed (the caller must stop)."""
    if not playing() or _s["challenge"] != "simon":
        return False
    if rounds_cleared < config.SOLO_SIMON_WIN_ROUNDS:
        return False
    from drivers import simon_engine
    simon_engine.exit_simon()
    _finish(True, f"cleared {rounds_cleared} rounds")
    return True


def update(now):
    """Per frame (drivers/show_engine.py::update)."""
    if _s["phase"] == "playing":
        if state.show_phase != "live":
            _s["phase"] = ""      # show stopped / new game: drop it, no points
            if _s["challenge"] == "cliffhanger":
                from drivers import cliffhanger_engine
                cliffhanger_engine.cancel()
            return
        if _s["challenge"] == "cliffhanger":
            from drivers import cliffhanger_engine
            cliffhanger_engine.update(now)
            return
        if _s["challenge"] == "simon" and state.mode != state.MODE_SIMON and now - _s["started_at"] > 1.0:
            # Simon went back to DJ mode on its own: a miss (after its score review) or a timeout.
            _finish(False, f"cleared {max(0, state.simon_round - 1)} of {config.SOLO_SIMON_WIN_ROUNDS} rounds")
    elif _s["phase"] == "result" and now >= _s["result_until"]:
        _s["phase"] = ""
        if _s["challenge"] == "cliffhanger":
            from drivers import cliffhanger_engine
            cliffhanger_engine.clear()


def public_view():
    """Phones (web/remote_server.py player state): None when nothing is happening."""
    if not active():
        return None
    spec = CHALLENGES.get(_s["challenge"]) or {}
    return {"phase": _s["phase"], "challenge": spec.get("name", _s["challenge"]), "rule": spec.get("rule", ""),
            "team": _s["team"], "player_id": _s["player_id"], "stakes": config.SOLO_STAKES,
            "result": _s["result"], "delta": _s["delta"], "detail": _s["detail"],
            "round": max(0, state.simon_round - 1) if _s["challenge"] == "simon" and _s["phase"] == "playing" else None,
            "progress": _progress()}


def _progress():
    if _s["challenge"] == "cliffhanger":
        from drivers import cliffhanger_engine
        return cliffhanger_engine.progress_text()
    return ""


def status():
    """Admin page."""
    teams = sorted(({"player_id": pid, "name": _team_name(pid),
                     "night": state.show_cumulative_scores.get(pid, 0) + p.get("score", 0)}
                    for pid, p in state.quiz_players.items()), key=lambda t: t["name"].lower())
    from drivers import cliffhanger_engine
    return {"ok": True, "view": public_view(), "can_start": _idle_reason() is None, "why_not": _idle_reason(),
            "cliff": cliffhanger_engine.admin_view() if _s["challenge"] == "cliffhanger" else None,
            "pregame": cliffhanger_engine.pregame_view(),
            "round_game": state.round_solo_game if state.round_type == "solo_challenge" else "",
            "stakes": config.SOLO_STAKES, "teams": teams, "solo_round": state.round_type == "solo_challenge",
            "challenges": [{"key": k, "name": v["name"], "available": v["available"], "rule": v["rule"]}
                           for k, v in CHALLENGES.items()]}
