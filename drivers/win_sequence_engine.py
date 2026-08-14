"""drivers/win_sequence_engine.py
Fires once a phone-joined player's score reaches config.GAME_WIN_SCORE
(Beta-Fix Feature Set item 10): duck the music deck, play a random
applause sound, wait for it to finish, restore the deck. Phase machine
shaped like drivers/westminster_engine.py; duck/restore reuses
drivers/midi_driver.py::tween_channel_faders_to(), the same mechanism
already used for the hour-chime duck (and Price Game's duck/restore).

Only fires for the multiplayer (phone-joined) path -- solo/operator-only
scoring is untouched, per the confirmed design decision (a "winner" only
makes sense once there's a specific person to name)."""
import time

import config
from state import state
from drivers import midi_driver, deck_orchestrator, auto_dj_engine, show_engine
from audio.audio_engine import play_processed_sound, pick_random_applause


def start(player_id):
    """Called from inputs/gamepad.py::_grade_multiplayer_round() the
    instant a player's score crosses config.GAME_WIN_SCORE."""
    if state.win_sequence_active:
        return
    # Trivia Night show flow (2026-08-13): fold this round's final scores
    # into the night-long cumulative tally BEFORE anything below resets
    # them -- see drivers/show_engine.py::accumulate_round_scores(). A
    # no-op outside a managed show (state.show_cumulative_scores just sits
    # unused).
    show_engine.accumulate_round_scores()
    state.game_winner_player_id = player_id
    state.win_sequence_active = True
    state.win_sequence_phase = "duck"
    state.win_sequence_started_at = time.time()
    midi_driver.tween_channel_faders_to(config.WIN_DUCK_LEVEL_PCT, config.WIN_DUCK_TWEEN_SECONDS)
    print(f"[WIN SEQUENCE] Player {player_id!r} reached {state.game_win_score} -- ducking for applause.")


def update(now):
    if state.intermission_active:
        _update_intermission(now)
        return

    if not state.win_sequence_active:
        return

    elapsed = now - state.win_sequence_started_at

    if state.win_sequence_phase == "duck":
        if elapsed >= config.WIN_DUCK_TWEEN_SECONDS:
            applause = pick_random_applause()
            play_processed_sound(applause)
            state.win_sequence_phase = "applause"
            state.win_sequence_started_at = now
            state.win_applause_ends_at = now + applause.get_length()

    elif state.win_sequence_phase == "applause":
        if now >= state.win_applause_ends_at:
            midi_driver.tween_channel_faders_to(state.music_volume, config.WIN_RESTORE_TWEEN_SECONDS)
            state.win_sequence_phase = "restore"
            state.win_sequence_started_at = now

    elif state.win_sequence_phase == "restore":
        if elapsed >= config.WIN_RESTORE_TWEEN_SECONDS:
            state.win_sequence_active = False
            state.win_sequence_phase = ""
            print("[WIN SEQUENCE] Complete -- deck restored to full.")
            # Trivia Night show flow (2026-08-13): if this round was
            # tonight's Auto Final Round, the night ends here instead of
            # starting a normal intermission -- see
            # drivers/show_engine.py::start_outro()/state.show_final_round_number
            # (set once, at Start Game time, only when the Setup page's
            # "Auto Final Round" checkbox was on; otherwise None, and only
            # the manual STOP GAME button ends the night).
            if (state.show_phase == "live" and state.show_final_round_number
                    and state.game_round_number >= state.show_final_round_number):
                show_engine.start_outro()
            else:
                _start_intermission(now)


def _update_intermission(now):
    if state.intermission_paused:
        # The dead-air watchdog stays live even while the countdown itself
        # is frozen -- pausing is about giving the crowd more time, not
        # about suspending the "never leave the room in silence" safety net.
        _watchdog_auto_next(now)
        return
    if now >= state.intermission_ends_at:
        state.intermission_active = False
        state.intermission_ends_at = 0.0
        state.game_round_number += 1  # matrix idle GOAL PAGE's "ROUND N"
        print("[WIN SEQUENCE] Intermission over -- next game can begin.")
        return
    _watchdog_auto_next(now)


def get_intermission_remaining_seconds(now):
    """Single source of truth for "how much intermission time is left" --
    every display/check (the LED countdown, the web remote, phones' /api/
    player/question poll) reads through this rather than each computing
    state.intermission_ends_at - now separately, which would have to
    independently remember to special-case the paused state too."""
    if not state.intermission_active:
        return 0.0
    if state.intermission_paused:
        return max(0.0, state.intermission_paused_remaining)
    return max(0.0, state.intermission_ends_at - now)


def pause_intermission():
    """Web remote Pause button. No-op (returns False) if there's no active
    intermission to pause, or it's already paused."""
    if not state.intermission_active or state.intermission_paused:
        return False
    state.intermission_paused_remaining = max(0.0, state.intermission_ends_at - time.time())
    state.intermission_paused = True
    print(f"[WIN SEQUENCE] Intermission paused with {state.intermission_paused_remaining:.0f}s remaining.")
    return True


def resume_intermission():
    """Web remote Resume button. Re-derives intermission_ends_at from the
    frozen remaining-seconds snapshot, so the countdown picks back up
    exactly where it left off rather than wherever the original deadline
    would have put it."""
    if not state.intermission_active or not state.intermission_paused:
        return False
    state.intermission_ends_at = time.time() + state.intermission_paused_remaining
    state.intermission_paused = False
    print(f"[WIN SEQUENCE] Intermission resumed, {state.intermission_paused_remaining:.0f}s remaining.")
    return True


def set_intermission_remaining_seconds(seconds):
    """Web remote "Set remaining" control -- overrides however much time is
    left, for a crowd that's taking too long to get back to their seats.
    Works whether the countdown is currently paused or running, and in
    either direction (a typo-proof "oops, meant 5 not 15" is just as valid
    a use as extending it). No-op if there's no active intermission."""
    if not state.intermission_active:
        return False
    seconds = max(0.0, float(seconds))
    if state.intermission_paused:
        state.intermission_paused_remaining = seconds
    else:
        state.intermission_ends_at = time.time() + seconds
    print(f"[WIN SEQUENCE] Intermission remaining time set to {seconds:.0f}s.")
    return True


def _watchdog_auto_next(now):
    """Safety net for "song runs out and doesn't auto-next during
    intermission": drivers/auto_dj_engine.py's own duration-based timer
    keeps running completely unmodified through intermission -- nothing
    here suppresses it, and that's deliberate, since a break in the show is
    exactly when nobody's likely to notice (and fix) dead air themselves.
    This is only a backstop for that timer's own trigger point somehow
    being missed (e.g. a bad duration read): once well past when the
    current song should have handed off, AND no transition is already in
    flight, force one directly via the same path a manual Next press uses.
    Never touches trivia/game state -- Btn6 and the mystery teaser are
    separately guarded against starting a game during intermission (see
    inputs/gamepad.py::handle_quiz_gate_button's intermission check and
    drivers/mystery_band_engine.py::check_new_track's intermission check)."""
    if not state.auto_dj_track_key or deck_orchestrator.has_pending_move():
        return
    overdue_by = config.AUTODJ_PRE_SWITCH_SECONDS + config.INTERMISSION_AUTO_NEXT_GRACE_SECONDS
    if now - state.auto_dj_track_started_at >= state.auto_dj_track_duration + overdue_by:
        print("[WIN SEQUENCE] Intermission watchdog: current song is overdue for a transition "
              "with none in flight -- forcing auto-next so the room never sits in dead air.")
        deck_orchestrator.trigger_track_move("next")
        auto_dj_engine.notify_manual_track_move()


def _start_intermission(now):
    """Immediately after the win celebration finishes (2026-08-11): reset
    every player's score right away (not waiting for the next game to
    actually start), force back to DJ mode so no further questions get
    pulled, and hold there for state.intermission_minutes. Music keeps
    playing normally through this -- only the mystery "Who is this?"
    teaser is suppressed (drivers/mystery_band_engine.py::check_new_track())."""
    for player in state.quiz_players.values():
        player["score"] = 0
    state.game_winner_player_id = ""
    state.game_active = False  # era-trivia scheduling: next real game starts fresh
    state.mode = state.MODE_DJ
    state.intermission_active = True
    state.intermission_ends_at = now + state.intermission_minutes * 60.0
    print(f"[WIN SEQUENCE] Intermission started -- {state.intermission_minutes} minute(s), "
          f"all scores reset.")
