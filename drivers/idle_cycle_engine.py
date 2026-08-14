"""drivers/idle_cycle_engine.py
Idle-matrix content cycle for panels 3-6 in DJ mode: START GAME prompt ->
leaderboard (paginated, ranked by score) -> dancing animations -> loop.
Fills the panel space that used to just always show dancing animations
(Beta-Fix Feature Set, item 1/4).

Phase machine shaped like drivers/westminster_engine.py: explicit phase +
phase_started_at, elapsed-time driven (not wall-clock modulo, which would
skip/duplicate leaderboard pages if a player joins mid-cycle). update() is
polled every frame from inputs/gamepad.py::process_events(), gated to only
advance while state.mode == state.MODE_DJ so it doesn't drift while a game
or Space Invaders is running."""
import math

import config
from state import state


def _leaderboard_page_count():
    if not state.quiz_players:
        return 0
    return math.ceil(len(state.quiz_players) / config.IDLE_LEADERBOARD_PAGE_SIZE)


def _goal_page_count():
    if not state.quiz_players:
        return 0
    return math.ceil(min(len(state.quiz_players), 4) / config.IDLE_GOAL_PAGE_SIZE)


def _enter_phase(phase, now):
    state.idle_cycle_phase = phase
    state.idle_cycle_phase_started_at = now
    if phase == "leaderboard":
        state.idle_cycle_leaderboard_page = 0
    elif phase == "goal":
        state.idle_cycle_goal_page = 0


def update(now):
    if state.mode != state.MODE_DJ:
        return

    if state.idle_cycle_phase_started_at == 0.0:
        # First call this run -- state.py's default is a 0.0 placeholder,
        # not a real timestamp; start the clock now instead of computing
        # elapsed against the Unix epoch (which would immediately trip
        # every phase's timeout on the very first frame).
        state.idle_cycle_phase_started_at = now

    elapsed = now - state.idle_cycle_phase_started_at
    phase = state.idle_cycle_phase

    if phase == "start_game":
        if elapsed >= config.IDLE_START_GAME_SECONDS:
            if state.quiz_players:
                _enter_phase("leaderboard", now)
            else:
                _enter_phase("dancing", now)

    elif phase == "leaderboard":
        if elapsed >= config.IDLE_LEADERBOARD_PAGE_SECONDS:
            page_count = _leaderboard_page_count()
            next_page = state.idle_cycle_leaderboard_page + 1
            if page_count == 0 or next_page >= page_count:
                _enter_phase("goal", now)
            else:
                state.idle_cycle_leaderboard_page = next_page
                state.idle_cycle_phase_started_at = now

    elif phase == "goal":
        if elapsed >= config.IDLE_GOAL_PAGE_SECONDS:
            page_count = _goal_page_count()
            next_page = state.idle_cycle_goal_page + 1
            if page_count == 0 or next_page >= page_count:
                _enter_phase("dancing", now)
            else:
                state.idle_cycle_goal_page = next_page
                state.idle_cycle_phase_started_at = now

    elif phase == "dancing":
        if elapsed >= config.IDLE_DANCE_SECONDS:
            _enter_phase("start_game", now)

    else:
        # Unknown/uninitialized phase -- recover to a known-good state.
        _enter_phase("start_game", now)


def ranked_players():
    """Returns state.quiz_players.values() sorted by score descending, each
    as {"initials": str, "score": int}. Ties keep dict insertion order
    (Python dicts preserve insertion order, and sort() is stable)."""
    return sorted(
        ({"initials": p.get("initials", "???"), "score": p.get("score", 0)}
         for p in state.quiz_players.values()),
        key=lambda p: p["score"],
        reverse=True,
    )
