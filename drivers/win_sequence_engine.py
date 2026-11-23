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


def start(player_id, reason="goal"):
    """Called from inputs/gamepad.py::_grade_multiplayer_round() the
    instant a player's score crosses config.GAME_WIN_SCORE (reason "goal"), and by
    drivers/round_engine.py when a round's songs run out or the operator ends it
    (reason "songs" / "operator": the leader on raw points wins; player_id None = nobody
    scored, so no celebration -- straight to the intermission)."""
    if state.win_sequence_active:
        return
    from drivers import round_engine
    round_engine.note_round_end(reason, player_id)
    if player_id is None:
        show_engine.accumulate_round_scores()
        state.last_round_winner_initials = ""
        state.last_round_number = state.game_round_number
        print(f"[WIN SEQUENCE] Round ended ({reason}) with no scores -- straight to the intermission.")
        _finish_round(time.time())
        return
    # Trivia Night show flow (2026-08-13): fold this round's final scores
    # into the night-long cumulative tally BEFORE anything below resets
    # them -- see drivers/show_engine.py::accumulate_round_scores(). A
    # no-op outside a managed show (state.show_cumulative_scores just sits
    # unused).
    show_engine.accumulate_round_scores()
    state.game_winner_player_id = player_id
    winner = state.quiz_players.get(player_id) or {}
    state.last_round_winner_initials = (winner.get("initials") or "").strip() or "???"
    state.last_round_number = state.game_round_number
    state.win_sequence_active = True
    state.win_sequence_phase = "duck"
    state.win_sequence_started_at = time.time()
    midi_driver.tween_channel_faders_to(config.WIN_DUCK_LEVEL_PCT, config.WIN_DUCK_TWEEN_SECONDS)
    print(f"[WIN SEQUENCE] Player {player_id!r} won the round ({reason}) -- ducking for applause.")


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
            _finish_round(now)


def _finish_round(now):
    """Trivia Night show flow (2026-08-13): if this round was tonight's Auto Final Round -- or the last
    one in the show's round list -- the night ends here instead of starting a normal intermission (see
    drivers/show_engine.py::start_outro()/state.show_final_round_number, set once at Start Game time,
    only when the Setup page's "Auto Final Round" checkbox was on; otherwise only the manual STOP GAME
    button ends the night)."""
    from drivers import round_engine
    if state.show_phase == "live" and (
            (state.show_final_round_number and state.game_round_number >= state.show_final_round_number)
            or round_engine.rounds_exhausted()):
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
    if state.intermission_finishing:
        # Timer is up but a song is still playing: let it finish before any
        # question is asked (a song change ends the hold). The dead-air
        # watchdog stays live so a missed hand-off can't hold the show forever.
        if state.transition_count != state.intermission_finish_transition:
            _end_intermission("song finished")
        else:
            _watchdog_auto_next(now)
        return
    _fit_intermission(now)
    if now >= state.intermission_ends_at:
        if _song_is_playing() and _next_round_opens_on_intro():
            # 2026-11-23: waiting for the song to end meant a song change (banner, wipe, a few seconds
            # of the new song) right before the round's intro faded it out again. These rounds open
            # on their own intro, which fades the intermission song itself -- start it now.
            _end_intermission("timer -- the next round opens on its intro")
            return
        if _song_is_playing():
            state.intermission_finishing = True
            state.intermission_finish_transition = state.transition_count
            state.intermission_ends_at = now
            print("[WIN SEQUENCE] Intermission timer up -- letting the current song finish first.")
            return
        _end_intermission("timer")
        return
    _watchdog_auto_next(now)


# Intermission fit (2026-11-23): the intermission's last song should END at the timer, not run 3 min
# past it. Planned once per song (and again if the next song or the timer changes):
#   gap = timer end - this song's end.
#   * gap <= INTERMISSION_EARLY_END_MAX_SECONDS: this is the last song -- the intermission ends early,
#     when it ends (nobody waits for a song that can't fit).
#   * otherwise, if the next song would run past the timer, this song is faded out early by the
#     overrun, so the next one plays in full and ends on the timer ("people remember bad endings much
#     more than bad middles"). This song still plays at least INTERMISSION_FIT_MIN_PLAY_SECONDS.
_fit = {"key": None}


def _song_end():
    """When Auto-DJ will start moving off the playing song (its cue-out)."""
    return state.auto_dj_track_started_at + state.auto_dj_track_duration - config.AUTODJ_PRE_SWITCH_SECONDS


def _fit_intermission(now):
    if not config.INTERMISSION_FIT_ENABLED or state.intermission_finishing or state.intermission_paused:
        return
    if not _song_is_playing() or not state.auto_dj_track_key or deck_orchestrator.has_pending_move():
        return
    if now - state.auto_dj_track_started_at < 2.0:
        return      # the new song's timer/cue-out is still settling
    nxt = deck_orchestrator.peek_next_track()
    key = (state.transition_count, (nxt or {}).get("path"), round(state.intermission_ends_at))
    if key == _fit["key"]:
        return
    _fit["key"] = key
    end = _song_end()
    gap = state.intermission_ends_at - end
    if gap < 0:
        return      # this song already runs past the timer (it was playing when the break began)
    if gap <= config.INTERMISSION_EARLY_END_MAX_SECONDS:
        state.intermission_ends_at = max(now, end - 2.0)
        _fit["key"] = (state.transition_count, (nxt or {}).get("path"), round(state.intermission_ends_at))
        print(f"[WIN SEQUENCE] Intermission fit: no room for another song ({gap:.0f}s left after this one) "
              f"-- the break ends with this song, {gap:.0f}s early.")
        return
    if not nxt or not nxt.get("duration"):
        return
    n_len = auto_dj_engine.planned_length(nxt["path"], float(nxt["duration"]))
    over = n_len - gap
    if over <= config.INTERMISSION_FIT_TOLERANCE_SECONDS:
        return      # the next song fits (whatever is left after it is planned when it plays)
    if over > config.INTERMISSION_FIT_BIG_CUT_SECONDS and gap <= config.INTERMISSION_EARLY_END_STRETCH_SECONDS:
        # Fading minutes off this song would hurt more than ending the break a little early.
        state.intermission_ends_at = max(now, end - 2.0)
        _fit["key"] = (state.transition_count, nxt.get("path"), round(state.intermission_ends_at))
        print(f"[WIN SEQUENCE] Intermission fit: '{nxt.get('title', '?')}' would need {over:.0f}s cut from this "
              f"song -- ending the break with this one instead, {gap:.0f}s early.")
        return
    earliest = max(now + 3.0, state.auto_dj_track_started_at + config.INTERMISSION_FIT_MIN_PLAY_SECONDS)
    cut_at = max(earliest, end - over)
    if cut_at >= end - 1.0:
        return
    state.auto_dj_track_duration = (cut_at - state.auto_dj_track_started_at) + config.AUTODJ_PRE_SWITCH_SECONDS
    _fit["key"] = (state.transition_count, nxt.get("path"), round(state.intermission_ends_at))
    left = (cut_at + n_len) - state.intermission_ends_at
    print(f"[WIN SEQUENCE] Intermission fit: '{nxt.get('title', '?')}' ({n_len:.0f}s) is the last song -- "
          f"fading this one {end - cut_at:.0f}s early so it ends on the timer"
          + (f" ({left:.0f}s over: this song can't give more)" if left > 2 else "") + ".")


def _next_round_opens_on_intro():
    """The coming round starts with its own jingle ("Wait, how much?" / Fastest Finger)."""
    from drivers import round_engine
    return str(round_engine._spec_for(state.round_index).get("type") or "") in ("wait_how_much", "fastest_finger")


def _song_is_playing():
    return bool(state.now_playing_path) and state.active_deck in (1, 2)


def _end_intermission(why):
    state.intermission_active = False
    state.intermission_finishing = False
    state.intermission_ends_at = 0.0
    state.game_round_number += 1  # matrix idle GOAL PAGE's "ROUND N"
    from drivers import round_engine
    round_engine.begin_round()
    print(f"[WIN SEQUENCE] Intermission over ({why}) -- next game can begin.")


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
    if not state.intermission_active or state.intermission_paused or state.intermission_finishing:
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
    if seconds > 0:
        state.intermission_finishing = False  # extended: the countdown is live again
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
    # Freeze this round's standings for the phones' intermission screen.
    try:
        from drivers import player_profile
        rows = sorted(((pid, p) for pid, p in state.quiz_players.items() if not p.get("idle")),
                      key=lambda kv: kv[1].get("score", 0), reverse=True)
        state.last_round_board = [{
            "player_id": pid, "rank": i + 1, "score": p.get("score", 0),
            "name": p.get("name") or p.get("initials", "???"),
            "emoji": p.get("emoji") or player_profile.DEFAULT_EMOJI,
            "avatar_color": p.get("avatar_color", state.AVATAR_COLORS[0]),
        } for i, (pid, p) in enumerate(rows) if p.get("score", 0) > 0]
    except Exception as exc:
        print(f"[WIN SEQUENCE] Could not freeze the round board: {exc}")
        state.last_round_board = []
    for player in state.quiz_players.values():
        player["score"] = 0
    state.game_winner_player_id = ""
    state.game_active = False  # era-trivia scheduling: next real game starts fresh
    state.mode = state.MODE_DJ
    state.intermission_active = True
    state.intermission_finishing = False
    state.intermission_ends_at = now + state.intermission_minutes * 60.0
    print(f"[WIN SEQUENCE] Intermission started -- {state.intermission_minutes} minute(s), "
          f"all scores reset.")
