import time
import threading

import config
from state import state
from drivers import factoid_engine
from drivers import midi_driver
from audio.audio_engine import play_random_game_music, fade_out_game_music

# key -> fetched result dict, or the string "FAILED" -- populated by
# _fetch_worker, consumed (popped) by update() once the intro sequence
# reaches "question_wait". A dict here, not a queue, since only the most
# recent fetch for the *current* track key is ever relevant.
_fetch_results = {}
_fetch_lock = threading.Lock()
_inflight = set()


# ------------------------------------------------------------
# Product repetition cooldown (Section 3): a specific product (e.g. "Gallon
# of Milk") asked about in a Price Game question is excluded from the AI
# prompt's candidate pool for config.PRICE_GAME_PRODUCT_COOLDOWN_ROUNDS
# subsequent trivia rounds, tracked via state.price_game_product_history.
# ------------------------------------------------------------
def _products_on_cooldown():
    current_round = state.quiz_score_total
    return [
        entry["product"] for entry in state.price_game_product_history
        if current_round - entry["round"] < config.PRICE_GAME_PRODUCT_COOLDOWN_ROUNDS
    ]


def _register_product_asked(product):
    if not product:
        return
    current_round = state.quiz_score_total
    state.price_game_product_history.append({"product": product, "round": current_round})
    cutoff = current_round - config.PRICE_GAME_PRODUCT_COOLDOWN_ROUNDS
    state.price_game_product_history = [
        entry for entry in state.price_game_product_history if entry["round"] > cutoff
    ]
    print(f"[PRICE GAME] Product {product!r} on cooldown for "
          f"{config.PRICE_GAME_PRODUCT_COOLDOWN_ROUNDS} trivia rounds.")


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

    # Section 1 (music rules): only the first Price Game this session plays
    # the full background bed -- every later one caps at
    # PRICE_GAME_MUSIC_REPEAT_CAP_SECONDS (see _update_price_game_audio).
    # The same occurrence count doubles as the round-robin index into
    # config.PRICE_GAME_CATEGORIES so consecutive rounds don't keep landing
    # on the same product type.
    category_index = state.price_game_occurrences
    state.price_game_audio_is_first = state.price_game_occurrences == 0
    state.price_game_occurrences += 1

    # Section 1: Price Game Mode background music + MIDI fader duck. Spans
    # the whole round (intro + question + grading), capped at
    # PRICE_GAME_AUDIO_MAX_SECONDS regardless of how the round itself plays
    # out -- see _update_price_game_audio().
    state.price_game_audio_active = True
    state.price_game_audio_entered_game = False
    state.price_game_audio_started_at = time.time()
    state.price_game_audio_capped = False
    play_random_game_music()
    midi_driver.tween_channel_faders_to(0, config.PRICE_GAME_AUDIO_TWEEN_SECONDS)
    if not state.price_game_audio_is_first:
        print(f"[PRICE GAME] Repeat occurrence #{state.price_game_occurrences} this session -- "
              f"game music capped at {config.PRICE_GAME_MUSIC_REPEAT_CAP_SECONDS}s.")

    if key not in _inflight:
        _inflight.add(key)
        avoid_products = _products_on_cooldown()
        threading.Thread(
            target=_fetch_worker, args=(key, decade_label, category_index, avoid_products), daemon=True
        ).start()


def _fetch_worker(key, decade_label, category_index, avoid_products=None):
    try:
        result, reason = factoid_engine.fetch_price_question(decade_label, category_index, avoid_products)
        with _fetch_lock:
            _fetch_results[key] = result if result else "FAILED"
        if result:
            _register_product_asked(result.get("product") or result.get("headline"))
        else:
            print(f"[PRICE GAME] Question fetch failed for '{key}': {reason}")
    finally:
        _inflight.discard(key)


def _end_price_game_audio(brisk=False):
    """Fades the background bed out and tweens the channel faders back to
    state.music_volume (the DJ's last stored level, 100% by default at
    launch). Idempotent -- safe to call even if already ended. `brisk=True`
    (used the instant a Price Game question is graded, see
    end_price_game_audio_on_answer) fades/tweens faster than the normal
    intro/timeout/abort paths."""
    if not state.price_game_audio_active:
        return
    state.price_game_audio_active = False
    if brisk:
        fade_out_game_music(config.PRICE_GAME_BRISK_FADE_MS)
        midi_driver.tween_channel_faders_to(state.music_volume, config.PRICE_GAME_BRISK_FADER_TWEEN_SECONDS)
    else:
        fade_out_game_music()
        midi_driver.tween_channel_faders_to(state.music_volume, config.PRICE_GAME_AUDIO_TWEEN_SECONDS)


def force_end_price_game_audio():
    """Public hook for an early exit (Btn7 abort during a Price Game round,
    inputs/gamepad.py::abort_game_mode_early()): ends the music/fader duck
    immediately instead of waiting out the PRICE_GAME_AUDIO_MAX_SECONDS cap."""
    _end_price_game_audio()


def end_price_game_audio_on_answer():
    """Public hook for inputs/gamepad.py::grade_quiz_selection(): the moment
    a Price Game question is graded, fade the bed out briskly and tween the
    channel faders back up quickly, rather than waiting for the scorecard
    hold + return-to-DJ-mode path in _update_price_game_audio() below."""
    _end_price_game_audio(brisk=True)


def _update_price_game_audio(now):
    if not state.price_game_audio_active:
        return

    # Section 1 (music rules): repeat occurrences cap the bed early. The
    # instant the shortened bed fades, immediately tween channel1/channel2
    # faders back up to state.music_volume -- previously this only silenced
    # the music and left the main track ducked to 0% (dead air) for the
    # rest of the round, since the fader restore only fired later on the
    # round's own end-of-round path.
    if (not state.price_game_audio_is_first and not state.price_game_audio_capped
            and now - state.price_game_audio_started_at >= config.PRICE_GAME_MUSIC_REPEAT_CAP_SECONDS):
        print(f"[PRICE GAME] Repeat-occurrence {config.PRICE_GAME_MUSIC_REPEAT_CAP_SECONDS}s cap "
              f"reached -- fading bed early and restoring main track faders.")
        fade_out_game_music()
        midi_driver.tween_channel_faders_to(state.music_volume, config.PRICE_GAME_AUDIO_TWEEN_SECONDS)
        state.price_game_audio_capped = True

    if state.mode == state.MODE_GAME:
        state.price_game_audio_entered_game = True
    elif state.price_game_audio_entered_game and state.mode == state.MODE_DJ:
        # The round played out and returned to DJ mode on its own.
        _end_price_game_audio()
        return

    if now - state.price_game_audio_started_at >= config.PRICE_GAME_AUDIO_MAX_SECONDS:
        print(f"[PRICE GAME] Audio/fader cap ({config.PRICE_GAME_AUDIO_MAX_SECONDS}s) reached -- "
              f"restoring music/faders regardless of round state.")
        _end_price_game_audio()


def update(now):
    """Per-frame poll, called from inputs/gamepad.py::process_events()
    alongside deck_orchestrator.update(). Advances the intro sequence:
    strobe (PRICE_GAME_STROBE_SECONDS) -> banner (PRICE_GAME_BANNER_SECONDS)
    -> question_wait (until the background fetch lands or times out)."""
    midi_driver.update_fader_tween(now)
    _update_price_game_audio(now)

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
