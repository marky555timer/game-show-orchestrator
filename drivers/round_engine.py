"""drivers/round_engine.py
Rounds (2026-11-19, docs/rounds_design.md; step 1 standard trivia, step 3 "Wait, how much?" + Fastest Finger).

Non-trivia rounds (no goal; the leader on points when the round is over wins it):
  * wait_how_much  -- one Price Game question (local bank) per song, `songs` = question count;
  * fastest_finger -- one Dance Medley with its TRUE/FALSE game, `songs` = clip count. No medley
    possible (too few mixable songs) = the round is played as standard trivia instead.
The normal per-song questions are off in both (song_questions_on()).

A night is an ordered list of rounds. A show may carry one:

  show["rounds"] = [{"type": "trivia", "name": "Round 1", "goal": 1500, "songs": 14}, ...]

Every field is optional; a show with no list runs endless standard-trivia rounds, exactly as before
(goal = the show's win score, repeating until STOP GAME). Only "trivia" exists so far -- any other type
is logged and run as trivia until its own build step lands.

Scores: a player's `score` is the ROUND score (zeroed when a round ends), and
state.show_cumulative_scores + `score` is the NIGHT score (every round counts).

A trivia round ends when
  * someone reaches the goal (inputs/gamepad.py -> win_sequence_engine.start), or
  * its songs run out: after `songs` songs have played, the limit-th song's last seconds end the
    round and the leader on raw round points is the winner (update() below), or
  * the operator ends it (END ROUND on the admin page).
Then the existing win celebration -> intermission -> next round runs. begin_round() is called at show
start and whenever an intermission ends.

Songs are counted off state.transition_count (one per song change). The limit defaults to the songs
needed to reach the goal at config.ROUND_POINTS_PER_SONG_ESTIMATE plus config.ROUND_SAFETY_SONGS spare.
"""
import math
import time

import config
from state import state

SUPPORTED_TYPES = ("trivia", "wait_how_much", "fastest_finger", "solo_challenge", "simon", "cliffhanger")
# Solo rounds (2026-11-22): "simon" and "cliffhanger" are a solo_challenge round with its game fixed
# (state.round_solo_game); "solo_challenge" (older shows) lets the host pick. A Cliffhanger round goes
# straight into its pregame (drivers/cliffhanger_engine.py::pregame_*).
SOLO_GAMES = {"simon": "simon", "cliffhanger": "cliffhanger", "solo_challenge": ""}
UNITS = {"trivia": "song", "wait_how_much": "question", "fastest_finger": "clip", "solo_challenge": "challenge"}

# Internals of the running non-trivia round (reset by begin_round).
_sp = {}


def _reset_special():
    _sp.clear()
    _sp.update({"began_at": time.time(), "asked_tc": None, "asking": False, "saw_game": False,
                "asked_at": 0.0, "fails": 0, "medley": False, "retry_at": 0.0, "next_at": 0.0,
                "intro_skip": False, "intro_done": False, "music_back": False})
    if state.ff_intro_started_at:
        _end_ff_intro()
    state.round_special_count = 0


def goal_counts():
    """Reaching the goal ends a standard trivia round and a "Wait, how much?" round (which also ends
    after its question count); Fastest Finger runs to its clip count and goes to the leader on points."""
    return state.round_type in ("trivia", "wait_how_much")


def first_bonus_on(category):
    """The first right answer earns config.FIRST_CORRECT_BONUS_POINTS: the "Who is this?" question,
    and every question of a "Wait, how much?" round (speed points already reward the fastest)."""
    return category == "identify_band" or (category == "price_game" and state.round_type == "wait_how_much")


def holds_song():
    """Keep the song where it is (drivers/auto_dj_engine.py::update): Cliffhanger. A "Wait, how
    much?" round no longer holds it (2026-11-20): the playlist plays on under the questions, songs
    change only when they end, with a plain crossfade (no sweeper / announcement / wheel spin)."""
    from drivers import solo_challenge
    return solo_challenge.holds_song()


def price_round_live():
    """A "Wait, how much?" round is mid-play: drivers/price_game_engine.py plays only a short stinger
    per question and skips the strobe intro after the first; songs change quietly (auto_dj_engine)."""
    return (state.round_type == "wait_how_much" and state.show_phase == "live"
            and not state.intermission_active and not state.win_sequence_active)


def price_round_card():
    """Phones between price questions: which question is next."""
    if not price_round_live():
        return None
    return {"next": min(state.round_special_count + 1, state.round_song_limit or 1),
            "of": state.round_song_limit}


def ff_intro_active():
    return bool(state.ff_intro_started_at)


def ff_intro_skip(why):
    """Red panel button / NEXT TRACK during the Fastest Finger / "Wait, how much?" intro: start the
    round's game now."""
    if ff_intro_active():
        print(f"[ROUND] {state.round_name} intro ended by {why}.")
        _sp["intro_skip"] = True


def song_questions_on():
    """The normal per-song questions ("Who is this?" + the song's own) run in trivia rounds only, and
    never while a solo challenge is being played."""
    from drivers import solo_challenge
    if solo_challenge.active():
        return False
    if night_over():
        return False
    return state.round_type == "trivia" or state.show_phase != "live"


def night_over():
    """The night has ended (2026-11-22): the last round is done (its win celebration may still be
    running), the outro is on, or the after-show background music is playing. No more questions."""
    return state.show_phase == "outro" or bool(state.show_background) or rounds_exhausted()


def _default_count(rtype):
    if rtype == "wait_how_much":
        return int(config.ROUND_PRICE_QUESTIONS_DEFAULT)
    if rtype == "fastest_finger":
        try:
            from drivers import medley_engine
            return int(medley_engine.options().get("songs") or config.MEDLEY_DEFAULT_SONGS)
        except Exception:
            return int(config.MEDLEY_DEFAULT_SONGS)
    return 0


def _apply_trivia(spec):
    goal = int(spec.get("goal") or 0)
    if goal > 0:
        state.game_win_score = goal
    state.round_type = "trivia"
    state.round_goal = int(state.game_win_score)
    limit = int(spec.get("songs") or 0)
    state.round_song_limit = limit if limit > 0 else estimate_song_limit(state.round_goal)


def _spec_for(index):
    rounds = state.rounds
    if rounds:
        if 0 <= index < len(rounds):
            return dict(rounds[index] or {})
        return {}
    return {}


def rounds_exhausted():
    """The show lists its rounds and every one has been played: the night ends after the last."""
    return bool(state.rounds) and state.round_index >= len(state.rounds)


def estimate_song_limit(goal):
    est = max(1, int(config.ROUND_POINTS_PER_SONG_ESTIMATE))
    return int(math.ceil(max(1, goal) / est)) + int(config.ROUND_SAFETY_SONGS)


def begin_round():
    """Applies the current round's spec (goal, song limit) and starts counting its songs. Safe to call
    more than once for the same round."""
    spec = _spec_for(state.round_index)
    rtype = str(spec.get("type") or "trivia")
    if rtype not in SUPPORTED_TYPES:
        print(f"[ROUND] Round type {rtype!r} isn't built yet -- running it as trivia.")
        rtype = "trivia"
    _reset_special()
    state.round_solo_game = SOLO_GAMES.get(rtype, "")
    if rtype in SOLO_GAMES:
        rtype = "solo_challenge"
    if rtype == "trivia":
        _apply_trivia(spec)
    elif rtype == "solo_challenge":
        state.round_type = rtype
        state.round_goal = 0          # the host launches the challenge; its result ends the round
        state.round_song_limit = 0
    elif rtype == "wait_how_much":
        goal = int(spec.get("goal") or 0)
        if goal > 0:
            state.game_win_score = goal
        state.round_type = rtype
        state.round_goal = int(state.game_win_score)   # first to the goal, or most points after N questions
        n = int(spec.get("songs") or 0)
        state.round_song_limit = n if n > 0 else _default_count(rtype)
    else:
        state.round_type = rtype
        state.round_goal = 0          # no goal: most points after the last question / clip wins
        n = int(spec.get("songs") or 0)
        state.round_song_limit = n if n > 0 else _default_count(rtype)
    state.round_name = str(spec.get("name") or f"Round {state.round_index + 1}")
    state.round_start_transition = state.transition_count
    state.round_wheel_pending = bool(spec.get("wheel")) and rtype == "trivia"
    _apply_decade(spec)
    if rtype == "trivia":
        print(f"[ROUND] {state.round_name} (trivia): first to {state.round_goal}, "
              f"{state.round_song_limit} songs max{', opens with a wheel spin' if state.round_wheel_pending else ''}.")
    elif rtype == "solo_challenge" and state.round_solo_game == "cliffhanger":
        print(f"[ROUND] {state.round_name} (Cliffhanger): straight into the pregame.")
    elif rtype == "solo_challenge":
        print(f"[ROUND] {state.round_name} (solo challenge{': ' + state.round_solo_game if state.round_solo_game else ''}): "
              f"waiting for the host to pick a team{'' if state.round_solo_game else ' + game'}.")
    else:
        print(f"[ROUND] {state.round_name} ({rtype}): {state.round_song_limit} {UNITS[rtype]}s, " + (f"first to {state.round_goal} or " if state.round_goal else "") + "most points wins.")


def _apply_decade(spec):
    """The round's declared decade (2026-11-21): its wheel spin lands there and its songs come from
    it (deck_orchestrator's show filter + drivers/show_runner.py). "" = any decade in the show's range."""
    from drivers import show_library
    dec = spec.get("decade") if spec.get("decade") in show_library.DECADES else ""
    state.round_decade = dec
    if state.show_background:
        return      # after the outro: background music keeps its own (unfiltered) picks
    state.show_allowed_decades = [dec] if dec else list(state.show_base_decades)
    if dec:
        print(f"[ROUND] {state.round_name or 'Round'}: songs from the {dec}.")
    try:
        from drivers import show_runner
        show_runner.round_changed()     # the song cued for later may be from another decade
    except Exception as e:
        print(f"[ROUND] Re-cue for the round's decade failed: {e}")


def reset_for_show(show):
    """A new night: load the show's round list and start round 1."""
    state.rounds = [dict(r) for r in ((show or {}).get("rounds") or []) if isinstance(r, dict)]
    state.round_index = 0
    state.round_results = []
    state.last_round_reason = ""
    begin_round()


def snapshot():
    """Where the night is in its round list, for RESUME LAST SHOW / backup takeover
    (drivers/failover_engine.py). "between" = the round just ended (win celebration or intermission):
    its scores are already folded into the night totals and round_index already points at the next."""
    return {"index": state.round_index, "results": list(state.round_results),
            "last_reason": state.last_round_reason, "songs": songs_in_round(),
            "between": bool(state.win_sequence_active or state.intermission_active)}


def restore(snap):
    """RESUME LAST SHOW / backup takeover (2026-11-23): pick the round list up at the round that was
    running (from its start; a trivia round keeps its song count), or at the next one if the restart
    came between rounds. Called after the show is loaded and the players restored. A report from
    before rounds were saved (no "rounds") leaves round 1 as it is."""
    if not isinstance(snap, dict):
        return
    try:
        index = max(0, int(snap.get("index") or 0))
    except (TypeError, ValueError):
        index = 0
    if state.rounds:
        index = min(index, len(state.rounds) - 1)
    between = bool(snap.get("between"))
    state.round_index = index
    state.round_results = [r for r in (snap.get("results") or []) if isinstance(r, dict)][-50:]
    state.last_round_reason = str(snap.get("last_reason") or "")
    if between:
        for player in state.quiz_players.values():
            player["score"] = 0     # already in the night totals
    begin_round()
    songs = 0
    if not between and state.round_type == "trivia":
        try:
            songs = max(0, int(snap.get("songs") or 0))
        except (TypeError, ValueError):
            songs = 0
        if songs:
            state.round_start_transition = state.transition_count - songs
            state.round_wheel_pending = False   # its opening spin already happened
    print(f"[ROUND] Resumed at {state.round_name} (round {index + 1}"
          f"{' of ' + str(len(state.rounds)) if state.rounds else ''})"
          + (f", {songs} song(s) already played" if songs else "")
          + (" -- the last round had just ended" if between else "") + ".")


def songs_in_round():
    return max(0, state.transition_count - state.round_start_transition)


def _leader():
    """Player id with the most raw ROUND points (ties: whoever joined first), or None if nobody scored."""
    best_id, best = None, 0
    for pid, p in state.quiz_players.items():
        if p.get("score", 0) > best:
            best_id, best = pid, p.get("score", 0)
    return best_id


def note_round_end(reason, winner_id):
    """win_sequence_engine.start(): record the finished round and move on to the next one."""
    from drivers import player_profile
    rows = sorted(state.quiz_players.items(), key=lambda kv: kv[1].get("score", 0), reverse=True)
    winner = state.quiz_players.get(winner_id) or {}
    state.round_results.append({
        "n": state.round_index + 1, "name": state.round_name, "type": state.round_type,
        "reason": reason, "goal": state.round_goal, "songs": songs_in_round(),
        "winner": winner.get("name") or winner.get("initials") or "",
        "board": [{"player_id": pid, "name": p.get("name") or p.get("initials", "???"),
                   "emoji": p.get("emoji") or player_profile.DEFAULT_EMOJI,
                   "score": p.get("score", 0)} for pid, p in rows[:10] if p.get("score", 0) > 0],
    })
    del state.round_results[:-50]
    state.last_round_reason = reason
    state.round_index += 1


def end_round(reason):
    """Ends the running round now with the leader on raw points as its winner (nobody scored: no
    winner, straight to the intermission)."""
    if state.win_sequence_active or state.intermission_active or state.game_winner_player_id:
        return False
    from drivers import win_sequence_engine
    win_sequence_engine.start(_leader(), reason=reason)
    return True


def _fall_back_to_trivia(why):
    print(f"[ROUND] {state.round_name}: {why} -- playing it as a standard trivia round instead.")
    state.set_message("TRIVIA ROUND INSTEAD", 2.0)
    _apply_trivia(_spec_for(state.round_index))
    state.round_start_transition = state.transition_count


def _update_price_round(now):
    """"Wait, how much?": Price Game questions (local bank) back to back until the round's question
    count, or someone reaches the goal. The playlist plays on underneath (see holds_song())."""
    from drivers import price_game_engine, deck_orchestrator
    if _sp["asking"]:
        if state.mode == state.MODE_GAME:
            _sp["saw_game"] = True
            return
        if state.price_game_active:
            return      # strobe / banner intro still running
        _sp["asking"] = False
        _sp["next_at"] = now + config.ROUND_PRICE_GAP_SECONDS
        if _sp["saw_game"]:
            state.round_special_count += 1
            print(f"[ROUND] {state.round_name}: price question {state.round_special_count}"
                  f"/{state.round_song_limit} done.")
            if state.round_special_count >= state.round_song_limit:
                end_round("questions")
            return
        _sp["fails"] += 1   # the intro ran but no question could be shown
        if _sp["fails"] >= 3:
            print(f"[ROUND] {state.round_name}: no price questions available -- ending the round.")
            end_round("questions")
        return
    if state.mode != state.MODE_DJ or state.medley_active or deck_orchestrator.has_pending_move():
        return
    if now < _sp["next_at"]:
        return
    if state.ff_intro_started_at:
        # The round's intro (waitHowMuch.wav + words): hold until red / NEXT TRACK / the hold time.
        # The playlist comes back up as the jingle ends -- no silence while the board holds.
        from audio.audio_engine import is_game_music_playing
        el = now - state.ff_intro_started_at
        if not _sp.get("music_back") and (el >= config.WHM_INTRO_MUSIC_BACK_AT
                                          or (el > 1.0 and not is_game_music_playing())):
            _sp["music_back"] = True
            _restore_song_under_intro()
        last = max(w[0] for w in config.WHM_INTRO_WORDS)
        if not (_sp["intro_skip"] or el >= last + config.FF_INTRO_HOLD_SECONDS):
            return
        _end_ff_intro()
        _sp["intro_done"] = True
    if _sp["asked_tc"] is None and not _sp.get("intro_done"):
        # Before the round's first question: let the song be heard (and its announcement end), then
        # the intro.
        started = state.now_playing_started_at or 0
        if not started or now < max(state.mystery_defer_until, started + config.ROUND_PRICE_SETTLE_SECONDS):
            return
        _begin_ff_intro(now, "whm")
        return
    first = _sp["asked_tc"] is None
    _sp["asked_tc"] = state.transition_count
    # After the intro the playlist just plays on like a jukebox: no strobe, no game music.
    price_game_engine.start_price_game_from_bank(quick=True, music=False)
    if state.price_game_active or state.mode == state.MODE_GAME:
        _sp.update(asking=True, saw_game=state.mode == state.MODE_GAME, asked_at=now)
        state.set_message(f"QUESTION {state.round_special_count + 1} OF {state.round_song_limit}", 1.5)
    else:
        _sp["fails"] += 1
        _sp["next_at"] = now + config.ROUND_PRICE_GAP_SECONDS
        if _sp["fails"] >= 3:
            print(f"[ROUND] {state.round_name}: no price questions available -- ending the round.")
            end_round("questions")


def _begin_ff_intro(now, kind="ff"):
    """Board wipes dark, the intro jingle plays (fastestFinger.wav / waitHowMuch.wav), the marquee runs
    its outer-edge chase and the words land on cue (graphics/matrix_canvas.py::_draw_ff_intro). The
    song underneath is faded out."""
    from audio.audio_engine import play_random_game_music
    from drivers import deck_orchestrator, midi_driver
    state.ff_intro_started_at = now
    state.ff_intro_kind = kind
    _sp["intro_skip"] = False
    try:
        deck_orchestrator.dj_engine.ramp_volume("deck1" if state.active_deck == 1 else "deck2", 0.0, 0.8)
    except Exception as e:
        print(f"[ROUND] Fastest Finger intro: deck fade failed: {e}")
    midi_driver.tween_channel_faders_to(0, 0.8)
    play_random_game_music(config.WHM_INTRO_SOUND if kind == "whm" else config.FF_INTRO_SOUND)
    print(f"[ROUND] {state.round_name}: {'Wait, how much?' if kind == 'whm' else 'Fastest Finger'} intro.")
    _sp["whm_song"] = None
    if kind == "whm" and state.round_decade:
        # 2026-11-22: the round opens on a NEW song from its decade as the jingle ends (cued now so it
        # is decoded by then), not the old song faded back up.
        try:
            from drivers import show_runner
            path = show_runner.next_round_song()
        except Exception as e:
            print(f"[ROUND] Intro: no {state.round_decade} song picked: {e}")
            path = None
        if path and path != state.now_playing_path:
            _sp["whm_song"] = path
            deck_orchestrator.set_cued_track(path)


def _restore_song_under_intro():
    """"Wait, how much?" jingle ending: a fresh song from the round's decade crossfades in (picked at
    the intro's start); with no decade, the playlist song (faded for it) comes straight back up."""
    from drivers import deck_orchestrator, midi_driver
    midi_driver.tween_channel_faders_to(state.music_volume, config.WHM_INTRO_MUSIC_BACK_SECONDS)
    path = _sp.pop("whm_song", None)
    if path and path != state.now_playing_path and not deck_orchestrator.has_pending_move():
        deck_orchestrator.set_cued_track(path)
        deck_orchestrator.trigger_quiet_move()     # seamless: no banner / wipe over the intro board
        from drivers import auto_dj_engine
        auto_dj_engine.notify_manual_track_move()
        print(f"[ROUND] {state.round_name}: jingle ending -- a new {state.round_decade} song comes in.")
        return
    try:
        deck_orchestrator.dj_engine.ramp_volume("deck1" if state.active_deck == 1 else "deck2", 1.0,
                                                config.WHM_INTRO_MUSIC_BACK_SECONDS)
    except Exception as e:
        print(f"[ROUND] Intro: deck restore failed: {e}")
    print(f"[ROUND] {state.round_name}: jingle ending -- playlist back up.")


def _end_ff_intro():
    from audio.audio_engine import fade_out_game_music
    from drivers import deck_orchestrator, midi_driver
    state.ff_intro_started_at = 0.0
    fade_out_game_music(600)
    midi_driver.tween_channel_faders_to(state.music_volume, 1.0)
    if state.ff_intro_kind == "whm":
        try:    # the song was faded for the jingle; bring it back (the medley replaces it in Fastest Finger)
            deck_orchestrator.dj_engine.ramp_volume("deck1" if state.active_deck == 1 else "deck2", 1.0, 1.0)
        except Exception as e:
            print(f"[ROUND] Intro: deck restore failed: {e}")


def _update_medley_round(now):
    """Fastest Finger: the intro, then one Dance Medley (with its TRUE/FALSE game) of the round's clip
    count; the round ends when the medley does (medley_finished)."""
    if state.ff_intro_started_at:
        last = max(w[0] for w in config.FF_INTRO_WORDS)
        if _sp["intro_skip"] or now - state.ff_intro_started_at >= last + config.FF_INTRO_HOLD_SECONDS:
            _end_ff_intro()
            from drivers import medley_engine
            medley_engine.release_hold()
            state.set_message("FASTEST FINGER!", 2.0)
        return
    if _sp["medley"] or now < _sp["began_at"] + config.ROUND_SPECIAL_START_DELAY_SECONDS or now < _sp["retry_at"]:
        return
    if state.mode != state.MODE_DJ or state.price_game_active or state.mystery_active:
        _sp["retry_at"] = now + 1.0
        return
    from drivers import medley_engine
    state.medley_game_enabled = True    # the round IS the medley game
    from drivers import show_runner
    # 2026-11-24: the show's decades, and its playlist songs win close picks
    r = medley_engine.start(songs=state.round_song_limit, hold=True, decades=show_runner.show_decades(),
                            prefer=[t["path"] for t in show_runner.upcoming_tracks()])
    if r.get("ok"):
        _sp["medley"] = True
        state.round_song_limit = int(r.get("tracks") or state.round_song_limit)
        print(f"[ROUND] {state.round_name}: Fastest Finger medley planned, {state.round_song_limit} clips.")
        _begin_ff_intro(now)
        return
    err = str(r.get("error") or "")
    if "mixable" in err:
        _fall_back_to_trivia(f"no medley possible ({err})")
    else:
        _sp["retry_at"] = now + 2.0     # transition in progress etc.: try again shortly


_pending_handoff = {"on": False}


def medley_finished(handoff=True):
    """drivers/medley_engine.py::_finish: the round's medley is over (played out, the host ended the
    vocal-drop bed, or the operator moved on) -- the leader on points takes the round. The winner is
    announced BEFORE the next song starts: returns True when the medley's hand-off to the next song is
    deferred until the win celebration's applause is over (_maybe_handoff)."""
    if state.round_type != "fastest_finger" or not _sp.get("medley"):
        return False
    _sp["medley"] = False
    if state.ff_intro_started_at:
        _end_ff_intro()
    state.round_special_count = state.round_song_limit
    ended = end_round("medley")
    if ended and handoff:
        _pending_handoff["on"] = True
        return True
    return False


def _maybe_handoff():
    if not _pending_handoff["on"]:
        return
    if state.win_sequence_active and state.win_sequence_phase in ("duck", "applause"):
        return
    _pending_handoff["on"] = False
    from drivers import deck_orchestrator
    state.tempo_operator_set = False
    deck_orchestrator.trigger_track_move("next")
    print("[ROUND] Fastest Finger winner announced -- on to the next song.")


def end_solo_round(team, won, delta, challenge):
    """drivers/solo_challenge.py: a solo-challenge ROUND's challenge is over -- the round ends (no
    celebration; straight to the intermission, or the outro after the last round)."""
    if state.round_type != "solo_challenge" or state.intermission_active or state.win_sequence_active:
        return
    from drivers import win_sequence_engine
    win_sequence_engine.start(None, reason="solo")
    if state.round_results:
        state.round_results[-1].update(winner=team if won else "",
                                       solo={"team": team, "won": bool(won), "delta": delta, "challenge": challenge})


def update(now):
    """Per frame (drivers/show_engine.py::update): ends the round when its songs have run out, and runs
    the non-trivia rounds' own questions."""
    _maybe_handoff()
    if state.ff_intro_started_at and (state.show_phase != "live"
                                      or (state.ff_intro_kind == "ff" and not state.medley_active)
                                      or (state.ff_intro_kind == "whm" and state.round_type != "wait_how_much")):
        _end_ff_intro()     # show stopped / medley gone / round changed mid-intro
    if state.show_phase != "live" or state.show_background:
        return
    if state.intermission_active or state.win_sequence_active or state.game_winner_player_id:
        return
    from drivers import solo_challenge
    if state.round_type == "solo_challenge" and state.round_solo_game == "cliffhanger":
        from drivers import cliffhanger_engine
        if not solo_challenge.active():
            cliffhanger_engine.pregame_tick(now)    # a Cliffhanger round opens on its pregame
        cliffhanger_engine.auto_tick(now)           # AUTO HOST (2026-11-23), when switched on
        return
    if solo_challenge.active() or state.round_type == "solo_challenge":
        return      # a challenge holds everything; a solo round waits for the host
    if state.round_type == "wait_how_much":
        _update_price_round(now)
        return
    if state.round_type == "fastest_finger":
        _update_medley_round(now)
        return
    limit = state.round_song_limit
    if limit <= 0 or not state.quiz_players:
        return
    n = songs_in_round()
    if n < limit:
        return
    over = n > limit    # the next song already started: don't wait for a quiet moment
    duration = state.now_playing_duration or 0
    started = state.now_playing_started_at or 0
    near_end = bool(duration and started and now - started >= duration - config.ROUND_END_BEFORE_SONG_END_SECONDS)
    if over or (near_end and state.mode == state.MODE_DJ):
        print(f"[ROUND] {state.round_name}: out of songs ({n}/{limit}) -- ending it on points.")
        end_round("songs")


def status():
    """Admin card / API."""
    songs = songs_in_round()
    night = []
    for pid, p in state.quiz_players.items():
        night.append({"name": p.get("name") or p.get("initials", "???"),
                      "round": p.get("score", 0),
                      "night": state.show_cumulative_scores.get(pid, 0) + p.get("score", 0)})
    night.sort(key=lambda r: r["night"], reverse=True)
    nxt = _spec_for(state.round_index)
    if state.round_type == "fastest_finger":
        songs = state.medley_pos if state.medley_active else state.round_special_count
    elif state.round_type != "trivia":
        songs = state.round_special_count
    return {
        "round_number": state.round_index + 1, "name": state.round_name, "type": state.round_type,
        "solo_game": state.round_solo_game if state.round_type == "solo_challenge" else "",
        "unit": UNITS.get(state.round_type, "song"),
        "goal": state.round_goal, "songs_played": songs, "song_limit": state.round_song_limit,
        "in_intermission": bool(state.intermission_active),
        "planned": [{"name": r.get("name") or f"Round {i + 1}", "type": r.get("type") or "trivia"}
                    for i, r in enumerate(state.rounds)],
        "next": nxt, "exhausted": rounds_exhausted(),
        "results": state.round_results[-10:], "night": night[:10],
    }
