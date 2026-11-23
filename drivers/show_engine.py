"""drivers/show_engine.py
Trivia Night show flow. Phases (state.show_phase):

  setup     -- the boot menu on the LED panels (drivers/panel_menu.py): network
               page, Load Show, the designer pages. Replaced the old Setup
               screen + AUTO countdown + jukebox auto-start (2026-10-17).
  countdown -- a web-scheduled "Start Game at 7PM".
  dark      -- BLACK: a saved show is loaded and waiting. Any panel button (or
               the joystick / the remote) starts it; a hands-off boot starts it
               by itself.
  intro     -- introTheme.wav + the LED/marquee choreography. With an operator
               there the theme loops ducked under the host until a button
               press; a hands-off boot moves on after ~10s. Then: the wheel
               (if the playlist opens with a spin) -> its decade stab, or
               LetsPlayStab -> the first song, 1s before the stab ends.
  live      -- the show (drivers/show_runner.py plays/records the saved show).
               A spin item mid-show fades the song out, spins the wheel, plays
               the decade stab and dovetails into the next song.
  outro     -- ShowEnd.mp3 + champion banner; afterwards the rig plays
               easy-listening background music (live, show_background) with
               every light dark until a Simon button returns to the panel menu.

Rendering lives in graphics/matrix_canvas.py (+ graphics/menu_pages.py),
drivers/lighting_engine.py, drivers/wled_engine.py and drivers/accent_engine.py,
all reading state. update(now) is pumped from inputs/gamepad.py::process_events().
"""
import os
import random
import time

import config
from state import state
from drivers import midi_driver, deck_orchestrator, show_runner, show_library, show_bed
from audio.audio_engine import play_processed_sound, pick_random_applause, show_end_sound

# ShowEnd.mp3's channel during the outro -- update() keeps its volume in step
# with state.music_volume (play_processed_sound only sets it once).
_show_music_channel = None
_outro_music_started = False

# The running open/spin sequence: {"context": "intro"|"spin", "stage": ...,
# "at": when the next stage starts, "decade": wheel target or "", "path": song}.
_seq = None
_intro_moved_on = False


def mark_operator_interaction():
    """Any physical control (joystick, keyboard) was used: the boot is no
    longer hands-off (drivers/panel_menu.py)."""
    from drivers import panel_menu
    panel_menu.mark_touched("joystick")


# ------------------------------------------------------------- starting
def _prepare(show):
    """Loads `show`'s settings and hands it to the runner."""
    show_library.apply_settings(show)
    from drivers import round_engine
    round_engine.reset_for_show(show)
    state.show_selected = show["name"]
    state.questions_suppressed = not show.get("questions", False)
    state.listen_enabled = bool(show.get("listen", False))  # phone "Listen to the show" checkbox
    state.show_outro_enabled = bool(show.get("outro", True))
    show_runner.begin(show)


def resolve_show(name=None):
    """The named show, else the selected one, else the last played one, else a
    new one from the designer defaults (so there is always something to run)."""
    for candidate in (name, state.show_selected):
        if candidate:
            show = show_library.get(candidate)
            if show:
                return show
    return show_library.last_played() or show_library.create()


def start_show(name=None, hands_off=False):
    """Panel menu / web: load a saved show and go to BLACK (dark). A hands-off
    start goes straight on into the intro."""
    if state.show_phase not in ("setup", "countdown", "outro", "live"):
        return False
    if state.show_phase == "live" and not state.show_background:
        return False
    from drivers import failover_engine
    if not failover_engine.start_allowed():
        print("[SHOW] Start refused -- the other Pi is running the show (TAKE CONTROL on this Pi's remote).")
        return False
    _stop_background()
    _prepare(resolve_show(name))
    state.show_hands_off = hands_off
    _enter_dark()
    if hands_off:
        begin_intro()
    return True


def enter_dark():
    """Web Setup page "Start Game" / a scheduled countdown reaching zero."""
    return start_show()


def _enter_dark():
    """Resets everything that means "fresh night" and holds BLACK until a
    button (begin_intro)."""
    global _show_music_channel, _seq
    if _show_music_channel is not None:
        _show_music_channel.fadeout(config.SHOW_DARK_ENTRY_FADE_MS)
        _show_music_channel = None
    show_bed.stop_all(config.SHOW_DARK_ENTRY_FADE_MS)
    deck_orchestrator.dj_engine.stop_decks()
    _seq = None

    # A previous show's question or mystery round must not carry over: it
    # would grade itself during the intro (its buzzer used to cut the intro
    # theme) and keep asking the old song's questions into the first track.
    from inputs import gamepad
    gamepad.cancel_question()

    _reset_asked_tracking()

    now = time.time()
    state.show_phase = "dark"
    state.show_phase_started_at = now
    state.show_scheduled_start_at = 0.0
    state.show_spin_active = False
    state.show_wheel_started_at = 0.0
    state.trivia_confirm_active = False
    state.game_round_number = 1
    state.show_cumulative_scores = {}
    state.last_round_winner_initials = ""
    state.last_round_number = 0
    state.last_round_board = []
    state.game_winner_player_id = ""
    state.quiz_history = []
    from drivers import results_archive
    state.show_session_id = results_archive.new_session_id()
    from drivers import category_round_engine
    category_round_engine.stop("reset for a new show")
    for player in state.quiz_players.values():
        player["score"] = 0
        player.update(streak=0, best_streak=0, correct=0, asked=0, last_points=0, streak_lost=0)
    print(f"[SHOW] BLACK -- '{state.show_selected}' loaded"
          f"{' (hands-off)' if state.show_hands_off else ', waiting for a button'}.")


def _reset_asked_tracking():
    """New show = nobody has heard anything yet. The "already asked" memory
    used to last the whole app session, so a restarted show skipped the
    "Who is this?" teaser (the thing that starts each song's questions) for
    every artist asked about in the earlier run, and the first song kept
    last run's half-used question queue."""
    from drivers import mystery_band_engine, ask_questions
    print(f"[SHOW] New show -- forgetting {len(state.asked_artists)} asked artist(s) and "
          f"{len(state.asked_question_texts)} asked question(s).")
    state.asked_artists.clear()
    state.mystery_asked_songs.clear()
    state.asked_question_texts.clear()
    state.mystery_track_key = ""          # re-evaluate the teaser even on the same song
    mystery_band_engine._pending_mystery = None
    ask_questions._pending = None
    state.factoid_track_key = ""          # first song reloads its full question list
    state.track_question_queue = []
    state.autoadv_questions_asked = 0
    state.autoadv_need_leaderboard = False
    state.autoadv_leaderboard_reviewed = False
    state.autoadv_track_key = ""
    state.autoadv_done_at = 0.0
    state.game_active = False             # fresh per-game question count / era slot


def resume_from(snap, own=False):
    """Backup takeover (drivers/failover_engine.py): pick the show up from the
    other Pi's last report -- same show, players, scores, round and asked
    questions, starting on the NEXT song. Returns a message for the remote.
    own=True is RESUME LAST SHOW: the report is this machine's own saved one."""
    global _seq
    phase = snap.get("phase", "setup")
    info = snap.get("show") or {}
    name = info.get("name", "")
    if phase in ("setup", "countdown") or not name:
        return "There is no show to resume." if own else "The other Pi wasn't running a show -- this Pi is now in charge."
    if phase == "outro" or snap.get("background"):
        return "The last show was already over." if own else "The other Pi's show was already over -- this Pi is now in charge."

    # Make sure we have the show (the web sync may not have brought it yet).
    if info.get("data"):
        try:
            show_library.merge([info["data"]], {})
        except Exception as e:
            print(f"[FAILOVER] Couldn't merge the show copy: {e}")
    show = show_library.get(name) or info.get("data")
    if not show:
        return f"Show '{name}' isn't on this Pi."

    if phase in ("dark", "intro"):
        state.show_phase = "setup"
        start_show(name)
        _restore_players(snap)
        if phase == "intro":
            begin_intro()
        return f"{'Resumed' if own else 'Took over'} '{name}' at the {'intro' if phase == 'intro' else 'BLACK hold'}."

    # Live: load the show quietly, restore the room, go straight to the next song.
    _stop_background()
    from inputs import gamepad
    gamepad.cancel_question()
    _reset_asked_tracking()
    _prepare(show)
    _restore_players(snap)
    from drivers import round_engine
    round_engine.restore(snap.get("rounds"))
    path, decade = show_runner.resume_point(info)
    now = time.time()
    state.show_hands_off = False
    state.show_intro_waiting = False
    state.show_scheduled_start_at = 0.0
    state.show_spin_active = False
    state.show_wheel_started_at = 0.0
    state.trivia_confirm_active = False
    if path is None and show_runner.mode() == "play":
        state.show_phase = "live"
        state.show_phase_started_at = now
        stop_show()
        return f"'{name}' was on its last song -- went to the outro."
    state.show_phase = "intro"      # _start_song() flips it to live as the song lands
    state.show_phase_started_at = now
    _seq = {"context": "resume", "stage": "pre", "at": 0.0, "decade": decade, "path": path}
    nxt = info.get("next_title") or (os.path.basename(path) if path else "a new song")
    return f"{'Resumed' if own else 'Took over'} '{name}' -- next song: {nxt}."


def _restore_players(snap):
    """Players, scores, round and what's been asked, from the other Pi."""
    players = {}
    for pid, p in (snap.get("players") or {}).items():
        players[pid] = {"initials": p.get("initials", "???"), "selected_index": -1, "locked": False,
                        "score": int(p.get("score", 0) or 0),
                        "name": p.get("name") or p.get("initials", "Team"),
                        "emoji": p.get("emoji") or "🎵",
                        "streak": int(p.get("streak", 0) or 0), "best_streak": int(p.get("best_streak", 0) or 0),
                        "correct": int(p.get("correct", 0) or 0), "asked": int(p.get("asked", 0) or 0),
                        "last_points": 0,
                        "avatar_color": p.get("avatar_color") or state.AVATAR_COLORS[0]}
    state.quiz_players = players
    state.show_cumulative_scores = {k: int(v) for k, v in (snap.get("cumulative") or {}).items()}
    state.game_round_number = int(snap.get("round", 1) or 1)
    state.asked_artists = set(snap.get("asked_artists") or [])
    state.mystery_asked_songs = set(snap.get("mystery_asked_songs") or [])
    state.asked_question_texts = set(snap.get("asked_questions") or [])
    s = snap.get("settings") or {}
    for key in ("questions_suppressed", "listen_enabled", "show_outro_enabled", "auto_advance_after_quiz",
                "auto_dj_enabled", "auto_announce_enabled", "sweepers_enabled", "sfx_enabled"):
        if key in s and hasattr(state, key):
            setattr(state, key, bool(s[key]))
    print(f"[FAILOVER] Restored {len(players)} player(s), round {state.game_round_number}, "
          f"{len(state.asked_question_texts)} asked question(s).")


def begin_intro():
    """BLACK -> the open. Any panel button, the joystick's X-, the remote's
    BEGIN, or a hands-off boot."""
    global _seq, _intro_moved_on
    if state.show_phase != "dark":
        return
    show = show_library.get(state.show_selected) or {}
    now = time.time()
    state.show_phase = "intro"
    state.show_phase_started_at = now
    state.show_wheel_started_at = 0.0
    state.show_wheel_landed = False
    state.show_wheel_target = ""
    _intro_moved_on = False
    _seq = None
    decade, path = show_runner.opening()
    _seq = {"context": "intro", "stage": "hold", "at": 0.0, "decade": decade, "path": path}
    if path:
        deck_orchestrator.set_cued_track(path)   # decode it now so it starts on the beat
    if show.get("intro", True):
        show_bed.start_intro()
        state.show_intro_waiting = True
        print(f"[SHOW] Intro started ({'hands-off' if state.show_hands_off else 'waiting for the host'}"
              f"{', wheel -> ' + decade if decade else ''}).")
    else:
        state.show_intro_waiting = False
        print("[SHOW] No intro -- straight in.")
        _move_on(now, swell=False)


def skip_intro():
    """The host is done talking: a panel button, the joystick's X-, the
    remote's PLAY TRACK. Moves the intro on (once)."""
    if state.show_phase != "intro" or _intro_moved_on:
        return
    _move_on(time.time(), swell=True)


def _move_on(now, swell):
    """Intro theme swells back up and fades; then the wheel, a stab, or the song."""
    global _intro_moved_on
    _intro_moved_on = True
    state.show_intro_waiting = False
    delay = show_bed.release_intro(swell=swell)
    _seq["stage"] = "pre"
    _seq["at"] = now + delay


def start_spin(decade, path):
    """drivers/show_runner.py (via deck_orchestrator): a spin item comes before
    the next song -- fade the song out, then the wheel, the stab, the song."""
    global _seq
    if state.show_spin_active:
        return
    now = time.time()
    state.show_spin_active = True
    state.show_wheel_started_at = 0.0
    state.show_wheel_landed = False
    state.show_wheel_target = decade
    deck_orchestrator.set_cued_track(path)
    midi_driver.tween_channel_faders_to(0, config.SHOW_SPIN_DECK_FADE_SECONDS)
    _seq = {"context": "spin", "stage": "fade_deck", "at": now + config.SHOW_SPIN_DECK_FADE_SECONDS,
            "decade": decade, "path": path}
    if state.mode == state.MODE_SIMON:
        from drivers import simon_engine
        simon_engine.exit_simon()
    print(f"[SHOW] Spin before the next song (lands on {decade}).")


def _start_wheel(now):
    state.show_wheel_target = _seq["decade"]
    state.show_wheel_landed = False
    state.show_wheel_spin_seconds = (max(config.SHOW_WHEEL_MIN_SPIN_SECONDS, show_bed.wheel_theme_seconds())
                                     + config.SHOW_WHEEL_EXTRA_SECONDS)
    state.show_wheel_started_at = now
    show_bed.start_wheel_theme()
    _seq["stage"] = "wheel"
    _seq["at"] = now + state.show_wheel_spin_seconds


def _land_wheel(now):
    show_bed.stop_wheel_theme()
    state.show_wheel_landed = True
    state.show_chosen_decade = _seq["decade"]
    length = show_bed.play_decade_stab(_seq["decade"])
    _seq["stage"] = "stab"
    _seq["at"] = now + max(0.0, length - config.SHOW_STAB_LEAD_SECONDS)
    print(f"[SHOW] Wheel landed on {_seq['decade']}.")


def _update_seq(now):
    global _seq
    if _seq is None or _seq["stage"] == "hold" or now < _seq["at"]:
        return
    stage = _seq["stage"]
    if stage == "fade_deck":
        deck_orchestrator.dj_engine.stop_decks()
        _start_wheel(now)
    elif stage == "pre":
        if _seq["decade"]:
            _start_wheel(now)
        elif _seq["context"] == "intro" and (show_library.get(state.show_selected) or {}).get("intro", True):
            length = show_bed.play_lets_play()
            _seq["stage"] = "stab"
            _seq["at"] = now + max(0.0, length - config.SHOW_STAB_LEAD_SECONDS)
        else:
            _start_song(now)
    elif stage == "wheel":
        _land_wheel(now)
    elif stage == "stab":
        _start_song(now)


def _start_song(now):
    """The show's next song comes in (dovetailed under the stab's tail)."""
    global _seq
    seq, _seq = _seq, None
    if seq and seq.get("path"):
        deck_orchestrator.set_cued_track(seq["path"])
    if state.show_phase == "intro":
        state.show_phase = "live"
        state.show_phase_started_at = now
        # Panels stay black until the first song's transition fires, so the
        # previous song's board never flashes up first (2026-10-26).
        state.show_first_song_pending_at = time.time()
        print("[SHOW] Intro finished -- live show begins.")
    midi_driver.tween_channel_faders_to(state.music_volume, config.SHOW_SONG_DOVETAIL_FADE_SECONDS)
    state.board_wipe_fresh = True   # the wheel/intro must not slide out over the new song
    deck_orchestrator.trigger_dovetail_move()
    state.show_spin_active = False
    state.show_wheel_started_at = 0.0


# --------------------------------------------------------- countdown
def schedule_start(target_epoch):
    """'Start Game at 7PM' (web Setup page)."""
    if state.show_phase not in ("setup", "outro") and not state.show_background:
        return False
    state.show_phase = "countdown"
    state.show_phase_started_at = time.time()
    state.show_scheduled_start_at = target_epoch
    return True


def abort_schedule():
    """Countdown page's ABORT button -- back to the panel menu."""
    if state.show_phase != "countdown":
        return False
    state.show_scheduled_start_at = 0.0
    back_to_menu()
    return True


# ------------------------------------------------------------ ending
def accumulate_round_scores():
    """Folds every player's CURRENT per-round score into the night-long
    cumulative tally (win_sequence_engine.start() and stop_show())."""
    for player_id, player in state.quiz_players.items():
        score = player.get("score", 0)
        if score:
            state.show_cumulative_scores[player_id] = (
                state.show_cumulative_scores.get(player_id, 0) + score
            )


def stop_show():
    """STOP GAME (web remote), a saved show's last song ending, or a cut on the
    last song -- straight to the outro."""
    if state.show_phase != "live" or state.show_background:
        return False
    accumulate_round_scores()

    from inputs import gamepad  # lazy: gamepad imports this module for update()
    gamepad.abort_game_mode_early()

    state.win_sequence_active = False
    state.win_sequence_phase = ""
    state.intermission_active = False
    state.intermission_finishing = False
    state.intermission_paused = False
    state.intermission_paused_remaining = 0.0
    state.game_winner_player_id = ""
    for player in state.quiz_players.values():
        player["score"] = 0

    start_outro()
    return True


def start_outro():
    """Fades the deck; ShowEnd.mp3 + applause start SHOW_OUTRO_DECK_FADE_SECONDS
    later (_start_outro_music); then background music (_start_background)."""
    global _show_music_channel, _outro_music_started, _seq
    if state.show_phase == "outro":
        return
    show_runner.note_show_end()
    show_bed.stop_all(400)
    _seq = None
    now = time.time()
    try:
        from drivers import results_archive
        results_archive.snapshot()
    except Exception as exc:   # never let the archive break the outro
        print(f"[RESULTS] snapshot failed: {exc}")
    _end_questions()
    state.show_phase = "outro"
    state.show_phase_started_at = now
    state.show_final_round_number = None
    state.show_spin_active = False
    state.show_song_ending = ""

    _show_music_channel = None
    _outro_music_started = False
    midi_driver.tween_channel_faders_to(0, config.SHOW_OUTRO_DECK_FADE_SECONDS)

    if not state.show_outro_enabled:
        # Outro switched off in the designer: no ShowEnd/applause/banner. Once
        # the deck has faded, update() hands over to the mellow music + blackout.
        _outro_music_started = True
        state.show_outro_message = ""
        state.show_outro_music_ends_at = now + config.SHOW_OUTRO_DECK_FADE_SECONDS - 1.0
        print("[SHOW] Outro suppressed -- fading to blackout + mellow music.")
        return
    state.show_outro_message = _compute_champion_message()
    state.show_outro_music_ends_at = (
        now + config.SHOW_OUTRO_DECK_FADE_SECONDS + show_end_sound.get_length())
    print(f"[SHOW] Outro started -- {state.show_outro_message!r}")


def _end_questions():
    """The night is over (2026-11-22): whatever question / teaser / challenge is up goes away and none
    of the song's queued questions follow. The last round ending used to go to the outro with the
    question machinery still armed, so questions carried on over the outro."""
    try:
        from inputs import gamepad
        gamepad.cancel_question()
    except Exception as exc:
        print(f"[SHOW] Outro: cancelling the question failed: {exc}")
    try:
        from drivers import solo_challenge, round_engine
        if solo_challenge.playing():
            solo_challenge.cancel()
        if state.ff_intro_started_at:
            round_engine._end_ff_intro()
    except Exception as exc:
        print(f"[SHOW] Outro: stopping the challenge / intro failed: {exc}")
    state.track_question_queue = []
    state.trivia_confirm_active = False
    state.mode = state.MODE_DJ


def _start_outro_music():
    global _show_music_channel
    deck_orchestrator.dj_engine.stop_decks()
    _show_music_channel = play_processed_sound(show_end_sound)
    play_processed_sound(pick_random_applause())


def _compute_champion_message():
    scores = state.show_cumulative_scores
    if not scores:
        return "THANKS FOR PLAYING!"
    top = max(scores.values())
    initials = [
        state.quiz_players[pid]["initials"]
        for pid, s in scores.items()
        if s == top and pid in state.quiz_players
    ]
    if not initials:
        return "THANKS FOR PLAYING!"
    names = " & ".join(initials)
    verb = "WINS" if len(initials) == 1 else "WIN"
    return f"{names} {verb} TRIVIA NIGHT!"


# ------------------------------------------------ after the show
def _pick_energy(low=None, high=None):
    from drivers.music_library import music_library, sanitize_track_key
    from drivers.music_metadata_engine import get_metadata_for
    pool = []
    for t in music_library.all_tracks():
        meta = get_metadata_for(sanitize_track_key(t["title"], t["artist"])) or {}
        rank = meta.get("energy_rank")
        if not rank:
            continue
        if (low is None or rank >= low) and (high is None or rank <= high):
            pool.append(t)
    return random.choice(pool)["path"] if pool else None


def _start_background(now):
    """The outro is over: easy-listening background music, every light dark
    (state.lights_out) until a Simon button returns to the panel menu."""
    global _seq
    _seq = None
    show_bed.stop_all(400)
    state.show_spin_active = False
    state.show_wheel_started_at = 0.0
    state.show_background = True
    state.show_phase = "live"
    state.show_phase_started_at = now
    state.questions_suppressed = True
    state.show_energy_max = config.SHOW_BACKGROUND_ENERGY_MAX
    state.show_require_decade = False
    state.show_allowed_decades = []
    path = _pick_energy(high=config.SHOW_BACKGROUND_ENERGY_MAX)
    if path:
        deck_orchestrator.set_cued_track(path)
    midi_driver.tween_channel_faders_to(
        state.music_volume * config.SHOW_BACKGROUND_VOLUME_PCT / 100.0, 3.0)
    deck_orchestrator.trigger_plain_move()
    print("[SHOW] Background music after the show, lights out (a panel press returns to the menu).")


def _stop_background():
    state.show_background = False
    state.show_energy_max = 0


def start_encore():
    """A panel press during the background music: someone wants to play.
    On-the-fly, energy up, jukebox (blue = TRIVIA QUESTION?); not recorded."""
    if not state.show_background:
        return
    _stop_background()
    state.show_phase_started_at = time.time()
    state.game_round_number = 1
    state.show_cumulative_scores = {}
    path = _pick_energy(low=config.SHOW_ENCORE_ENERGY_MIN)
    if path:
        deck_orchestrator.set_cued_track(path)
    midi_driver.tween_channel_faders_to(state.music_volume, 1.0)
    deck_orchestrator.trigger_announced_track_move()
    print("[SHOW] Encore! Energy up.")


def back_to_menu():
    """Admin remote: full reset -- stop everything and bring the panel menu
    back (Load Show)."""
    global _seq, _show_music_channel
    show_runner.stop()
    show_bed.stop_all(400)
    _seq = None
    if _show_music_channel is not None:
        _show_music_channel.fadeout(600)
        _show_music_channel = None
    from inputs import gamepad
    gamepad.abort_game_mode_early()
    _stop_background()
    midi_driver.tween_channel_faders_to(0, 1.5)
    state.show_spin_active = False
    state.show_wheel_started_at = 0.0
    state.show_intro_waiting = False
    state.show_phase = "setup"
    state.show_phase_started_at = time.time()
    from drivers import panel_menu
    panel_menu.reset(touched=True)
    _pending_deck_stop[0] = time.time() + 1.6
    print("[SHOW] Back to the panel menu.")


def new_game_setup():
    """Admin START NEW GAME: same full stop + music fade as back_to_menu(), but the panels and HDMI
    board stay BLANK while the operator sets the next game up on the admin page (a physical panel
    button press brings the panel menu back)."""
    back_to_menu()
    state.setup_blank = True
    print("[SHOW] New game: stopped, music fading, panels blank for setup.")


_pending_deck_stop = [0.0]
def _reveal_sweeper(now):
    """A random sweeper at the moment a song-change wipe clears the announcement banner ("GET READY")
    and the new content (the first question) wipes in. drivers/deck_orchestrator.py arms
    state.reveal_sweeper_at for that instant (= the board wipe's hold end)."""
    at = state.reveal_sweeper_at
    if not at or now < at:
        return
    state.reveal_sweeper_at = 0.0
    from drivers import cliffhanger_engine, round_engine
    if cliffhanger_engine.active() or round_engine.price_round_live():
        return              # no sweepers during Cliffhanger or a "Wait, how much?" round
    if now - at < 1.5:      # not for a moment we only just caught up to (a stalled frame)
        show_bed.play_random_sweeper()


# ------------------------------------------------------------- frame
def update(now):
    """Per-frame pump, called from inputs/gamepad.py::process_events()."""
    global _outro_music_started, _show_music_channel
    from drivers import panel_menu
    if state.show_phase != "setup":
        state.setup_blank = False
    if state.show_phase == "setup":
        panel_menu.update(now)
    show_runner.update(now)
    from drivers import category_round_engine
    category_round_engine.update(now)
    show_bed.update(now)
    _reveal_sweeper(now)
    from drivers import round_engine, solo_challenge
    solo_challenge.update(now)
    round_engine.update(now)
    from drivers import failover_engine
    failover_engine.update(now)
    from drivers import selftest_engine
    try:
        selftest_engine.update(now)      # Setup page SELF TEST (2026-11-23)
    except Exception as exc:
        print(f"[SELFTEST] update failed: {exc!r}")

    if _pending_deck_stop[0] and now >= _pending_deck_stop[0]:
        _pending_deck_stop[0] = 0.0
        if state.show_phase == "setup":
            deck_orchestrator.dj_engine.stop_decks()

    if (state.show_phase == "countdown"
            and state.show_scheduled_start_at
            and now >= state.show_scheduled_start_at):
        start_show()

    if (state.show_phase == "intro" and state.show_intro_waiting and state.show_hands_off
            and now - state.show_phase_started_at >= config.SHOW_HANDS_OFF_INTRO_SECONDS):
        print("[SHOW] Hands-off: moving the intro on.")
        _move_on(now, swell=False)

    _update_seq(now)

    if (state.show_phase == "outro" and not _outro_music_started
            and now - state.show_phase_started_at >= config.SHOW_OUTRO_DECK_FADE_SECONDS):
        _outro_music_started = True
        _start_outro_music()

    if _show_music_channel is not None:
        if state.show_phase == "outro" and now < state.show_outro_music_ends_at:
            _show_music_channel.set_volume(state.music_volume / 100.0)
        else:
            _show_music_channel = None

    if (state.show_phase == "outro" and _outro_music_started
            and now >= state.show_outro_music_ends_at + 1.0):
        _start_background(now)
