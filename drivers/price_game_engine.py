import time
import threading

import config
from state import state
from drivers import factoid_engine

# key -> fetched result dict, or the string "FAILED" -- populated by
# _fetch_worker, consumed (popped) by update() once the intro sequence
# reaches "question_wait". A dict here, not a queue, since only the most
# recent fetch for the *current* track key is ever relevant.
_fetch_results = {}
_fetch_lock = threading.Lock()
_inflight = set()


def start_price_game(key, decade_label):
    """Btn6 hook (inputs/gamepad.py::handle_quiz_gate_button): arms the
    70s/80s Price Game intro -- strobe -> banner -> pricing question --
    instead of the normal instant pull from the track's question queue.
    The pricing question itself is fetched in the background over the
    ~3.5s strobe+banner intro, so it's usually ready the instant the intro
    finishes."""
    if state.price_game_active:
        return

    state.price_game_active = True
    state.price_game_phase = "strobe"
    state.price_game_phase_started_at = time.time()
    state.price_game_decade = decade_label
    state.price_game_pending = False

    # Guarantee "the standard mid-pace chase pattern" per the bonus-round
    # spec, even if a prior win/loss fast/slow chase-pace window is still
    # counting down (drivers/lighting_engine.py::_chase_pace_seconds).
    state.chase_pace_mode = "mid"
    state.chase_pace_until = 0.0

    if key not in _inflight:
        _inflight.add(key)
        threading.Thread(target=_fetch_worker, args=(key, decade_label), daemon=True).start()


def _fetch_worker(key, decade_label):
    try:
        result, reason = factoid_engine.fetch_price_question(decade_label)
        with _fetch_lock:
            _fetch_results[key] = result if result else "FAILED"
        if not result:
            print(f"[PRICE GAME] Question fetch failed for '{key}': {reason}")
    finally:
        _inflight.discard(key)


def update(now):
    """Per-frame poll, called from inputs/gamepad.py::process_events()
    alongside deck_orchestrator.update(). Advances the intro sequence:
    strobe (PRICE_GAME_STROBE_SECONDS) -> banner (PRICE_GAME_BANNER_SECONDS)
    -> question_wait (until the background fetch lands or times out)."""
    if not state.price_game_active:
        return

    phase = state.price_game_phase
    elapsed = now - state.price_game_phase_started_at

    if phase == "strobe":
        if elapsed >= config.PRICE_GAME_STROBE_SECONDS:
            state.price_game_phase = "banner"
            state.price_game_phase_started_at = now
        return

    if phase == "banner":
        if elapsed >= config.PRICE_GAME_BANNER_SECONDS:
            state.price_game_phase = "question_wait"
            state.price_game_phase_started_at = now
        return

    if phase == "question_wait":
        key = state.factoid_track_key
        with _fetch_lock:
            result = _fetch_results.pop(key, None)

        if result == "FAILED":
            print("[PRICE GAME] Forcing local fallback question after a failed fetch.")
            factoid_engine.load_forced_fallback_question("PRICE_GAME_FETCH_FAILED")
            state.mode = state.MODE_GAME
            _reset_intro()
            return

        if result:
            factoid_engine.apply_price_question(result)
            state.mode = state.MODE_GAME
            _reset_intro()
            return

        if elapsed >= config.PRICE_GAME_QUESTION_TIMEOUT_SECONDS:
            print(f"[PRICE GAME] Question fetch still not ready after "
                  f"{config.PRICE_GAME_QUESTION_TIMEOUT_SECONDS}s -- forcing local fallback.")
            factoid_engine.load_forced_fallback_question("PRICE_GAME_TIMEOUT_FALLBACK")
            state.mode = state.MODE_GAME
            _reset_intro()


def _reset_intro():
    state.price_game_active = False
    state.price_game_phase = ""
    state.price_game_phase_started_at = 0.0
