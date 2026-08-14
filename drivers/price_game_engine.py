import time
import threading

import config
from state import state
from drivers import factoid_engine
from drivers import midi_driver
from drivers import price_bank_engine
from audio.audio_engine import (
    play_random_game_music, fade_out_game_music, is_game_music_playing, sync_game_music_volume,
)

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


def _arm_intro(decade_label):
    """Shared setup for both Price Game entry points below: strobe/banner
    intro state + the Section 1 background-music duck. `decade_label` is
    only used for the banner text (graphics/matrix_canvas.py::
    _render_price_banner) -- empty for a Btn6/bank-sourced round, which
    isn't themed to any particular decade."""
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

    # Section 1: Price Game Mode background music (plays once, no loop --
    # see audio_engine.py::play_random_game_music) + MIDI fader duck. Spans
    # the whole round (intro + question + grading), capped at
    # PRICE_GAME_AUDIO_MAX_SECONDS regardless of how the round itself plays
    # out -- see _update_price_game_audio().
    state.price_game_audio_active = True
    state.price_game_audio_entered_game = False
    state.price_game_audio_started_at = time.time()
    play_random_game_music()
    midi_driver.tween_channel_faders_to(0, config.PRICE_GAME_AUDIO_TWEEN_SECONDS)


def start_price_game(key, decade_label):
    """Web remote 'Force Game Mode -> Price Game' / the Btn5+Btn6 hold combo
    (inputs/gamepad.py::force_price_game()): arms the Price Game intro
    themed to a specific decade -- strobe -> banner -> pricing question --
    fetched live from the AI (decade/era-matched, with product-category
    rotation and a repetition cooldown). Only reachable when the AI has
    already tagged the current track with a release year in range, hence
    it needing its own dedicated combo rather than living on plain Btn6 --
    see start_price_game_from_bank() for the always-works local path."""
    if state.price_game_active:
        return

    _arm_intro(decade_label)

    # Round-robin index into config.PRICE_GAME_CATEGORIES so consecutive
    # rounds don't keep landing on the same product type.
    category_index = state.price_game_occurrences
    state.price_game_occurrences += 1

    if key not in _inflight:
        _inflight.add(key)
        avoid_products = _products_on_cooldown()
        threading.Thread(
            target=_fetch_worker, args=(key, decade_label, category_index, avoid_products), daemon=True
        ).start()


def start_price_game_from_bank():
    """Btn6 hook (inputs/gamepad.py::handle_quiz_gate_button): unconditionally
    arms the Price Game intro sourced from the local CSV bank
    (drivers/price_bank_engine.py, config.PRICE_GAME_BANK_PATH) -- no AI
    call, no track/decade dependency, so a live Btn6 press can never fail
    into a fallback/Mystery-Band question or a 'synthesized sound' buzz.
    The question is drawn synchronously before the strobe even starts, so
    by the time the ~3.5s strobe+banner intro finishes there's nothing left
    to wait on.

    The question is matched to the playing song's own year where the bank
    still has an unused one for it (state.track_release_year, set by
    drivers/factoid_engine.py as soon as the AI tags the track), falling
    outward to the nearest year otherwise."""
    if state.price_game_active:
        return

    question = price_bank_engine.draw_price_question(state.track_release_year)
    if question is None:
        print("[PRICE GAME] Local question bank is empty/unreadable -- "
              "forcing a generic offline question instead of a silent no-op.")
        factoid_engine.load_forced_fallback_question("PRICE_GAME_BANK_EMPTY")
        state.mode = state.MODE_GAME
        return

    _arm_intro("")
    state.price_game_bank_question = question


def _fetch_worker(key, decade_label, category_index, avoid_products=None):
    try:
        if state.ai_suppressed:
            # Master AI Suppress checkbox: skip the network call entirely --
            # the "FAILED" result routes straight into the same local
            # fallback-question path a real fetch failure already uses
            # (see update()'s "question_wait" phase below).
            with _fetch_lock:
                _fetch_results[key] = "FAILED"
            print(f"[PRICE GAME] AI suppressed -- forcing local fallback question for '{key}'.")
            return
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

    # Keeps the bed tracking the admin's live master volume (2026-08-12
    # fix) -- it plays on a separate audio stream from the 4 channels
    # audio/dj_engine.py scales by state.music_volume, so without this a
    # volume nudge mid-round wouldn't be heard until the NEXT Price Game.
    sync_game_music_volume()

    # The bed plays exactly once, no loop (audio_engine.py::
    # play_random_game_music, loops=0 -- it must never audibly repeat in a
    # live show). If it finishes its single playthrough before the round
    # is graded, restore the deck to normal volume right away (brisk --
    # every extra second of silence matters here) instead of leaving it
    # ducked to 0% for whatever's left of the round. Routed through the
    # single _end_price_game_audio() path (rather than duplicating its
    # fade/tween calls here) so this can only ever fire once per round.
    if not is_game_music_playing():
        print("[PRICE GAME] Background bed finished its single playthrough -- "
              "restoring deck volume early instead of sitting in silence.")
        _end_price_game_audio(brisk=True)
        return

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
        if state.price_game_bank_question is not None:
            question = state.price_game_bank_question
            state.price_game_bank_question = None
            factoid_engine.apply_price_question(question)
            state.mode = state.MODE_GAME
            _reset_intro()
            return

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
