import time
import json
import threading
import pygame
import config
from config import (
    VOLUME_HOLD_INITIAL_DELAY_SECONDS, VOLUME_HOLD_REPEAT_INTERVAL_SECONDS,
    BUTTON_DEBOUNCE_SECONDS, QUIZ_GATE_DEBOUNCE_SECONDS, QUIZ_GATE_EMPTY_CACHE_TIMEOUT_SECONDS,
    TEMPO_TAP_WINDOW,
    TEMPO_PERIOD_MIN_SECONDS, TEMPO_PERIOD_MAX_SECONDS,
    DJ_THEME_COUNT, DJ_COLOR_PALETTE,
    QUIZ_CELEBRATION_HOLD_SECONDS,
    DMX_GRADE_FLASH_SECONDS,
    BTN1_HOLD_THRESHOLD_SECONDS, BTN1_HOLD_OVERLAY_PERSIST_SECONDS,
    RELAY1_CHANNEL, RELAY_PULSE_SECONDS,
)
from drivers.dmx_driver import dmx
from drivers import wled_engine
from drivers import accent_engine
# SI_ENTRY_BUTTONS/SI_EXIT_BUTTONS/SHUTDOWN_COMBO_BUTTONS/
# SHUTDOWN_COMBO_HOLD_SECONDS/FORCE_PRICE_GAME_COMBO_BUTTONS/
# FORCE_PRICE_GAME_COMBO_HOLD_SECONDS no longer imported here (2026-08-20)
# -- drivers/joystick_bindings.py reads them once at module load to seed
# its default combos, and everything at runtime now goes through its
# combos()/buttons_for() lookups instead of these raw constants.
from state import state
from drivers.midi_driver import handle_dj_volume
from drivers.deck_orchestrator import get_now_playing as get_rekordbox_track
from drivers.factoid_engine import (
    ensure_prefetch, pull_next_from_queue, pull_by_category, build_mock_question,
    is_exhausted, load_exhausted_fallback_question, apply_mystery_identify_question,
)
from drivers import deck_orchestrator
from drivers import price_game_engine
from drivers import mystery_band_engine
from drivers import auto_dj_engine
from drivers import space_invaders_engine
from drivers import simon_engine
from drivers import simon_hardware
from drivers import westminster_engine
from drivers import idle_cycle_engine
from drivers import live_round_engine
from drivers import win_sequence_engine
from drivers import light_prefs_engine
from drivers import show_engine
from drivers import joystick_bindings
from graphics import overlay_panel
from graphics import secondary_canvas
from drivers import announcement_engine
from drivers import hot_track_engine
from drivers import relay_engine
from audio.audio_engine import (
    play_processed_sound, raw_buzzer, raw_bigwin, raw_clear, raw_ding,
    raw_coin, raw_buzz_short, stop_previous_audio, reverb_enabled
)

# Gamepad Axis State Tracking
last_axis_x = 0
last_axis_y = 0
joysticks = []

# Volume hold-to-repeat state (TV-remote style: press moves one step,
# holding keeps stepping until released).
_vol_hold_dir = 0
_vol_next_repeat = 0.0

# Btn1 tap-vs-hold state (Section: joystick remap + status overlays).
# JOYBUTTONDOWN arms _down_at and clears _hold_fired; the per-frame
# _process_btn1_hold() poll promotes a still-held press past _hold_fired
# once BTN1_HOLD_THRESHOLD_SECONDS elapses; JOYBUTTONUP's
# _handle_btn1_release() reads _hold_fired to decide tap vs. hold.
_btn1_down_at = None
_btn1_hold_fired = False

# Combo hold-tracking (Shutdown, Force Price Game, Space Invaders entry,
# and any user-defined ones) now lives in _combo_since/_combo_fired near
# _process_combos() further down -- one generalized mechanism instead of a
# separate pair of module globals per combo. NOT a joystick-axis long-press
# for Force Price Game specifically (an earlier X- hold version was tried
# and reverted): the X-axis is edge-triggered straight to
# deck_orchestrator.trigger_track_move("next") the instant it crosses the
# threshold, in the SAME JOYAXISMOTION handler, before any hold-duration
# check could ever tell a tap from a hold -- so holding X- also fired an
# unwanted, audience-visible track transition every single time. A
# multi-button combo has no such quick-tap side effect to worry about.

def init_joysticks():
    global joysticks
    pygame.joystick.init()
    count = pygame.joystick.get_count()
    print(f"\\n[JOYSTICK STATUS] Detected {count} controller(s).")
    joysticks = []
    for i in range(count):
        js = pygame.joystick.Joystick(i)
        js.init()
        joysticks.append(js)
        print(f"  -> Initialized: {js.get_name()}")

init_joysticks()

# ------------------------------------------
# GAME SHOW ANSWER-SELECTION HANDLERS
# ------------------------------------------
def trigger_loss(play_sfx=True):
    """`play_sfx=False`: used whenever a round is graded a loss because it
    timed out (state.round_timed_out), rather than via a real Grade-button
    press (2026-09-18, generalized from two narrower timeout-only cases --
    see both call sites' own comments for that history). A timed-out
    round's own visual (graphics/matrix_canvas.py's "TIMES UP!" + bare
    correct-answer reveal) never blames a specific wrong pick the way a
    real loss does, so an audible buzzer over it read as a mismatch between
    what's on screen and what's heard, even when someone HAD armed a
    (wrong) selection before time ran out. Still does the DMX/message
    bookkeeping either way, just skips the audible SFX."""
    stop_previous_audio()
    state.active_option = None
    state.set_message("WRONG ANSWER! (LOSS BUZZER)", 1.5)
    print("[ACTION] Btn6 GRADE -> LOSS")
    if state.sfx_enabled and play_sfx:
        play_processed_sound(raw_buzzer)
        wled_engine.flash(255, 0, 0)

    # Fixture 1 (win/loss indicator lamp): solid red, latched until an
    # explicit reset (board clear / new question / return to DJ mode) --
    # drivers/lighting_engine.py renders this every frame from state.
    state.fixture1_mode = "loss"
    state.fixture1_mode_set_at = time.time()

    # Amplified feedback: brief solid-red strobe pulse across ALL 10
    # uplight fixtures (2-11), rendered by
    # drivers/lighting_engine.py::_render_grade_flash -- Fixture 1 keeps its
    # own latched "loss" state above regardless of this pulse ending.
    state.fixture_flash_mode = "loss"
    state.fixture_flash_until = time.time() + DMX_GRADE_FLASH_SECONDS

def clear_quiz_selection():
    """Physical Btn5 (GAME_MODE-only swap with Btn6, see process_events) /
    keyboard 6: clears whichever answer is currently armed (or, if already
    graded, un-grades it) without replaying a win/loss -- lets the host
    recover from a mis-press."""
    stop_previous_audio()
    state.active_option = None
    state.quiz_selected_index = -1
    state.quiz_locked = False
    state.fixture1_mode = "off"  # Reset rule: board clear -> Fixture 1 black
    state.set_message("SELECTION CLEARED", 1.0)
    print("[ACTION] Btn5 -> CLEAR SELECTION")

def trigger_clear_latches():
    """Keyboard 'C': manual reset escape hatch. Clears any armed/graded
    selection and, if the currently-loaded question is the local TEST
    placeholder, rerolls a fresh one so the select/grade flow can be
    exercised repeatedly."""
    import audio.audio_engine as ae
    stop_previous_audio()
    state.active_option = None
    state.quiz_selected_index = -1
    state.quiz_locked = False
    state.fixture1_mode = "off"  # Reset rule: board clear -> Fixture 1 black

    if state.quiz_is_test:
        mock = build_mock_question()
        print("=== INCOMING QUESTION DATA (MOCK/TEST REROLL) ===")
        print(json.dumps(mock, indent=2))
        state.factoid_question = mock["question"]
        state.factoid_choices = mock["choices"]
        state.factoid_correct_index = mock["correct_index"]
        state.factoid_correction = ""

    ae.reverb_enabled = not ae.reverb_enabled
    state_str = "ON" if ae.reverb_enabled else "OFF"
    state.set_message(f"CLEAR LATCHES | REVERB: {state_str}", 1.2)
    print(f"[ACTION] Keyboard 'C' -> CLEAR LATCHES | REVERB: {state_str}")
    play_processed_sound(raw_clear)

def select_quiz_answer(index):
    """Arms (but does not grade) the chosen answer: dim red fill on that
    panel (matrix_canvas._draw_selected_panel). A short ding confirms the
    selection prior to lock-in. Grading happens separately via
    grade_quiz_selection() on Btn6 (GAME_MODE-only swap with Btn5)."""
    if state.quiz_locked:
        return
    if not state.factoid_choices or state.factoid_correct_index < 0:
        print("[ACTION] Answer button pressed but no quiz is loaded.")
        return
    if index < 0 or index >= len(state.factoid_choices):
        return

    letter = "ABCD"[index]
    state.quiz_selected_index = index
    state.active_option = letter
    print(f"[ACTION] Answer {letter} armed (not yet graded)")

    stop_previous_audio()
    if state.sfx_enabled:
        play_processed_sound(raw_ding)

def select_and_grade_quiz_answer(index):
    """Physical arcade-button hook (drivers/simon_engine.py::poll_hardware(),
    any live/ungraded round -- 2026-09-16): a single button press both arms
    AND grades the answer in one shot, unlike the joystick's two-step
    select_quiz_answer()/grade_quiz_selection() (game_select_N then
    game_grade).

    Bounds-checked here (not left to select_quiz_answer()'s own silent
    no-op) because there are only 4 physical buttons but as few as 2 live
    choices (True/False): an out-of-range press (yellow/blue on a T/F
    question) must be a total no-op, not fall through to grading whatever
    selection happened to already be armed (a prior valid press this same
    round) or, worse, force-ending a multiplayer round on nothing but a
    mis-press."""
    if state.quiz_locked or not state.factoid_choices or index >= len(state.factoid_choices):
        return
    select_quiz_answer(index)
    grade_quiz_selection()


_grade_lock = threading.Lock()


def grade_quiz_selection(forced=False):
    """Physical Btn6 (GAME_MODE-only swap with Btn5, see process_events) /
    keyboard 5 / web remote "Grade" button: ends the current round. With
    players signed up (state.quiz_players, see web/remote_server.py's
    /api/player/join), grades every locked-in player's answer at once via
    _grade_multiplayer_round(); with nobody signed up, grades the operator's
    own single armed selection exactly as before -- multiplayer is purely
    additive, so the show works identically whether zero or fifty phones
    are connected.

    `forced=True` (drivers/live_round_engine.py's 30s timeout path): skips
    the "no selection yet" early-return below so a genuinely-unanswered
    solo round still grades as a loss (TIMES UP) instead of silently doing
    nothing.

    _grade_lock (2026-08-11 fix): this can be entered from two different
    threads -- the main pygame loop (drivers/live_round_engine.py's
    auto-grade check) and web/remote_server.py's POST /api/quiz/grade,
    which runs on uvicorn's own request thread. "if state.quiz_locked:
    return" followed later by "state.quiz_locked = True" (inside
    _grade_multiplayer_round()) is NOT atomic across threads -- if the
    operator pressed Grade on the web remote at nearly the same instant
    auto-grade fired, both could pass the check before either set the
    flag, running the grading logic (and its win/loss sound) twice in a
    row. This is the confirmed cause of a reported bigWin.wav "played over
    and over" -- the lock makes the check-and-set atomic."""
    with _grade_lock:
        if state.quiz_locked:
            return
        # "No selection yet" isn't a real grading attempt -- don't lock the
        # round for it, or the operator could never actually grade once
        # they do pick something. Checked inside the lock (not after) so
        # this early-return itself can't race with a genuine grade.
        if not state.quiz_players and state.quiz_selected_index < 0 and not forced:
            print("[ACTION] Btn6 pressed but no answer is selected yet.")
            state.set_message("SELECT AN ANSWER FIRST", 1.2)
            return
        state.quiz_locked = True
    _do_grade_quiz_selection()


def _do_grade_quiz_selection():
    if state.quiz_players:
        _grade_multiplayer_round()
        _maybe_advance_from_mystery_grade()
        return

    # Any 30s-timeout loss is silent (2026-09-18, generalized from the
    # narrower "nobody ever selected anything" case below): a timed-out
    # round's own visual (graphics/matrix_canvas.py's "TIMES UP!" + bare
    # correct-answer reveal, never a specific wrong-pick callout) never
    # blames a particular choice the way a real Grade-button loss does, so
    # buzzing the wrong-answer sound over it reads as a mismatch between
    # what's on screen and what's heard -- even if an answer HAD been
    # armed before time ran out. Originally added narrower, for Jukebox
    # mode (2026-08-13): a totally untouched app still auto-arms a mystery
    # question on every new song (drivers/mystery_band_engine.py), and with
    # nobody signed up as a player, that question can only ever grade
    # through this solo path -- so on a cold start where the operator never
    # once presses a button, this is the ONLY grading that happens, over
    # and over, one per song, and needs to stay silent so the app can just
    # sit there running as a plain jukebox without buzzing at nobody.
    silent_timeout = state.round_timed_out

    state.quiz_graded_at = time.time()
    letter = "ABCD"[state.quiz_selected_index]
    is_correct = (state.quiz_selected_index == state.factoid_correct_index)
    print(f"[ACTION] Btn6 GRADE -> Answer {letter} is {'CORRECT' if is_correct else 'WRONG'}")

    # Price Game Mode (Section 1): the instant the question is answered,
    # briskly fade the bed out and tween the channel faders back up rather
    # than waiting for the full scorecard hold + return-to-DJ-mode path.
    if state.price_game_audio_active:
        price_game_engine.end_price_game_audio_on_answer()

    state.quiz_score_total += 1
    if is_correct:
        state.quiz_score_correct += 1
        trigger_big_win()
    else:
        trigger_loss(play_sfx=not silent_timeout)

    _maybe_advance_from_mystery_grade()


def _maybe_advance_from_mystery_grade():
    """Once the initial "Who is this?" teaser question is graded,
    immediately continue into the extended question queue instead of
    leaving the room waiting for the operator to manually force Game Mode
    (2026-08-10). Calls mystery_band_engine.end_mystery_after_grade() --
    NOT enter_game_from_mystery() (that one's for Btn6-during-the-teaser,
    BEFORE grading, where re-applying the identify question as a fresh
    round is correct) -- since this fires AFTER grading already happened;
    re-applying the same question here was a confirmed bug (2026-09-17,
    see end_mystery_after_grade()'s docstring). This just does the
    post-grade advance automatically the instant grading happens via any
    path (auto-grade, timeout, or an explicit Grade press/panel button),
    not only a dedicated Btn6-during-teaser press.

    Skipped once a winner's been declared this game (2026-08-11) -- the
    game is over and headed into intermission, so there's nothing to
    advance into."""
    if state.game_winner_player_id or state.intermission_active:
        return
    if state.mode != state.MODE_GAME and state.mystery_active:
        mystery_band_engine.end_mystery_after_grade()
        state.mode = state.MODE_GAME
        print("[MYSTERY BAND] Auto-advancing to GAME_MODE after teaser grade -- "
              "continuing with extended questions, no operator action needed.")


def _grade_multiplayer_round():
    """Grades every signed-up player's locked-in answer against the current
    question in one shot. Players who never locked in are skipped entirely
    (didn't answer, not "answered wrong") -- not counted against them.

    The operator's own joystick/gamepad selection (state.quiz_selected_index)
    is ALSO graded here (2026-08-10 fix) -- previously it was ignored
    entirely once any players were signed up, which meant an operator who
    picked the correct answer on their own controller would still see a
    LOSS if no phone player happened to also lock in that answer. The
    operator and phone players are evaluated independently and combined:
    win/loss feedback (sound + DMX flash) fires if EITHER the operator's
    own pick OR at least one player's locked-in answer was correct."""
    state.quiz_locked = True
    state.quiz_graded_at = time.time()

    if state.price_game_audio_active:
        price_game_engine.end_price_game_audio_on_answer()

    results = []
    winning_player_id = None
    for player_id, player in state.quiz_players.items():
        if not player["locked"]:
            continue
        correct = player["selected_index"] == state.factoid_correct_index
        if correct:
            player["score"] += 1
        results.append({"player_id": player_id, "initials": player["initials"], "correct": correct})
    state.quiz_last_round_results = results
    state.round_winner_initials = [r["initials"] for r in results if r["correct"]]

    # First-correct-answer bonus (2026-08-11): only for the mystery/identify
    # "who is this?" question -- the FIRST player to lock in the right
    # answer gets an extra point on top of their normal one, and sees a
    # dedicated "FIRST ONE RIGHT!" message (web/remote_server.py's
    # player_question()). Ties (identical locked_at, essentially
    # impossible in practice) resolve to whichever dict-iteration order
    # hit first -- not worth guarding against for a live show.
    state.round_bonus_player_id = ""
    if state.factoid_category == "identify_band":
        correct_locked = [
            (pid, p) for pid, p in state.quiz_players.items()
            if p["locked"] and p["selected_index"] == state.factoid_correct_index
        ]
        if correct_locked:
            first_id, first_player = min(correct_locked, key=lambda item: item[1].get("locked_at", 0.0))
            first_player["score"] += 1
            state.round_bonus_player_id = first_id
            print(f"[MULTIPLAYER QUIZ] First-correct bonus -> {first_player['initials']} (+1 extra point).")

    # Only look for a NEW winner once per game (2026-08-11 fix): previously
    # this re-scanned every round regardless, and since a winner's score
    # never drops back below the threshold, every subsequent question they
    # answered re-triggered win_sequence_engine.start() (whose own
    # "already active" guard only blocks WHILE the duck/applause/restore
    # sequence is actively playing, not after) -- the confirmed cause of
    # "questions keep coming and so does the applause" after a win.
    if not state.game_winner_player_id and not state.intermission_active:
        for player_id, player in state.quiz_players.items():
            if player["score"] >= state.game_win_score and winning_player_id is None:
                winning_player_id = player_id
        if winning_player_id is not None:
            win_sequence_engine.start(winning_player_id)

    correct_count = sum(1 for r in results if r["correct"])
    not_locked = len(state.quiz_players) - len(results)
    operator_correct = (state.quiz_selected_index >= 0
                         and state.quiz_selected_index == state.factoid_correct_index)
    print(f"[MULTIPLAYER QUIZ] GRADE -> {correct_count}/{len(results)} locked-in players correct "
          f"({not_locked} didn't lock in an answer). Operator pick correct: {operator_correct}.")

    if correct_count > 0 or operator_correct:
        trigger_big_win()
    else:
        # Any 30s-timeout loss is silent (2026-09-18, generalized from the
        # identify_band-only, nobody-ever-answered case below -- same
        # change as _do_grade_quiz_selection()'s solo path): a timed-out
        # round's own visual (graphics/matrix_canvas.py's "TIMES UP!" +
        # bare correct-answer reveal) never blames a specific wrong pick
        # the way a real Grade-button loss does, so buzzing the
        # wrong-answer sound over it reads as a mismatch between what's on
        # screen and what's heard -- even if someone DID lock in a wrong
        # answer before time ran out. Originally added narrower (2026-08-12):
        # if this is the identify_band question, it timed out (not graded
        # because everyone answered), and literally nobody ever answered at
        # all, the visual teaser resolved and the matrix reverted to idle
        # content long before this ~40s deadline hit -- an unprompted
        # buzzer at that point has no on-screen context and just read as
        # random noise.
        silent_timeout = state.round_timed_out
        trigger_loss(play_sfx=not silent_timeout)

def abort_game_mode_early():
    """Btn7, at ANY point in GAME_MODE (question live, grading, or the
    scorecard display): immediately kills the round and returns to DJ_MODE.
    This is an abort, not a grade -- no score is recorded for an
    in-progress question and no win/loss sound plays. Clearing
    quiz_graded_at/quiz_locked stops the celebration/scorecard/auto-advance
    sequence in graphics/matrix_canvas.py from firing after the mode
    switch; DMX reverts to DJ-mode uplighting on the very next frame since
    drivers/lighting_engine.py renders purely off state.mode."""
    stop_previous_audio()
    state.mode = state.MODE_DJ
    state.quiz_locked = False
    state.quiz_selected_index = -1
    state.active_option = None
    state.quiz_graded_at = 0.0
    state.fixture1_mode = "off"  # Reset rule: leaving GAME_MODE -> Fixture 1 black
    state.game_active = False  # era-trivia scheduling: next question starts a fresh game
    # Same fix as graphics/matrix_canvas.py's return-to-DJ-mode path
    # (2026-08-11): quiz_locked resets to False above without this, so
    # drivers/live_round_engine.py's "active" check would see the aborted
    # question as newly live again next frame and could grade it anyway --
    # directly contradicting this function's own "no score is recorded"
    # guarantee.
    state.factoid_choices = []
    state.factoid_question = ""
    state.round_deadline_at = 0.0
    state.quiz_gate_status = "idle"
    price_game_engine.force_end_price_game_audio()
    state.set_message("MODE: DJ (GAME ABORTED)", 1.5)
    print("GAME MODE EXIT: Aborted early via Gamepad Button 7")

def trigger_big_win():
    stop_previous_audio()
    state.set_message("CORRECT ANSWER! BIG WIN!", 2.0)
    print("[ACTION] BIG WIN")
    if state.sfx_enabled:
        # Swapped for the doorbell (2026-09-18, operator request) -- rings
        # the physical solenoid USB_RELAY_BIG_WIN_RING_COUNT times rapidly
        # instead of playing bigwin.wav. Left commented (not deleted) in
        # case the sound effect is wanted back later.
        # play_processed_sound(raw_bigwin)
        relay_engine.ring(config.USB_RELAY_POINT_CHANNEL, config.USB_RELAY_BIG_WIN_RING_COUNT)
        dmx.pulse_channel(RELAY1_CHANNEL, 255, RELAY_PULSE_SECONDS)
        wled_engine.flash(255, 255, 255)

    # Fixture 1: pulsing green, rendered every frame by lighting_engine.py
    # from this state until the next reset (board clear / new question /
    # return to DJ mode).
    state.fixture1_mode = "win"
    state.fixture1_mode_set_at = time.time()

    # Amplified feedback: brief solid-green strobe pulse across ALL 10
    # uplight fixtures (2-11), rendered by
    # drivers/lighting_engine.py::_render_grade_flash -- Fixture 1 keeps its
    # own latched "win" pulse above regardless of this pulse ending.
    state.fixture_flash_mode = "win"
    state.fixture_flash_until = time.time() + DMX_GRADE_FLASH_SECONDS

# ------------------------------------------
# SECTION 1: QUIZ API GATE (Btn6, DJ mode only)
# ------------------------------------------
def _current_dj_track():
    track_info = get_rekordbox_track()
    if isinstance(track_info, tuple):
        return track_info[0], track_info[1]
    return str(track_info), ""

def handle_normal_trivia_button():
    """Btn2 in DJ mode (moved here from Btn6, 2026-08-12 -- see
    handle_quiz_gate_button() below): every accepted press plays an
    immediate confirmation chime BEFORE anything else happens -- the sound
    never waits on a lookup, and playback is wrapped so a mixer hiccup can
    never block the state transition below it.

    Track questions are pre-fetched continuously in the background as soon
    as a deck's track is confidently identified (see
    drivers/factoid_engine.py::ensure_prefetch, called every frame from
    process_events() below) -- so this instantly pops the next cached
    question off state.track_question_queue and enters GAME_MODE, with NO
    network call at press time. Only a cold-start track (queue still empty --
    brand new track, still filling, or offline) falls through to the
    QUIZ_GATE_EMPTY_CACHE_TIMEOUT_SECONDS wait-then-fallback path below,
    polled every frame by _process_quiz_gate().

    No longer has a price_game_pending diversion (that lived here when this
    was Btn6) -- Price Game has its own dedicated, always-works button now,
    so there's nothing left here to divert to it.

    Refuses to start a game during the post-win intermission -- scores were
    just reset and the room is meant to get a real break. Also refuses
    outside state.show_phase == "live" (2026-08-14) -- setup/countdown/
    intro/outro have no confidently-identified track playing (the deck is
    silent until the intro hands off), so a question fired here would have
    nothing real to be about."""
    if state.mode != state.MODE_DJ:
        return
    if state.show_phase != "live":
        state.set_message("START THE SHOW FIRST", 1.2)
        print(f"[BUTTON] Btn2 pressed outside a live show (show_phase={state.show_phase!r}) -- ignored.")
        return
    if state.intermission_active:
        state.set_message("INTERMISSION -- NEW GAME AFTER THE BREAK", 1.5)
        print("[BUTTON] Btn2 pressed during intermission -- ignored.")
        return

    _maybe_reset_after_win()

    print(f"[BUTTON] Btn2 pressed. quiz_gate_status={state.quiz_gate_status!r}")

    try:
        if state.sfx_enabled:
            play_processed_sound(raw_coin, volume=1.0)
    except Exception as e:
        print(f"[AUDIO ERROR] Btn2 coin chime failed to play: {e}")

    if state.quiz_gate_status == "fetching":
        print("[ACTION] Btn2 pressed while already waiting on the pre-fetch queue to fill.")
        return

    confident = state.deck1_confident if state.active_deck == 1 else state.deck2_confident
    if not confident or not state.factoid_track_key:
        state.set_message("NO CONFIDENT TRACK ID YET", 1.2)
        print("[ACTION] Btn2 pressed but no confident track ID -- nothing buffered yet.")
        return

    key = state.factoid_track_key

    if mystery_band_engine.is_teaser_live():
        if mystery_band_engine.enter_game_from_mystery():
            state.quiz_gate_status = "idle"
            state.mode = state.MODE_GAME
            state.set_message("MYSTERY BAND! IDENTIFY THE ARTIST", 1.5)
            print("[BUTTON] Btn2 -> Mystery Band identify-question forced -> GAME_MODE")
            return

    if pull_next_from_queue(key):
        state.quiz_gate_status = "idle"
        state.mode = state.MODE_GAME
        state.set_message("QUIZ MODE", 1.0)
        print("[BUTTON] Btn2 -> instant pull from pre-fetch queue -> GAME_MODE")
        return

    if is_exhausted(key):
        # One-shot AI query policy: the AI already said AI_NOT_CONFIDENT for
        # this track -- skip the QUIZ_GATE_EMPTY_CACHE_TIMEOUT_SECONDS wait
        # entirely and go straight to the offline fallback question.
        title, artist = _current_dj_track()
        state.quiz_gate_status = "idle"
        # 2026-09-18: load_exhausted_fallback_question() no longer
        # fabricates fake content when even fallback_questions.json is
        # unavailable -- it returns None instead, and we must NOT enter
        # Game Mode in that case (see factoid_engine.py::
        # load_forced_fallback_question()'s docstring for why). Same
        # "notify, stay put" shape as _process_quiz_gate()'s own timeout
        # tripwire below.
        if load_exhausted_fallback_question(title, artist) is None:
            print(f"[BUTTON ERROR] Btn2 -> '{key}' AI_EXHAUSTED, but the offline fallback bank is also "
                  f"unavailable -- staying in DJ_MODE rather than showing a placeholder question.")
            state.set_message("NO QUESTION READY -- TRY AGAIN", 1.5)
            return
        state.mode = state.MODE_GAME
        state.set_message("QUIZ MODE (OFFLINE)", 1.2)
        print(f"[BUTTON] Btn2 -> '{key}' AI_EXHAUSTED -> offline fallback -> GAME_MODE")
        return

    print("[ACTION] Btn2 -> pre-fetch queue empty (cold start), waiting on background fetch.")
    state.quiz_gate_status = "fetching"
    state.quiz_gate_key = key
    state.quiz_gate_started_at = time.time()


def handle_quiz_gate_button():
    """Btn6 in DJ mode (2026-08-12 redesign): ALWAYS forces exactly one
    Price Game question, drawn synchronously from the local CSV bank
    (drivers/price_bank_engine.py, config.PRICE_GAME_BANK_PATH) -- no AI
    call, no track-ID confidence requirement, no decade dependency. A live
    "...only if the price is right! <press Btn6>" MC cue can no longer fail
    into a Mystery Band question, a generic offline fallback, or a
    'no question ready' buzz -- the local bank always has an answer.

    Normal (non-Price-Game) trivia -- Btn6's old job -- now lives on Btn2
    (handle_normal_trivia_button() above), which had no DJ-mode binding
    before this change.

    Refuses to start during the post-win intermission (scores were just
    reset, the room is meant to get a real break) -- Btn5+Btn6 hold
    (force_price_game() below) is still the deliberate early-end override.
    Also refuses outside state.show_phase == "live" (2026-08-14) -- during
    setup/countdown/intro/outro the deck is silent and LEDs show the
    startup screen, so a Price Game firing here has no show context to sit
    inside (confirmed live: music started while the panels still read
    "START UP")."""
    if state.mode != state.MODE_DJ:
        return
    if state.show_phase != "live":
        state.set_message("START THE SHOW FIRST", 1.2)
        print(f"[BUTTON] Btn6 pressed outside a live show (show_phase={state.show_phase!r}) -- ignored.")
        return
    if state.intermission_active:
        state.set_message("INTERMISSION -- HOLD BTN5+BTN6 TO FORCE PRICE GAME", 1.5)
        print("[BUTTON] Btn6 pressed during intermission -- ignored "
              "(hold Btn5+Btn6 together to force Price Game early).")
        return

    _maybe_reset_after_win()

    print("[BUTTON] Btn6 pressed -> forcing Price Game from the local question bank.")

    try:
        if state.sfx_enabled:
            play_processed_sound(raw_coin, volume=1.0)
    except Exception as e:
        print(f"[AUDIO ERROR] Btn6 coin chime failed to play: {e}")

    if state.price_game_active:
        print("[ACTION] Btn6 pressed while a Price Game intro is already playing out -- ignored.")
        return

    price_game_engine.start_price_game_from_bank()
    state.set_message("PRICE GAME!", 1.5)


def force_price_game():
    """Btn5+Btn6 hold combo (see _process_force_price_game_combo() below):
    the AI-fetch, decade-themed Price Game path -- distinct from a plain
    Btn6 tap, which now always forces a local-bank question instead (see
    handle_quiz_gate_button()) and never needs this combo to be reliable.
    This combo remains for operators who specifically want a round themed
    to the *current track's own era* rather than a random year. A no-op
    (status message only) if no Price Game is currently armed for this
    track -- there's no era/decade to build a pricing question around
    otherwise (drivers/factoid_engine.py's _maybe_arm_price_game() is what
    arms state.price_game_pending/price_game_decade once the AI tags a
    release year for the track).

    Also the deliberate override for the post-win intermission (mirrors
    handle_quiz_gate_button()'s own intermission guard/message): ends
    intermission early, but only once a Price Game is actually confirmed
    armed -- holding the combo with nothing armed leaves intermission
    running rather than ending it for nothing."""
    if state.mode != state.MODE_DJ:
        return
    if not (state.price_game_pending and state.price_game_decade):
        state.set_message("NO PRICE GAME ARMED YET", 1.2)
        print("[BUTTON] Btn5+Btn6 hold -- Force Price Game pressed but none is armed for this track.")
        return

    if state.intermission_active:
        state.intermission_active = False
        state.intermission_ends_at = 0.0
        state.game_round_number += 1  # matrix idle GOAL PAGE's "ROUND N"
        print("[BUTTON] Btn5+Btn6 hold during intermission -- ending it early to force Price Game.")

    _maybe_reset_after_win()
    key = state.factoid_track_key
    decade = state.price_game_decade
    print(f"[BUTTON] Btn5+Btn6 hold -> FORCE {decade.upper()} PRICE GAME for '{key}'")
    price_game_engine.start_price_game(key, decade)
    state.set_message(f"{decade.upper()} PRICE GAME!", 1.5)

def _process_quiz_gate():
    """Per-frame poll: only relevant right after a cold-start Btn2 press
    (queue was empty). Reacts the instant the background pre-fetch lands a
    question for this track, entering GAME_MODE. If
    QUIZ_GATE_EMPTY_CACHE_TIMEOUT_SECONDS elapses with still nothing cached,
    buzzShort.wav plays as a pure audio/visual error notification (panel 3
    coin-pop) -- it does NOT alter application state. The DJ stays in
    DJ_MODE and can retry Btn2 once the cache fills."""
    if state.quiz_gate_status != "fetching":
        return

    if state.factoid_track_key == state.quiz_gate_key and pull_next_from_queue(state.quiz_gate_key):
        state.quiz_gate_status = "idle"
        state.mode = state.MODE_GAME
        state.set_message("QUIZ MODE", 1.0)
        print("[BUTTON] Btn2 background fetch resolved -> auto-entering GAME_MODE")
        return

    if is_exhausted(state.quiz_gate_key):
        # The track went AI_EXHAUSTED while we were waiting -- don't sit out
        # the full timeout window, fall back offline right away.
        title, artist = _current_dj_track()
        state.quiz_gate_status = "idle"
        # See the matching comment on the other load_exhausted_fallback_
        # question() call site above -- None means even the offline bank
        # is unavailable, don't enter Game Mode with nothing to show.
        if load_exhausted_fallback_question(title, artist) is None:
            print(f"[BUTTON ERROR] Btn2 wait -> '{state.quiz_gate_key}' went AI_EXHAUSTED, but the offline "
                  f"fallback bank is also unavailable -- staying in DJ_MODE.")
            state.set_message("NO QUESTION READY -- TRY AGAIN", 1.5)
            return
        state.mode = state.MODE_GAME
        state.set_message("QUIZ MODE (OFFLINE)", 1.2)
        print(f"[BUTTON] Btn2 wait -> '{state.quiz_gate_key}' went AI_EXHAUSTED -> offline fallback -> GAME_MODE")
        return

    timed_out = (time.time() - state.quiz_gate_started_at) >= QUIZ_GATE_EMPTY_CACHE_TIMEOUT_SECONDS
    if not timed_out:
        return  # still legitimately waiting, still inside the tripwire window

    state.quiz_gate_status = "idle"
    print(f"[BUTTON ERROR] Btn2 pre-fetch queue still empty after "
          f"{QUIZ_GATE_EMPTY_CACHE_TIMEOUT_SECONDS}s TIMEOUT TRIPWIRE. "
          f"Notifying only -- staying in DJ_MODE (retry Btn2 once the cache fills).")
    state.coin_pop_flash_until = time.time() + 2.0
    try:
        if state.sfx_enabled:
            play_processed_sound(raw_buzz_short, volume=1.0)
    except Exception as e:
        print(f"[AUDIO ERROR] Btn2 fallback buzz failed to play: {e}")

    state.set_message("NO QUESTION READY -- TRY AGAIN", 1.5)

# ------------------------------------------
# WEB REMOTE: MANUAL GAME-MODE TRIGGER + CATEGORY SELECTOR
# ------------------------------------------
def _maybe_reset_after_win():
    """Called at every Game Mode entry point: if the last game ended in a
    completed win sequence, clear every player's score and show a brief
    "NEW GAME STARTING!" toast before the new round's own message
    overwrites it (Beta-Fix Feature Set item 10). No-op once already
    cleared (game_winner_player_id stays "" the rest of the time) and
    while the win sequence itself is still actively playing out (duck/
    applause/restore) -- only reset once it's fully finished."""
    if not state.game_winner_player_id or state.win_sequence_active:
        return
    for player in state.quiz_players.values():
        player["score"] = 0
    state.game_winner_player_id = ""
    state.game_active = False  # era-trivia scheduling: next question starts a fresh game
    state.game_round_number += 1  # matrix idle GOAL PAGE's "ROUND N"
    state.set_message("NEW GAME STARTING!", 1.5)
    print("[GAME] New game entered after a completed win -- all scores reset.")


def force_game_mode(category_key):
    """Web remote 'Force Game Mode' control (web/remote_server.py's
    /api/game/force): manually enters GAME_MODE with a host-selected
    category preference (config.WEB_GAME_CATEGORIES), reusing the same
    engines Btn6 already drives rather than duplicating question-selection
    logic. Returns (ok: bool, message: str) for the API response.

    - "band_name": forces the Mystery Band "identify this band" question
      (live teaser if one's armed, else built fresh from the current
      track's artist -- no AI call either way, same as the offline
      exhausted-track fallback path).
    - "price_game": only works if a Price Game is actually armed for the
      current track (release-year gated, see config.PRICE_GAME_MIN_YEAR/
      MAX_YEAR) -- can't manufacture pricing trivia for a track with no
      AI-tagged release year at all.
    - "geography" / "true_false": prefers a queued question already tagged
      with that category; falls back to whatever's next if none match."""
    if state.mode != state.MODE_DJ:
        return False, "Already in Game Mode."

    _maybe_reset_after_win()

    if category_key == "band_name":
        question = state.mystery_identify_question
        if question is not None:
            mystery_band_engine.enter_game_from_mystery()
        else:
            title, artist = _current_dj_track()
            question = mystery_band_engine.build_identify_fallback_question(artist)
            if question is None:
                return False, "No artist known for the current track yet."
            apply_mystery_identify_question(question)
        state.mode = state.MODE_GAME
        state.set_message("MYSTERY BAND! IDENTIFY THE ARTIST", 1.5)
        print("[WEB REMOTE] Force Game Mode -> Band Name")
        return True, "Band Name question forced."

    if category_key == "price_game":
        if state.price_game_pending and state.price_game_decade and state.factoid_track_key:
            decade = state.price_game_decade
            price_game_engine.start_price_game(state.factoid_track_key, decade)
            state.set_message(f"{decade.upper()} PRICE GAME!", 1.5)
            print("[WEB REMOTE] Force Game Mode -> Price Game")
            return True, "Price Game started."
        return False, "No Price Game armed for the current track."

    if category_key in ("geography", "true_false"):
        key = state.factoid_track_key
        if not key:
            return False, "No confident track ID yet."
        if pull_by_category(key, category_key):
            state.mode = state.MODE_GAME
            state.set_message("QUIZ MODE", 1.0)
            print(f"[WEB REMOTE] Force Game Mode -> {category_key}")
            return True, "Question forced."
        return False, "No question ready yet -- try again shortly."

    return False, f"Unknown category: {category_key!r}"

# ------------------------------------------
# SECTION 1B: AUTO-ANNOUNCEMENT TOGGLE (Btn1 / pygame index 0, DJ mode only)
# ------------------------------------------
# NOTE: Btn1 previously triggered an "Emergency Force Override" (skip the
# AI fetch, force-enter GAME_MODE with a fallback_questions.json question).
# That binding has been fully replaced by the Auto-Announcement toggle
# below -- the emergency override is no longer reachable from the gamepad.
def handle_auto_announce_toggle():
    """Btn1 (pygame index 0) in DJ mode: toggles the Auto-DJ station
    announcement voice-over feature on/off (drivers/auto_dj_engine.py),
    with a 1.5s "v ON"/"v OFF" confirmation overlay on panel 3. Scoped to
    DJ mode only so it never collides with the GAME_MODE Btn1 binding
    (select Answer 4). Called from _handle_btn1_release() on a genuine
    quick tap only -- holding Btn1 shows the status overlay instead (see
    _process_btn1_hold())."""
    auto_dj_engine.toggle_auto_announce()

# ------------------------------------------
# SECTION 3: DJ-MODE LIGHTING CONTROLS (Btns 5/7/8)
# ------------------------------------------
def handle_tempo_tap():
    """Btn5 in DJ mode: tap-tempo for the DMX uplighting themes, plus a
    brief red flash-outline on panels 3-6 (rendered in matrix_canvas.py)."""
    now = time.time()
    state.tempo_tap_times.append(now)
    state.tempo_tap_times = state.tempo_tap_times[-TEMPO_TAP_WINDOW:]
    if len(state.tempo_tap_times) >= 2:
        deltas = [
            state.tempo_tap_times[i + 1] - state.tempo_tap_times[i]
            for i in range(len(state.tempo_tap_times) - 1)
        ]
        avg = sum(deltas) / len(deltas)
        state.dj_tempo_period = max(TEMPO_PERIOD_MIN_SECONDS, min(TEMPO_PERIOD_MAX_SECONDS, avg))
    state.tempo_flash_at = now
    # Even the first tap of a sequence (before there are two to average)
    # counts as the operator taking over: it locks out a late-arriving
    # online BPM from overwriting what they're in the middle of dialing in.
    state.tempo_operator_set = True
    light_prefs_engine.mark_dirty()
    print(f"[ACTION] Btn5 TEMPO TAP -> period {state.dj_tempo_period:.2f}s")

def handle_feature_select():
    """Btn9 in DJ mode (2026-09-17): steps state.dj_selected_feature through
    config.DJ_FEATURE_ORDER, picking which fixture type Btn7 (color) and
    Btn8 (theme) below currently apply to -- these three used to share one
    look unconditionally; this is what makes them independently
    addressable. Confirms the new selection with a brief red flash on that
    fixture's own output: DMX uses its own self-expiring state pair
    (rendered in drivers/lighting_engine.py::_render_dj_uplights, same
    shape as the GAME-mode grade flash but DJ-mode-scoped), marquee reuses
    its existing wled_engine.flash(), outline uses the new accent_engine.
    flash() added alongside it. Not itself a saved preference -- doesn't
    call light_prefs_engine.mark_dirty()."""
    order = config.DJ_FEATURE_ORDER
    idx = order.index(state.dj_selected_feature) if state.dj_selected_feature in order else 0
    state.dj_selected_feature = order[(idx + 1) % len(order)]
    if state.dj_selected_feature == "dmx":
        state.dj_feature_flash_color = (255, 0, 0)
        state.dj_feature_flash_until = time.time() + config.DJ_FEATURE_FLASH_SECONDS
    elif state.dj_selected_feature == "marquee":
        wled_engine.flash(255, 0, 0)
    else:
        accent_engine.flash(255, 0, 0)
    print(f"[ACTION] Btn9 FEATURE SELECT -> {state.dj_selected_feature}")

def handle_color_cycle():
    """Btn7 in DJ mode: cycles the uplighting color for whichever fixture
    type is currently selected (state.dj_selected_feature, Btn9 above) --
    DMX, marquee, and outline each keep their own independent color index
    since 2026-09-17, no longer one shared value."""
    feature = state.dj_selected_feature
    if feature == "dmx":
        state.dmx_color_index = (state.dmx_color_index + 1) % len(DJ_COLOR_PALETTE)
        result = state.dmx_color_index
    elif feature == "marquee":
        state.marquee_color_index = (state.marquee_color_index + 1) % len(DJ_COLOR_PALETTE)
        result = state.marquee_color_index
    else:
        state.accent_color_index = (state.accent_color_index + 1) % len(DJ_COLOR_PALETTE)
        result = state.accent_color_index
    light_prefs_engine.mark_dirty()
    print(f"[ACTION] Btn7 COLOR ({feature}) -> index {result}")

def toggle_last_announcement_swear():
    """Btn3 press in DJ mode: flips the swear tag on whichever announcement
    clip most recently played (state.last_announcement_filename -- the same
    "what just played" pointer the caption editor reads/writes). No-ops
    (console log only) if nothing's played yet this session. Panel 4 shows
    the result for as long as Btn3 stays down -- see the JOYBUTTONUP
    callsite in process_events() and matrix_canvas.py's pid==4 branch."""
    filename = state.last_announcement_filename
    if not filename:
        print("[ACTION] Btn3 SWEAR TOGGLE -- no announcement has played yet this session, no-op.")
        return
    is_swear = announcement_engine.toggle_swear(filename)
    state.btn3_swear_toggle_active = True
    state.btn3_swear_toggle_text = "*#@!" if is_swear else "a-ok"
    print(f"[ACTION] Btn3 SWEAR TOGGLE -- {filename!r} swear={is_swear}")

def handle_theme_cycle():
    """Btn8 in DJ mode: cycles the animation pattern for whichever fixture
    type is currently selected (state.dj_selected_feature, Btn9 above),
    each against its own count/modulus (DMX: DJ_THEME_COUNT; marquee:
    config.MARQUEE_THEME_NAMES; outline: config.ACCENT_EFFECT_NAMES, WLED's
    full default effect catalog since 2026-09-18) rather than one shared
    range. DMX deliberately excludes DJ_THEME_ALL_OFF_INDEX
    -- landing on a dark stop while cycling through patterns live reads as
    an error, not a lighting choice. ALL LIGHTS OFF is still reachable as a
    deliberate direct selection from the admin panel's DMX pattern
    dropdown, just never something you can cycle into by accident.
    Marquee/outline have no equivalent "off" entry to exclude."""
    feature = state.dj_selected_feature
    if feature == "dmx":
        state.dmx_theme_index = (state.dmx_theme_index + 1) % DJ_THEME_COUNT
        result = state.dmx_theme_index
    elif feature == "marquee":
        state.marquee_theme_index = (state.marquee_theme_index + 1) % len(config.MARQUEE_THEME_NAMES)
        result = state.marquee_theme_index
    else:
        state.accent_theme_index = (state.accent_theme_index + 1) % len(config.ACCENT_EFFECT_NAMES)
        result = state.accent_theme_index
    light_prefs_engine.mark_dirty()
    print(f"[ACTION] Btn8 THEME ({feature}) -> theme {result}")

# ------------------------------------------------------------
# SECTION 4: AUTO-DJ TOGGLE
# ------------------------------------------------------------
# No physical-button trigger (2026-08-12 -- removed from Btn4, see the
# JOYBUTTONDOWN dispatch above). auto_dj_engine.toggle_auto_dj() is still
# reachable from the web remote's Auto-DJ control (web/remote_server.py).

# ------------------------------------------------------------
# SECTION 4B: JOYSTICK REMAPPING (2026-08-20, "Joy Assign" web page,
# drivers/joystick_bindings.py) -- every simple "one press, one call"
# action's handler, keyed by the stable action id joystick_bindings.py's
# registry uses instead of a raw button number. dj_auto_announce (tap/hold
# split) and si_exit_1/si_exit_2 (need the "everything else fires"
# membership check) still get bespoke handling in process_events() below
# -- they're in here too, since a combo firing them just means "do the
# tap behavior"/"exit", no tap/hold or membership logic needed for that.
# ------------------------------------------------------------
ACTION_HANDLERS = {
    "dj_auto_announce": handle_auto_announce_toggle,
    "dj_trivia_pull": handle_normal_trivia_button,
    "dj_swear_toggle": toggle_last_announcement_swear,
    "dj_tempo_tap": handle_tempo_tap,
    "dj_force_price_game": handle_quiz_gate_button,
    "dj_color_cycle": handle_color_cycle,
    "dj_theme_cycle": handle_theme_cycle,
    "dj_feature_select": handle_feature_select,
    "game_select_1": lambda: select_quiz_answer(0),
    "game_select_2": lambda: select_quiz_answer(1),
    "game_select_3": lambda: select_quiz_answer(2),
    "game_select_4": lambda: select_quiz_answer(3),
    "game_clear": clear_quiz_selection,
    "game_grade": grade_quiz_selection,
    "game_exit_1": abort_game_mode_early,
    "game_exit_2": abort_game_mode_early,
    "si_exit_1": lambda: space_invaders_engine.exit_space_invaders(),
    "si_exit_2": lambda: space_invaders_engine.exit_space_invaders(),
    "simon_select_1": lambda: simon_engine.press(0),
    "simon_select_2": lambda: simon_engine.press(1),
    "simon_select_3": lambda: simon_engine.press(2),
    "simon_select_4": lambda: simon_engine.press(3),
    "simon_exit_1": lambda: simon_engine.exit_simon(),
    "simon_exit_2": lambda: simon_engine.exit_simon(),
}

# Per-action debounce window for the DIRECT per-button dispatch path only
# (a combo has its own hold_seconds + one-shot latch, no separate debounce
# needed on top). Missing from this dict = no debounce, matching exactly
# which buttons had no _debounced() wrapper in the original hardcoded
# chain (e.g. the Game Mode answer-select buttons, Btn3's swear toggle).
_ACTION_DEBOUNCE = {
    "dj_trivia_pull": QUIZ_GATE_DEBOUNCE_SECONDS,
    "dj_force_price_game": QUIZ_GATE_DEBOUNCE_SECONDS,
    "dj_tempo_tap": BUTTON_DEBOUNCE_SECONDS,
    "dj_color_cycle": BUTTON_DEBOUNCE_SECONDS,
    "dj_theme_cycle": BUTTON_DEBOUNCE_SECONDS,
    "dj_feature_select": BUTTON_DEBOUNCE_SECONDS,
    "game_clear": BUTTON_DEBOUNCE_SECONDS,
    "game_grade": BUTTON_DEBOUNCE_SECONDS,
    "game_exit_1": BUTTON_DEBOUNCE_SECONDS,
    "game_exit_2": BUTTON_DEBOUNCE_SECONDS,
}

_MODE_NAMES = {
    state.MODE_DJ: "DJ",
    state.MODE_GAME: "GAME",
    state.MODE_SPACE_INVADERS: "SPACE_INVADERS",
    state.MODE_SIMON: "SIMON",
}


def _mode_name():
    return _MODE_NAMES.get(state.mode, "DJ")


def _dispatch_action(action_id):
    """Direct per-button press path: looks up action_id's handler and
    applies its debounce window (if any), exactly replacing what used to
    be an individual `if _debounced(btn): handle_x()` line per button."""
    handler = ACTION_HANDLERS.get(action_id)
    if handler is None:
        return
    debounce_s = _ACTION_DEBOUNCE.get(action_id)
    if debounce_s is None:
        handler()
    elif _debounced(action_id, debounce_s):
        handler()


def _button_is_forming_a_combo(btn):
    """True if `btn` is one button of a multi-button combo (applicable to
    the current mode) whose OTHER buttons are ALSO currently held --
    generalizes the original bespoke "suppress this button's own action
    while the other combo button is already down" checks for Space
    Invaders entry and Force Price Game to any number of user-defined
    combos. _process_combos() (polled every frame) owns the actual
    hold-to-fire logic; this only stops the individual press from ALSO
    firing its own single-button action on top while the combo forms."""
    mode = _mode_name()
    for combo in joystick_bindings.combos():
        buttons = combo["buttons"]
        if btn not in buttons:
            continue
        modes = combo.get("modes", ["any"])
        if "any" not in modes and mode not in modes:
            continue
        others = [b for b in buttons if b != btn]
        if others and all(_joystick_button_held(b) for b in others):
            return True
    return False

# ------------------------------------------
# BUTTON DEBOUNCE (Btns 5-8, Section 5.3)
# ------------------------------------------
def _debounced(btn_index, min_interval=BUTTON_DEBOUNCE_SECONDS):
    now = time.time()
    last = state.last_button_press_time.get(btn_index, 0.0)
    if now - last < min_interval:
        return False
    state.last_button_press_time[btn_index] = now
    return True

# ------------------------------------------
# VOLUME HOLD-TO-REPEAT (TV remote style)
# ------------------------------------------
def _held_volume_direction():
    """Polls current input state (not events) so a held control keeps
    reporting a direction every frame. Returns +1 / -1 / 0. The joystick
    axis sign convention is reversed per Section 2.1; the D-pad/hat is a
    separate physical control and is untouched."""
    if state.mode not in (state.MODE_DJ, state.MODE_GAME):
        return 0

    keys = pygame.key.get_pressed()
    if keys[pygame.K_UP]:
        return 1
    if keys[pygame.K_DOWN]:
        return -1

    for js in joysticks:
        try:
            hx, hy = js.get_hat(0)
        except Exception:
            hx, hy = 0, 0
        if hy == 1 or hx == 1:
            return 1
        if hy == -1 or hx == -1:
            return -1

        try:
            naxes = js.get_numaxes()
        except Exception:
            naxes = 0
        # Only the Y axis (1, 7) drives volume now -- X (0, 6) is
        # reassigned to Next Track / answer-3 select (Section 2.2).
        for axis, positive_dir in ((1, 1), (7, 1)):
            if axis >= naxes:
                continue
            try:
                val = js.get_axis(axis)
            except Exception:
                continue
            if val > 0.6:
                return positive_dir
            if val < -0.6:
                return -positive_dir

    return 0

def _process_volume_hold():
    """Called once per frame. The first press of a direction is handled
    by the discrete event handlers below (immediate tactile response);
    this only takes over once the control has been held past the initial
    delay, then keeps stepping every REPEAT_INTERVAL seconds."""
    global _vol_hold_dir, _vol_next_repeat
    direction = _held_volume_direction()
    now = time.time()

    if direction == 0:
        _vol_hold_dir = 0
        return

    if direction != _vol_hold_dir:
        _vol_hold_dir = direction
        _vol_next_repeat = now + VOLUME_HOLD_INITIAL_DELAY_SECONDS
        return

    if now >= _vol_next_repeat:
        handle_dj_volume(5 * direction)
        _vol_next_repeat = now + VOLUME_HOLD_REPEAT_INTERVAL_SECONDS

# ------------------------------------------
# SPACE INVADERS: DUAL-BUTTON COMBO DETECTION + HELD-MOVEMENT POLLING
# ------------------------------------------
def _joystick_button_held(button_index):
    """Real-time hardware read (not the event queue) of whether
    `button_index` is currently pressed on any connected joystick -- used to
    detect the Btn1+Btn3 combo regardless of which button's JOYBUTTONDOWN
    event happens to arrive first."""
    for js in joysticks:
        try:
            if js.get_numbuttons() > button_index and js.get_button(button_index):
                return True
        except Exception:
            continue
    return False


# ------------------------------------------------------------
# COMBOS (2026-08-20): generalized hold-to-fire, replacing the three
# separate bespoke functions this used to be (Space Invaders entry, Force
# Price Game, Shutdown) -- see joystick_bindings.py's module docstring for
# why SI entry (originally edge-triggered) now goes through the same
# polled-hold model as the other two.
# ------------------------------------------------------------
_combo_since = {}   # combo_id -> time.time() first became fully held
_combo_fired = set()  # combo_id -> already fired this hold (latched until release)


def _combo_target_handler(fires):
    """Resolves a combo's "fires" target to a callable -- either one of
    the three combo-only special targets (no plain single-button action
    behind them) or, for any ordinary remappable action, its normal
    ACTION_HANDLERS entry -- so a combo can target literally any
    single-button action too, not just the three built-ins."""
    if fires == "__space_invaders_entry__":
        return lambda: space_invaders_engine.enter_space_invaders()
    if fires == "__simon_entry__":
        return lambda: simon_engine.enter_simon()
    if fires == "__force_price_game__":
        return force_price_game
    if fires == "__shutdown__":
        def _do_shutdown():
            print("SYSTEM SHUTDOWN: Triggered via configured joystick combo")
            state.shutdown_reason = "JOYSTICK COMBO"
            state.shutdown_requested = True
        return _do_shutdown
    return ACTION_HANDLERS.get(fires)


def _process_combos():
    """Per-frame, regardless of mode (each combo's own "modes" list gates
    whether it's live right now -- see joystick_bindings.py). Hardware-
    polled (_joystick_button_held()), not event-driven, since a "held
    together" combo can't be detected reliably off JOYBUTTONDOWN events
    alone (whichever button's event fires first can't see the others'
    state yet mid-event-loop). Each combo latches via _combo_fired so it
    fires exactly once per hold, not every frame past its threshold --
    same reasoning the original Force Price Game combo's own latch had
    (avoids spamming a "nothing armed" status message right after a
    successful trigger clears the thing that made it succeed)."""
    if state.shutdown_requested:
        return  # already triggered (this or the web remote) -- no more combo work needed
    mode = _mode_name()
    now = time.time()
    for combo in joystick_bindings.combos():
        combo_id = combo["id"]
        buttons = combo["buttons"]
        modes = combo.get("modes", ["any"])
        applies = "any" in modes or mode in modes
        all_held = applies and bool(buttons) and all(_joystick_button_held(b) for b in buttons)
        if not all_held:
            _combo_since.pop(combo_id, None)
            _combo_fired.discard(combo_id)
            continue
        since = _combo_since.setdefault(combo_id, now)
        if combo_id in _combo_fired:
            continue
        if now - since >= combo["hold_seconds"]:
            _combo_fired.add(combo_id)
            handler = _combo_target_handler(combo["fires"])
            if handler:
                handler()

# ------------------------------------------
# BTN1/BTN3 TAP-VS-HOLD: PER-FRAME HOLD-PROMOTION + RELEASE HANDLERS
# ------------------------------------------
def _process_btn1_hold():
    """Called once per frame. Promotes a still-held Btn1 press to a "hold"
    (shows the status overlay) once it's been down past
    BTN1_HOLD_THRESHOLD_SECONDS. A no-op once already promoted this press,
    or if Btn1 isn't currently armed (no JOYBUTTONDOWN this press, or the
    Space Invaders combo consumed it -- see process_events())."""
    global _btn1_hold_fired
    if state.mode != state.MODE_DJ or _btn1_down_at is None or _btn1_hold_fired:
        return
    btn1 = joystick_bindings.button_for("dj_auto_announce")
    if btn1 is not None and _joystick_button_held(btn1) and time.time() - _btn1_down_at >= BTN1_HOLD_THRESHOLD_SECONDS:
        _btn1_hold_fired = True
        state.btn1_hold_overlay_active = True

def _process_cpu_temp_overlay():
    """Called once per frame, regardless of mode/show_phase -- a hardware
    diagnostic shouldn't be gated behind DJ mode any more than the
    shutdown combo is. Sets state.cpu_temp_overlay_active for as long as
    every button in the configured trigger (Joy Assign page, 1 or more
    buttons) is held simultaneously; graphics/matrix_canvas.py reads that
    flag to draw panel 5's overlay. No persistence window like Btn1's --
    this hides the instant any trigger button releases, matching "while
    engaged" rather than a confirmation toast."""
    trigger = joystick_bindings.cpu_temp_trigger()
    state.cpu_temp_overlay_active = bool(trigger) and all(_joystick_button_held(b) for b in trigger)


def _handle_btn1_release():
    """JOYBUTTONUP for Btn1 (DJ mode only -- see process_events()). Decides
    tap vs. hold using _btn1_hold_fired (set by _process_btn1_hold() above):
      - Genuine hold released: don't toggle Auto-Announcement -- instead
        keep the status overlay showing for BTN1_HOLD_OVERLAY_PERSIST_SECONDS
        more.
      - Quick tap while that persistence window is still live: dismiss the
        overlay early, still no toggle.
      - Quick tap otherwise (today's behavior, unchanged): toggle
        Auto-Announcement.
    The `_btn1_down_at is None` guard makes this a safe no-op for the
    JOYBUTTONUP that follows a press the Space Invaders combo consumed
    (SI_ENTRY_BUTTONS) -- that path never sets _btn1_down_at."""
    global _btn1_down_at, _btn1_hold_fired
    if _btn1_down_at is None:
        return
    was_hold = _btn1_hold_fired
    _btn1_down_at = None
    _btn1_hold_fired = False

    if was_hold:
        state.btn1_hold_overlay_active = False
        state.btn1_hold_overlay_until = time.time() + BTN1_HOLD_OVERLAY_PERSIST_SECONDS
        return

    if time.time() < state.btn1_hold_overlay_until:
        state.btn1_hold_overlay_until = 0.0  # dismiss early, no toggle
        return

    if _debounced("dj_auto_announce"):
        handle_auto_announce_toggle()

# Shutdown is now just another entry in joystick_bindings.py's combo list
# (combo_shutdown, modes=["any"]) -- _process_combos() above owns firing
# it, replacing what used to be its own bespoke _process_shutdown_combo().

_si_last_move_at = None

def _held_space_invaders_direction():
    """Polls current input state (not events), mirroring
    _held_volume_direction()'s style: D-pad/hat, joystick X axis, the
    left/right arrow keys (test shim), and the web remote's virtual D-pad
    (state.web_si_direction, zero-hardware-gamepad fallback) all move the
    cannon while held."""
    if time.time() < state.web_si_direction_expires_at and state.web_si_direction != 0:
        return state.web_si_direction

    keys = pygame.key.get_pressed()
    if keys[pygame.K_LEFT]:
        return -1
    if keys[pygame.K_RIGHT]:
        return 1

    for js in joysticks:
        try:
            hx, _hy = js.get_hat(0)
        except Exception:
            hx = 0
        if hx == 1:
            return 1
        if hx == -1:
            return -1

        try:
            naxes = js.get_numaxes()
        except Exception:
            naxes = 0
        for axis in (0, 6):
            if axis >= naxes:
                continue
            try:
                val = js.get_axis(axis)
            except Exception:
                continue
            if val > 0.6:
                return 1
            if val < -0.6:
                return -1

    return 0

def _process_space_invaders_movement():
    """Called once per frame: advances the cannon at config.SI_PLAYER_SPEED
    px/sec for as long as a movement control is held. Tracks its own dt
    (rather than reusing the caller's frame delta) so it stays correct
    regardless of when in process_events() it's called."""
    global _si_last_move_at
    now = time.time()
    if _si_last_move_at is None:
        _si_last_move_at = now
    dt = now - _si_last_move_at
    _si_last_move_at = now

    if state.mode != state.MODE_SPACE_INVADERS:
        return

    direction = _held_space_invaders_direction()
    if direction != 0:
        space_invaders_engine.move_player(direction, dt)

# ------------------------------------------
# PANEL LEDS FOR LIVE MULTIPLE-CHOICE QUESTIONS (2026-09-18)
# ------------------------------------------
_panel_led_state = (False, False, False, False)

# Post-grade correct-answer LED flash (2026-09-18, operator ask: "reinforce
# a physical experience with the buttons"): on/off half-period for the
# rapid flash on the correct answer's own physical arcade button once a
# round is graded -- noticeably faster than the 1Hz blink the matrix text/
# marquee segment already use for the same reveal, since this is a single
# monochrome LED with no color/chase to lean on instead.
_CORRECT_LED_FLASH_PERIOD_SECONDS = 0.15


def _sync_panel_leds():
    """Per-frame poll: lights exactly as many panel LEDs as there are real
    choices for the currently-answerable question, dark otherwise. Reuses
    drivers/live_round_engine.py::is_round_active() -- the exact same
    condition that already gates whether pressing one of these buttons
    does anything (drivers/simon_engine.py::poll_hardware()), so this
    doesn't invent new state, it just makes the LEDs agree with what the
    buttons already do -- covers ordinary GAME_MODE questions and the
    Mystery Band teaser (still MODE_DJ) alike, correctly excludes Price
    Game.

    Per-choice-count (2026-09-18 fix): a True/False question only
    populates state.factoid_choices[0:2] ("True"/"False") -- lighting all
    4 LEDs made yellow/blue look answerable when those buttons are
    inactive. Zips config.SIMON_HW_COLOR_ORDER against len(factoid_choices)
    directly rather than special-casing True/False, so it's correct for
    any future <4-choice question type too.

    Once graded (2026-09-18): rather than just going dark like every other
    LED, the correct answer's own button LED rapidly flashes for as long as
    the round stays locked -- the same physical reinforcement the matrix
    text/marquee chase already give that same answer, but right on the
    button itself. Solo only (`not state.quiz_players`), matching every
    other piece of this reveal (graphics/matrix_canvas.py::
    _render_solo_win_panel(), drivers/wled_engine.py's matching marquee
    condition) -- a registered multiplayer round already has its own
    "CORRECT: <names>" callout instead. Bypasses the edge-triggered cache
    below since it needs to toggle every frame, not just on a state change.

    Skips entirely during MODE_SIMON -- drivers/simon_engine.py already
    owns these same LEDs for the mini-game's own pulse/hold sequences,
    and Simon never populates state.factoid_choices, so the two are
    naturally mutually exclusive; this just stays out of the way rather
    than racing simon_engine.py's own set_led()/pulse_led() calls.

    Edge-triggered (_panel_led_state tracks the last per-color state
    actually sent) so this doesn't spam redundant GPIO writes every frame
    -- same "cheap no-op unless changed" convention used elsewhere in this
    app (e.g. drivers/wled_engine.py's own sync_to_show_state())."""
    global _panel_led_state
    if state.mode == state.MODE_SIMON:
        return
    if (state.quiz_locked and not state.quiz_players and state.factoid_choices
            and 0 <= state.factoid_correct_index < len(config.SIMON_HW_COLOR_ORDER)):
        flash_on = int(time.time() / _CORRECT_LED_FLASH_PERIOD_SECONDS) % 2 == 0
        for i, color in enumerate(config.SIMON_HW_COLOR_ORDER):
            simon_hardware.set_led(color, flash_on and i == state.factoid_correct_index)
        _panel_led_state = None  # force a resync once the round goes live again
        return
    choice_count = len(state.factoid_choices) if live_round_engine.is_round_active() else 0
    desired = tuple(i < choice_count for i in range(len(config.SIMON_HW_COLOR_ORDER)))
    if desired != _panel_led_state:
        for color, on in zip(config.SIMON_HW_COLOR_ORDER, desired):
            simon_hardware.set_led(color, on)
        _panel_led_state = desired


# ------------------------------------------
# EVENT DISPATCHER
# ------------------------------------------
def process_events():
    global last_axis_x, last_axis_y
    global _btn1_down_at, _btn1_hold_fired

    # Section 1: as soon as the active deck's track is confidently
    # identified, keep its question queue topped up in the background --
    # no Btn6 press required. Cheap/no-op once the track's cache holds
    # TRACK_QUESTIONS_PER_TRACK questions.
    title, artist = _current_dj_track()
    confident = state.deck1_confident if state.active_deck == 1 else state.deck2_confident
    ensure_prefetch(title, artist, confident)
    mystery_band_engine.check_new_track(title, artist, confident)
    hot_track_engine.update(time.time())

    _process_quiz_gate()
    deck_orchestrator.update(time.time())
    price_game_engine.update(time.time())
    mystery_band_engine.update(time.time())
    auto_dj_engine.update(time.time())
    westminster_engine.update(time.time())
    idle_cycle_engine.update(time.time())
    live_round_engine.update(time.time())
    _sync_panel_leds()
    win_sequence_engine.update(time.time())
    light_prefs_engine.update(time.time())
    show_engine.update(time.time())
    if state.mode == state.MODE_SPACE_INVADERS:
        space_invaders_engine.update(time.time())
    if state.mode == state.MODE_SIMON:
        simon_engine.update(time.time())
    simon_engine.poll_hardware(time.time())

    for event in pygame.event.get():
        if event.type in (pygame.JOYBUTTONDOWN, pygame.JOYHATMOTION, pygame.KEYDOWN):
            # Unattended-autoplay fallback (drivers/show_engine.py): any
            # deliberate physical-rig press resets the Setup-page idle
            # clock. Cheap no-op outside show_phase == "setup".
            show_engine.mark_operator_interaction()

        if event.type == pygame.QUIT:
            return False

        elif event.type == pygame.WINDOWCLOSE:
            # Multi-window guard (Feature Update -- Secondary HDMI Fullscreen
            # Fallback Canvas): pygame-ce's SDL2 multi-window support tags
            # per-window close clicks as WINDOWCLOSE (not the app-wide QUIT
            # above), carrying which window it belongs to. The fallback
            # canvas is only ever closable in its single-monitor windowed
            # mode (the auto-placed fullscreen window is borderless, no
            # close button) -- if that's what this is, just hide it rather
            # than falling through and taking the whole show down.
            if secondary_canvas.is_secondary_window_event(event):
                secondary_canvas.handle_window_close()

        elif event.type == pygame.JOYBUTTONDOWN:
            btn = event.button
            print(f"[BUTTON] Raw Button Pressed: {btn}")

            # Press-to-bind capture (Joy Assign page, web/static/
            # joyassign.html): consumes this press entirely if capture
            # mode is currently armed -- no normal dispatch happens for
            # it at all, regardless of mode.
            if joystick_bindings.offer_capture(btn):
                continue

            # Combo suppression (generalized 2026-08-20 -- see
            # _button_is_forming_a_combo()'s docstring): if btn is part of
            # a combo (applicable to this mode) whose other buttons are
            # ALSO already held, this press is forming that combo -- don't
            # ALSO fire its own single-press action on top.
            # _process_combos() (polled every frame, not event-driven)
            # owns the actual hold-to-fire logic.
            if _button_is_forming_a_combo(btn):
                continue

            if state.mode == state.MODE_DJ:
                action = joystick_bindings.action_for_button("DJ", btn)
                if action == "dj_auto_announce":
                    # Armed here, resolved to a tap (Auto-Announce toggle)
                    # or a hold (status overlay) on release -- see
                    # _handle_btn1_release()/_process_btn1_hold().
                    _btn1_down_at = time.time()
                    _btn1_hold_fired = False
                elif action == "dj_swear_toggle":
                    # Plain press, no tap/hold split -- toggles the swear
                    # tag on the last-played announcement; JOYBUTTONUP
                    # below clears state.btn3_swear_toggle_active once
                    # this same physical button releases.
                    toggle_last_announcement_swear()
                elif action is not None:
                    _dispatch_action(action)
                # else: unbound button in DJ mode -- silent no-op (matches
                # the original Btn4 no-op, now generalized to any
                # currently-unassigned button).

            elif state.mode == state.MODE_GAME:
                action = joystick_bindings.action_for_button("GAME", btn)
                if action is not None:
                    _dispatch_action(action)

            elif state.mode == state.MODE_SPACE_INVADERS:
                si_exit_buttons = joystick_bindings.buttons_for(["si_exit_1", "si_exit_2"])
                if btn in si_exit_buttons:  # IMMEDIATE exit
                    if _debounced("si_exit"):
                        space_invaders_engine.exit_space_invaders()
                else:                        # Any other button: fire
                    space_invaders_engine.fire()

            elif state.mode == state.MODE_SIMON:
                action = joystick_bindings.action_for_button("SIMON", btn)
                if action is not None:
                    _dispatch_action(action)

        elif event.type == pygame.JOYBUTTONUP:
            if state.mode == state.MODE_DJ:
                if event.button == joystick_bindings.button_for("dj_auto_announce"):
                    _handle_btn1_release()
                elif event.button == joystick_bindings.button_for("dj_swear_toggle"):
                    state.btn3_swear_toggle_active = False

        elif event.type == pygame.MOUSEBUTTONDOWN:
            if event.button == 1:  # left click
                click_id = overlay_panel.hit_test(event.pos)
                if click_id:
                    overlay_panel.handle_click(click_id)

        elif event.type == pygame.JOYHATMOTION:
            hat_x, hat_y = event.value
            if state.mode in (state.MODE_DJ, state.MODE_GAME):
                if hat_y == 1 or hat_x == 1:
                    handle_dj_volume(5)
                elif hat_y == -1 or hat_x == -1:
                    handle_dj_volume(-5)

        elif event.type == pygame.JOYAXISMOTION:
            if event.axis in (0, 1, 6, 7):
                val = 1 if event.value > 0.6 else (-1 if event.value < -0.6 else 0)

                if event.axis in (1, 7) and val != last_axis_y:
                    last_axis_y = val
                    if val != 0 and state.mode in (state.MODE_DJ, state.MODE_GAME):
                        # Reversed per Section 2.1.
                        vol_delta = 5 if val == 1 else -5
                        handle_dj_volume(vol_delta)

                elif event.axis in (0, 6) and val != last_axis_x:
                    last_axis_x = val
                    if val == -1 and state.show_phase == "dark":
                        # Trivia Night show flow (2026-08-18): the
                        # operator's manual cue to leave the silent dark
                        # pause and actually start the scripted open.
                        # Takes priority over the normal MODE_DJ mapping
                        # below for the same reason the intro-skip branch
                        # does -- nothing useful happens from the deck
                        # during dark/intro anyway.
                        show_engine.begin_intro()
                    elif val == -1 and state.show_phase == "intro":
                        # Trivia Night show flow (2026-08-13): during the
                        # scripted open, X- ends the intro immediately and
                        # hands off into live gameplay -- the intro has no
                        # fixed end time anymore, so this is the ONLY thing
                        # that ends it, giving the live host's own spoken
                        # intro over the ShowStart.mp3 music as much room as
                        # they need. Takes priority over the normal
                        # MODE_DJ "Next Track" mapping below, which
                        # wouldn't do anything useful anyway since the deck
                        # is silent throughout the intro.
                        show_engine.skip_intro()
                    elif val == 1 and state.mode == state.MODE_GAME:
                        select_quiz_answer(2)
                    elif val == 1 and state.mode == state.MODE_DJ:
                        # X+ axis: Previous Track (mirrors X- = Next below).
                        deck_orchestrator.trigger_track_move("back")
                        auto_dj_engine.notify_manual_track_move()
                    elif val == -1 and state.mode == state.MODE_DJ:
                        deck_orchestrator.trigger_track_move("next")
                        auto_dj_engine.notify_manual_track_move()

        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_TAB:
                state.toggle_mode()
            elif event.key == pygame.K_q:
                return False
            elif event.key == pygame.K_RIGHT and state.show_phase == "dark":
                # Keyboard test shim for the Joy X- dark-to-intro mapping
                # above -- takes priority over the MODE_DJ K_RIGHT mapping
                # below for the same reason the joystick version does.
                show_engine.begin_intro()
            elif event.key == pygame.K_RIGHT and state.show_phase == "intro":
                # Keyboard test shim for the Joy X- intro-skip mapping
                # above -- takes priority over the MODE_DJ K_RIGHT mapping
                # below for the same reason the joystick version does.
                show_engine.skip_intro()

            elif state.mode == state.MODE_DJ:
                if event.key == pygame.K_UP:
                    handle_dj_volume(5)
                elif event.key == pygame.K_DOWN:
                    handle_dj_volume(-5)
                elif event.key == pygame.K_RIGHT:
                    # Keyboard test shim for the X- "Next" axis mapping.
                    deck_orchestrator.trigger_track_move("next")
                    auto_dj_engine.notify_manual_track_move()
                elif event.key == pygame.K_LEFT:
                    # Keyboard test shim for the Btn1/2 "Back" mapping.
                    deck_orchestrator.trigger_track_move("back")
                    auto_dj_engine.notify_manual_track_move()

            elif state.mode == state.MODE_GAME:
                if event.key == pygame.K_UP:
                    handle_dj_volume(5)
                elif event.key == pygame.K_DOWN:
                    handle_dj_volume(-5)
                elif event.key == pygame.K_1:
                    select_quiz_answer(0)
                elif event.key == pygame.K_2:
                    select_quiz_answer(1)
                elif event.key == pygame.K_3:
                    select_quiz_answer(2)
                elif event.key == pygame.K_4:
                    select_quiz_answer(3)
                elif event.key == pygame.K_5:
                    grade_quiz_selection()
                elif event.key == pygame.K_6:
                    clear_quiz_selection()
                elif event.key == pygame.K_7:
                    abort_game_mode_early()
                elif event.key == pygame.K_c:
                    trigger_clear_latches()

            elif state.mode == state.MODE_SPACE_INVADERS:
                # Keyboard test shim -- left/right movement is polled every
                # frame in _process_space_invaders_movement() below.
                if event.key == pygame.K_SPACE:
                    space_invaders_engine.fire()
                elif event.key in (pygame.K_7, pygame.K_8):
                    space_invaders_engine.exit_space_invaders()

        elif event.type in (pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED):
            init_joysticks()

    _process_volume_hold()
    _process_space_invaders_movement()
    _process_btn1_hold()
    _process_cpu_temp_overlay()
    _process_combos()

    if state.shutdown_requested:
        return False

    return True
