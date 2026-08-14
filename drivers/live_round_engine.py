"""drivers/live_round_engine.py
The shared "round clock" for the trivia game (Beta-Fix Feature Set items 2
and 8): owns both the auto-grade-when-everyone's-answered check and the 30s
question-timeout check, in that priority order, so the two features never
race each other or drive two competing timers. update() is polled every
frame from inputs/gamepad.py::process_events(), alongside the other
per-frame engines.

Deliberately a thin module: the actual grading logic already lives in
inputs/gamepad.py::grade_quiz_selection() (which itself dispatches to the
multiplayer path when players are signed up) -- this module just decides
WHEN to call it automatically, instead of waiting for the operator."""
import config
from state import state


def update(now):
    if state.quiz_locked:
        return
    if state.price_game_active:
        # Only actually guards the brief strobe/banner intro (state.mode is
        # still MODE_DJ then, so `active` below is False anyway) -- once
        # the price question itself is on screen, price_game_engine's
        # _reset_intro() has already flipped this back to False, so Price
        # Game questions fall through to the same shared round clock as
        # every other question type below (2026-08-10 correction: they
        # used to be fully exempted here, which left them with no
        # automatic timeout at all -- see factoid_engine.py's
        # _apply_active_question()).
        return

    # "Active" = a question is loaded and not yet graded. Deliberately NOT
    # gated on state.mystery_active/mystery_resolved (2026-08-10 fix): that
    # pair drives an entirely independent ~10-13s "Who is this?" reveal-
    # blink animation on panels 1+2 (MYSTERY_REVEAL_TIMEOUT_SECONDS +
    # MYSTERY_REVEAL_BLINK_SECONDS) that predates client-side answering and
    # was never meant to gate whether a round can still be graded. Because
    # it used to, a mystery round would silently stop being monitored by
    # this engine (and stop showing as "active" to clients) after ~10-13s
    # -- long before the real 30/40s answer deadline -- even though nobody
    # had answered yet, which is exactly what let a correct-but-still-
    # pending answer go ungraded. state.quiz_locked/factoid_choices alone
    # already correctly track "is there a live, ungraded round" for every
    # question type (mystery included, since _apply_active_question() is
    # the shared chokepoint that resets both).
    active = bool(state.factoid_choices) and not state.quiz_locked
    if not active:
        return

    # Lazy import: inputs.gamepad imports this module at its own top level
    # to call update() every frame, so importing it back there would be
    # circular (same pattern as graphics/matrix_canvas.py's lazy import of
    # drivers/deck_orchestrator.py).
    from inputs import gamepad

    if state.quiz_players:
        connected = [
            p for p in state.quiz_players.values()
            if now - p.get("last_seen", 0) <= config.CONNECTED_PLAYER_TIMEOUT_SECONDS
        ]
        if connected and all(p["locked"] for p in connected):
            gamepad.grade_quiz_selection(forced=True)
            return

    if state.round_deadline_at and now >= state.round_deadline_at:
        state.round_timed_out = True
        gamepad.grade_quiz_selection(forced=True)
