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
    SI_ENTRY_BUTTONS, SI_EXIT_BUTTONS,
    BTN1_HOLD_THRESHOLD_SECONDS, BTN1_HOLD_OVERLAY_PERSIST_SECONDS,
    BTN3_HOLD_THRESHOLD_SECONDS,
    SHUTDOWN_COMBO_BUTTONS, SHUTDOWN_COMBO_HOLD_SECONDS,
    FORCE_PRICE_GAME_COMBO_BUTTONS, FORCE_PRICE_GAME_COMBO_HOLD_SECONDS,
)
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
from drivers import westminster_engine
from drivers import idle_cycle_engine
from drivers import live_round_engine
from drivers import win_sequence_engine
from drivers import light_prefs_engine
from drivers import show_engine
from graphics import overlay_panel
from graphics import secondary_canvas
from web import qr_popup
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

# Btn1/Btn3 tap-vs-hold state (Section: joystick remap + status overlays).
# JOYBUTTONDOWN arms *_down_at and clears *_hold_fired; the per-frame
# _process_btnX_hold() polls promote a still-held press past *_hold_fired
# once BTNx_HOLD_THRESHOLD_SECONDS elapses; JOYBUTTONUP's
# _handle_btnX_release() reads *_hold_fired to decide tap vs. hold.
_btn1_down_at = None
_btn1_hold_fired = False
_btn3_down_at = None
_btn3_hold_fired = False

# Shutdown combo (Feature Update): Btn5+Btn2 held together continuously for
# SHUTDOWN_COMBO_HOLD_SECONDS. None while the combo isn't currently held;
# set to the time.time() the combo FIRST became held once both buttons are
# down together, so elapsed hold time survives across frames.
_shutdown_combo_since = None

# Force Price Game combo: Btn5+Btn6 held together continuously for
# FORCE_PRICE_GAME_COMBO_HOLD_SECONDS -- same shape as the shutdown combo
# above. NOT a joystick-axis long-press (an earlier X- hold version was
# tried and reverted): the X-axis is edge-triggered straight to
# deck_orchestrator.trigger_track_move("next") the instant it crosses the
# threshold, in the SAME JOYAXISMOTION handler, before any hold-duration
# check could ever tell a tap from a hold -- so holding X- also fired an
# unwanted, audience-visible track transition every single time. A
# two-button combo has no such quick-tap side effect to worry about.
_force_price_game_combo_since = None
_force_price_game_fired = False

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
    """`play_sfx=False`: used whenever a round times out with nobody having
    actually interacted with it. Two call sites: (2026-08-12)
    _grade_multiplayer_round(), for the mystery question specifically, when
    literally nobody answered (state.round_first_answer_at == 0) -- by
    ~40s in, the visual teaser has long since resolved and the matrix is
    back to normal idle content, so an unprompted buzzer with no on-screen
    context read as a random, confusing noise. (2026-08-13)
    _do_grade_quiz_selection()'s solo path, for ANY question timing out
    with the operator never having armed a selection at all
    (state.quiz_selected_index < 0) -- an untouched app left running as a
    plain jukebox (no players joined, operator never presses a button)
    would otherwise buzz on every single song's mystery-question timeout,
    forever, with nobody around to have caused or expect it. Still does
    the DMX/message bookkeeping either way, just skips the audible SFX."""
    stop_previous_audio()
    state.active_option = None
    state.set_message("WRONG ANSWER! (LOSS BUZZER)", 1.5)
    print("[ACTION] Btn6 GRADE -> LOSS")
    if state.sfx_enabled and play_sfx:
        play_processed_sound(raw_buzzer)

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

    # Jukebox mode (2026-08-13): a totally untouched app still auto-arms a
    # mystery question on every new song (drivers/mystery_band_engine.py),
    # and with nobody signed up as a player, that question can only ever
    # grade through this solo path -- so on a cold start where the operator
    # never once presses a button, this is the ONLY grading that happens,
    # over and over, one per song. No selection ever armed means nobody
    # interacted with THIS round at all; the loss buzzer below is skipped
    # for exactly that case so the app can just sit there running as a
    # plain jukebox without buzzing at nobody.
    silent_timeout = state.round_timed_out and state.quiz_selected_index < 0

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
    (2026-08-10). Mirrors exactly what Btn6-during-the-teaser already does
    (mystery_band_engine.enter_game_from_mystery() + the state.mode flip in
    handle_quiz_gate_button()) -- this just does it automatically the
    instant grading happens via any path (auto-grade, timeout, or an
    explicit Grade press), not only a dedicated Btn6-during-teaser press.

    Skipped once a winner's been declared this game (2026-08-11) -- the
    game is over and headed into intermission, so there's nothing to
    advance into."""
    if state.game_winner_player_id or state.intermission_active:
        return
    if state.mode != state.MODE_GAME and state.mystery_active:
        mystery_band_engine.enter_game_from_mystery()
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
        results.append({"initials": player["initials"], "correct": correct})
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
        # Silent mystery-question timeout (2026-08-12): if this is the
        # identify_band question, it timed out (not graded because
        # everyone answered), and literally nobody ever answered at all,
        # the visual teaser resolved and the matrix reverted to idle
        # content long before this ~40s deadline hit -- an unprompted
        # buzzer at that point has no on-screen context and just reads as
        # a random noise. Skip the SFX for that specific case only; a real
        # wrong answer (or a timeout where SOMEONE at least tried) still buzzes.
        silent_timeout = (state.factoid_category == "identify_band"
                           and state.round_timed_out and not state.round_first_answer_at)
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
        play_processed_sound(raw_bigwin)

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
        load_exhausted_fallback_question(title, artist)
        state.quiz_gate_status = "idle"
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
        load_exhausted_fallback_question(title, artist)
        state.quiz_gate_status = "idle"
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

def handle_color_cycle():
    """Btn7 in DJ mode: cycles the main uplighting theme color."""
    state.dj_color_index = (state.dj_color_index + 1) % len(DJ_COLOR_PALETTE)
    light_prefs_engine.mark_dirty()
    print(f"[ACTION] Btn7 COLOR -> index {state.dj_color_index}")

def handle_theme_cycle():
    """Btn8 in DJ mode: cycles the DJ_THEME_COUNT animated uplighting
    themes. Deliberately excludes DJ_THEME_ALL_OFF_INDEX -- landing on a
    dark stop while cycling through patterns live reads as an error, not a
    lighting choice. ALL LIGHTS OFF is still reachable as a deliberate
    direct selection from the admin panel (web/remote_server.py's
    /api/dmx/scene), just never something you can cycle into by accident."""
    state.dj_theme_index = (state.dj_theme_index + 1) % DJ_THEME_COUNT
    light_prefs_engine.mark_dirty()
    print(f"[ACTION] Btn8 THEME -> theme {state.dj_theme_index}")

# ------------------------------------------------------------
# SECTION 4: AUTO-DJ TOGGLE
# ------------------------------------------------------------
# No physical-button trigger (2026-08-12 -- removed from Btn4, see the
# JOYBUTTONDOWN dispatch above). auto_dj_engine.toggle_auto_dj() is still
# reachable from the web remote's Auto-DJ control (web/remote_server.py).

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


def _process_force_price_game_combo():
    """Per-frame: Btn5+Btn6 held together continuously for
    FORCE_PRICE_GAME_COMBO_HOLD_SECONDS force-starts a Price Game round
    (force_price_game()). Same shape as _process_shutdown_combo() below --
    hardware polling (not JOYBUTTONDOWN events), since a "held together"
    combo can't be detected reliably off events alone (whichever button's
    event fires first can't see the other's state yet mid-event-loop).
    `_force_price_game_fired` latches so this fires exactly once per hold,
    not every single frame the combo stays down past threshold -- without
    it, the second and later frames would see price_game_pending already
    cleared (start_price_game() clears it on success) and spam the "NO
    PRICE GAME ARMED YET" status message right after a successful trigger."""
    global _force_price_game_combo_since, _force_price_game_fired
    both_held = (
        _joystick_button_held(FORCE_PRICE_GAME_COMBO_BUTTONS[0])
        and _joystick_button_held(FORCE_PRICE_GAME_COMBO_BUTTONS[1])
    )
    if not both_held:
        _force_price_game_combo_since = None
        _force_price_game_fired = False
        return
    if _force_price_game_combo_since is None:
        _force_price_game_combo_since = time.time()
        return
    if (not _force_price_game_fired
            and time.time() - _force_price_game_combo_since >= FORCE_PRICE_GAME_COMBO_HOLD_SECONDS):
        _force_price_game_fired = True
        force_price_game()

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
    if _joystick_button_held(0) and time.time() - _btn1_down_at >= BTN1_HOLD_THRESHOLD_SECONDS:
        _btn1_hold_fired = True
        state.btn1_hold_overlay_active = True

def _process_btn3_hold():
    """Same pattern as _process_btn1_hold() for Btn3 (token overlay)."""
    global _btn3_hold_fired
    if state.mode != state.MODE_DJ or _btn3_down_at is None or _btn3_hold_fired:
        return
    if _joystick_button_held(2) and time.time() - _btn3_down_at >= BTN3_HOLD_THRESHOLD_SECONDS:
        _btn3_hold_fired = True
        state.btn3_token_overlay_active = True

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

    if _debounced(0):
        handle_auto_announce_toggle()

def _handle_btn3_release():
    """JOYBUTTONUP for Btn3 (DJ mode only). Token overlay hides immediately
    on release either way (no persistence, unlike Btn1); a quick tap opens
    the QR remote popup. Same _btn3_down_at is None guard as Btn1 above for
    Space Invaders combo safety."""
    global _btn3_down_at, _btn3_hold_fired
    if _btn3_down_at is None:
        return
    was_hold = _btn3_hold_fired
    _btn3_down_at = None
    _btn3_hold_fired = False
    state.btn3_token_overlay_active = False

    if was_hold:
        return

    if _debounced(2):
        qr_popup.trigger_qr_popup()

# ------------------------------------------
# SHUTDOWN COMBO: Btn5 + Btn2 HELD TOGETHER FOR 5 CONTINUOUS SECONDS
# ------------------------------------------
def _process_shutdown_combo():
    """Called once per frame, regardless of mode -- an emergency/admin
    shutdown control shouldn't be gated behind DJ mode. Polls hardware
    state (not events) the same way _held_volume_direction() does, since a
    "held together" combo can't be detected reliably off JOYBUTTONDOWN
    events alone (whichever button's event fires first can't see the
    other's state yet mid-event-loop)."""
    global _shutdown_combo_since
    if state.shutdown_requested:
        return  # already triggered (from here or the web remote) -- no-op

    both_held = (
        _joystick_button_held(SHUTDOWN_COMBO_BUTTONS[0])
        and _joystick_button_held(SHUTDOWN_COMBO_BUTTONS[1])
    )
    if not both_held:
        _shutdown_combo_since = None
        return

    if _shutdown_combo_since is None:
        _shutdown_combo_since = time.time()
        return

    if time.time() - _shutdown_combo_since >= SHUTDOWN_COMBO_HOLD_SECONDS:
        print("SYSTEM SHUTDOWN: Triggered via Gamepad 5+2 5-second hold")
        state.shutdown_reason = "GAMEPAD 5+2 HOLD"
        state.shutdown_requested = True

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
# EVENT DISPATCHER
# ------------------------------------------
def process_events():
    global last_axis_x, last_axis_y
    global _btn1_down_at, _btn1_hold_fired, _btn3_down_at, _btn3_hold_fired

    # Section 1: as soon as the active deck's track is confidently
    # identified, keep its question queue topped up in the background --
    # no Btn6 press required. Cheap/no-op once the track's cache holds
    # TRACK_QUESTIONS_PER_TRACK questions.
    title, artist = _current_dj_track()
    confident = state.deck1_confident if state.active_deck == 1 else state.deck2_confident
    ensure_prefetch(title, artist, confident)
    mystery_band_engine.check_new_track(title, artist, confident)

    _process_quiz_gate()
    deck_orchestrator.update(time.time())
    price_game_engine.update(time.time())
    mystery_band_engine.update(time.time())
    auto_dj_engine.update(time.time())
    westminster_engine.update(time.time())
    idle_cycle_engine.update(time.time())
    live_round_engine.update(time.time())
    win_sequence_engine.update(time.time())
    light_prefs_engine.update(time.time())
    show_engine.update(time.time())
    if state.mode == state.MODE_SPACE_INVADERS:
        space_invaders_engine.update(time.time())

    for event in pygame.event.get():
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

            # Space Invaders dual-button entry (Section 1): Btn1 AND Btn3
            # held simultaneously, DJ mode only. Checked before the normal
            # single-button DJ actions below -- if the combo actually
            # fires, this event is fully consumed so Btn1's Auto-Announce
            # toggle / Btn3's "back" track move never also happen on top
            # of it. If the other combo button ISN'T currently held, this
            # falls through to the ordinary single-button handling
            # untouched.
            if state.mode == state.MODE_DJ and btn in SI_ENTRY_BUTTONS:
                other_btn = SI_ENTRY_BUTTONS[1] if btn == SI_ENTRY_BUTTONS[0] else SI_ENTRY_BUTTONS[0]
                if _joystick_button_held(other_btn):
                    if _debounced("si_entry"):
                        space_invaders_engine.enter_space_invaders()
                    continue

            # Force Price Game combo (Btn5+Btn6): suppress each button's own
            # single-press action (tempo tap / bank-sourced Price Game)
            # while the OTHER combo button is already held -- otherwise
            # forming the combo would also fire an unwanted tempo
            # recalculation and, worse, an unwanted bank Price Game round
            # (or intermission-blocked message) from the stray individual
            # Btn6 press, before the hold timer even reaches
            # FORCE_PRICE_GAME_COMBO_HOLD_SECONDS.
            # _process_force_price_game_combo() (polled every frame, not
            # event-driven) owns the actual hold-to-fire logic; this just
            # keeps the two buttons' normal taps out of the way while it's
            # forming. Same shape as the Space Invaders combo check above.
            if state.mode == state.MODE_DJ and btn in FORCE_PRICE_GAME_COMBO_BUTTONS:
                other_btn = (FORCE_PRICE_GAME_COMBO_BUTTONS[1] if btn == FORCE_PRICE_GAME_COMBO_BUTTONS[0]
                             else FORCE_PRICE_GAME_COMBO_BUTTONS[0])
                if _joystick_button_held(other_btn):
                    continue

            if state.mode == state.MODE_DJ:
                if btn == 0:  # Physical Btn1: armed here, resolved to a tap
                    # (Auto-Announce toggle) or a hold (status overlay) on
                    # release -- see _handle_btn1_release()/_process_btn1_hold().
                    _btn1_down_at = time.time()
                    _btn1_hold_fired = False
                elif btn == 1:  # Physical Btn2: normal trivia gate (moved
                    # here from Btn6, 2026-08-12 -- Btn6 is now a dedicated
                    # Price Game trigger, see handle_quiz_gate_button()).
                    # GAME_MODE still uses Btn2 to select Answer 2.
                    if _debounced(btn, QUIZ_GATE_DEBOUNCE_SECONDS):
                        handle_normal_trivia_button()
                elif btn == 2:  # Physical Btn3: armed here, resolved to a tap
                    # (QR remote popup) or a hold (token overlay) on release.
                    _btn3_down_at = time.time()
                    _btn3_hold_fired = False
                elif btn == 3:  # Physical Btn4: no-op in DJ mode (2026-08-12
                    # -- too easy to bump by accident, and Auto-DJ is almost
                    # never turned off in practice). auto_dj_engine.
                    # toggle_auto_dj() itself is untouched -- still reachable
                    # from the web remote's Auto-DJ control (web/
                    # remote_server.py) -- this only removes the physical-
                    # button trigger. GAME_MODE still uses Btn4 to select
                    # Answer 1.
                    pass
                elif btn == 5:  # Physical Btn5 (L shoulder): tempo tap
                    if _debounced(btn):
                        handle_tempo_tap()
                elif btn == 6:  # Physical Btn6 (R shoulder): force Price Game (local bank)
                    if _debounced(btn, QUIZ_GATE_DEBOUNCE_SECONDS):
                        handle_quiz_gate_button()
                elif btn == 9:  # Physical Btn7 (Select): color cycle
                    if _debounced(btn):
                        handle_color_cycle()
                elif btn == 10:  # Physical Btn8 (Start): theme cycle
                    if _debounced(btn):
                        handle_theme_cycle()

            elif state.mode == state.MODE_GAME:
                if btn == 0:        # Physical Btn1: select Answer 4 (index 3)
                    select_quiz_answer(3)
                elif btn == 1:      # Physical Btn2: select Answer 2 (index 1)
                    select_quiz_answer(1)
                elif btn == 2:      # Physical Btn3: select Answer 3 (index 2) -- was unmapped
                    select_quiz_answer(2)
                elif btn == 3:      # Physical Btn4: select Answer 1 (index 0)
                    select_quiz_answer(0)
                elif btn == 5:      # Physical Btn5 (L shoulder): GAME_MODE-only swap -> clear the current selection
                    if _debounced(btn):
                        clear_quiz_selection()
                elif btn == 6:      # Physical Btn6 (R shoulder): GAME_MODE-only swap -> grade the current selection
                    if _debounced(btn):
                        grade_quiz_selection()
                elif btn == 9:      # Physical Btn7 (Select): EARLY EXIT -> abort back to DJ_MODE
                    if _debounced(btn):
                        abort_game_mode_early()
                elif btn == 10:     # Physical Btn8 (Start): also EARLY EXIT (2026-08-09 --
                    if _debounced(btn):  # same as Btn7, just a second way out)
                        abort_game_mode_early()

            elif state.mode == state.MODE_SPACE_INVADERS:
                if btn in SI_EXIT_BUTTONS:  # Physical Btn7 or Btn8: IMMEDIATE exit
                    if _debounced("si_exit"):
                        space_invaders_engine.exit_space_invaders()
                else:                        # Any other button: fire
                    space_invaders_engine.fire()

        elif event.type == pygame.JOYBUTTONUP:
            if state.mode == state.MODE_DJ:
                if event.button == 0:
                    _handle_btn1_release()
                elif event.button == 2:
                    _handle_btn3_release()

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
                    if val == -1 and state.show_phase == "intro":
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
    _process_btn3_hold()
    _process_shutdown_combo()
    _process_force_price_game_combo()

    if state.shutdown_requested:
        return False

    return True
