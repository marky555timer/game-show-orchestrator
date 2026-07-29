import time
import json
import pygame
from config import (
    VOLUME_HOLD_INITIAL_DELAY_SECONDS, VOLUME_HOLD_REPEAT_INTERVAL_SECONDS,
    BUTTON_DEBOUNCE_SECONDS, QUIZ_GATE_DEBOUNCE_SECONDS, QUIZ_GATE_EMPTY_CACHE_TIMEOUT_SECONDS,
    TEMPO_TAP_WINDOW,
    TEMPO_PERIOD_MIN_SECONDS, TEMPO_PERIOD_MAX_SECONDS,
    DJ_THEME_COUNT, DJ_COLOR_PALETTE,
    QUIZ_CELEBRATION_HOLD_SECONDS,
)
from state import state
from drivers.midi_driver import handle_dj_volume
from drivers.rekordbox_driver import get_rekordbox_track
from drivers.factoid_engine import (
    ensure_prefetch, pull_next_from_queue, build_mock_question, load_forced_fallback_question,
)
from drivers import deck_orchestrator
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
def trigger_loss():
    stop_previous_audio()
    state.active_option = None
    state.set_message("WRONG ANSWER! (LOSS BUZZER)", 1.5)
    print("[ACTION] Btn6 GRADE -> LOSS")
    play_processed_sound(raw_buzzer)

    # Fixture 1 (win/loss indicator lamp): solid red, latched until an
    # explicit reset (board clear / new question / return to DJ mode) --
    # drivers/lighting_engine.py renders this every frame from state.
    state.fixture1_mode = "loss"
    state.fixture1_mode_set_at = time.time()

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
    play_processed_sound(raw_ding)

def grade_quiz_selection():
    """Physical Btn6 (GAME_MODE-only swap with Btn5, see process_events) /
    keyboard 5: grades whichever answer is currently armed via
    select_quiz_answer()."""
    if state.quiz_locked:
        return
    if state.quiz_selected_index < 0:
        print("[ACTION] Btn6 pressed but no answer is selected yet.")
        state.set_message("SELECT AN ANSWER FIRST", 1.2)
        return

    state.quiz_locked = True
    state.quiz_graded_at = time.time()
    letter = "ABCD"[state.quiz_selected_index]
    is_correct = (state.quiz_selected_index == state.factoid_correct_index)
    print(f"[ACTION] Btn6 GRADE -> Answer {letter} is {'CORRECT' if is_correct else 'WRONG'}")

    state.quiz_score_total += 1
    if is_correct:
        state.quiz_score_correct += 1
        trigger_big_win()
    else:
        trigger_loss()

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
    state.quiz_gate_status = "idle"
    state.set_message("MODE: DJ (GAME ABORTED)", 1.5)
    print("GAME MODE EXIT: Aborted early via Gamepad Button 7")

def trigger_big_win():
    stop_previous_audio()
    state.set_message("CORRECT ANSWER! BIG WIN!", 2.0)
    print("[ACTION] BIG WIN")
    play_processed_sound(raw_bigwin)

    # Fixture 1: pulsing green, rendered every frame by lighting_engine.py
    # from this state until the next reset (board clear / new question /
    # return to DJ mode).
    state.fixture1_mode = "win"
    state.fixture1_mode_set_at = time.time()

# ------------------------------------------
# SECTION 1: QUIZ API GATE (Btn6, DJ mode only)
# ------------------------------------------
def _current_dj_track():
    track_info = get_rekordbox_track()
    if isinstance(track_info, tuple):
        return track_info[0], track_info[1]
    return str(track_info), ""

def handle_quiz_gate_button():
    """Btn6 in DJ mode: every accepted press plays an immediate confirmation
    chime BEFORE anything else happens -- the sound never waits on a lookup,
    and playback is wrapped so a mixer hiccup can never block the state
    transition below it.

    Track questions are pre-fetched continuously in the background as soon
    as a deck's track is confidently identified (see
    drivers/factoid_engine.py::ensure_prefetch, called every frame from
    process_events() below) -- so this instantly pops the next cached
    question off state.track_question_queue and enters GAME_MODE, with NO
    network call at press time. Only a cold-start track (queue still empty --
    brand new track, still filling, or offline) falls through to the
    QUIZ_GATE_EMPTY_CACHE_TIMEOUT_SECONDS wait-then-fallback path below,
    polled every frame by _process_quiz_gate()."""
    if state.mode != state.MODE_DJ:
        return

    print(f"[BUTTON] Btn6 pressed. quiz_gate_status={state.quiz_gate_status!r}")

    try:
        play_processed_sound(raw_coin, volume=1.0)
    except Exception as e:
        print(f"[AUDIO ERROR] Btn6 coin chime failed to play: {e}")

    if state.quiz_gate_status == "fetching":
        print("[ACTION] Btn6 pressed while already waiting on the pre-fetch queue to fill.")
        return

    confident = state.deck1_confident if state.active_deck == 1 else state.deck2_confident
    if not confident or not state.factoid_track_key:
        state.set_message("NO CONFIDENT TRACK ID YET", 1.2)
        print("[ACTION] Btn6 pressed but no confident track ID -- nothing buffered yet.")
        return

    key = state.factoid_track_key
    if pull_next_from_queue(key):
        state.quiz_gate_status = "idle"
        state.mode = state.MODE_GAME
        state.set_message("QUIZ MODE", 1.0)
        print("[BUTTON] Btn6 -> instant pull from pre-fetch queue -> GAME_MODE")
        return

    print("[ACTION] Btn6 -> pre-fetch queue empty (cold start), waiting on background fetch.")
    state.quiz_gate_status = "fetching"
    state.quiz_gate_key = key
    state.quiz_gate_started_at = time.time()

def _process_quiz_gate():
    """Per-frame poll: only relevant right after a cold-start Btn6 press
    (queue was empty). Reacts the instant the background pre-fetch lands a
    question for this track, or force-falls-back once
    QUIZ_GATE_EMPTY_CACHE_TIMEOUT_SECONDS elapses since the press -- the DJ
    is never left hanging on a stuck/slow API call; a local fallback
    question is forced in and GAME_MODE is entered either way."""
    if state.quiz_gate_status != "fetching":
        return

    if state.factoid_track_key == state.quiz_gate_key and pull_next_from_queue(state.quiz_gate_key):
        state.quiz_gate_status = "idle"
        state.mode = state.MODE_GAME
        state.set_message("QUIZ MODE", 1.0)
        print("[BUTTON] Btn6 background fetch resolved -> auto-entering GAME_MODE")
        return

    timed_out = (time.time() - state.quiz_gate_started_at) >= QUIZ_GATE_EMPTY_CACHE_TIMEOUT_SECONDS
    if not timed_out:
        return  # still legitimately waiting, still inside the tripwire window

    state.quiz_gate_status = "idle"
    print(f"[BUTTON ERROR] Btn6 pre-fetch queue still empty after "
          f"{QUIZ_GATE_EMPTY_CACHE_TIMEOUT_SECONDS}s TIMEOUT TRIPWIRE. Loading Fallback.")
    state.coin_pop_flash_until = time.time() + 2.0
    try:
        play_processed_sound(raw_buzz_short, volume=1.0)
    except Exception as e:
        print(f"[AUDIO ERROR] Btn6 fallback buzz failed to play: {e}")

    load_forced_fallback_question("BTN6_TIMEOUT_FALLBACK")
    state.mode = state.MODE_GAME
    state.set_message("QUIZ MODE (OFFLINE FALLBACK)", 1.2)

# ------------------------------------------
# SECTION 1B: EMERGENCY OVERRIDE (Btn1 / pygame index 0, DJ mode only)
# ------------------------------------------
def handle_force_override():
    """Btn1 (pygame index 0) in DJ mode: emergency manual override for when
    Btn6's fetch path is stuck or unresponsive. Skips the AI fetch and track
    matching entirely -- plays the coin chime instantly and force-enters
    GAME_MODE with a question pulled straight from fallback_questions.json.
    Scoped to DJ mode only so it never collides with the GAME_MODE Btn1
    binding (select Answer 4)."""
    stop_previous_audio()
    try:
        play_processed_sound(raw_coin, volume=1.0)
    except Exception as e:
        print(f"[AUDIO ERROR] Btn0 override coin chime failed to play: {e}")

    print("FORCE OVERRIDE: Button 0 triggered GAME_MODE")
    state.quiz_gate_status = "idle"
    load_forced_fallback_question("BTN0_FORCE_OVERRIDE")
    state.mode = state.MODE_GAME
    state.set_message("FORCE OVERRIDE: QUIZ MODE", 1.0)

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
    print(f"[ACTION] Btn5 TEMPO TAP -> period {state.dj_tempo_period:.2f}s")

def handle_color_cycle():
    """Btn7 in DJ mode: cycles the main uplighting theme color."""
    state.dj_color_index = (state.dj_color_index + 1) % len(DJ_COLOR_PALETTE)
    print(f"[ACTION] Btn7 COLOR -> index {state.dj_color_index}")

def handle_theme_cycle():
    """Btn8 in DJ mode: cycles the 4 uplighting themes, with an
    ALL-LIGHTS-OFF stop before looping back to theme 1."""
    state.dj_theme_index = (state.dj_theme_index + 1) % (DJ_THEME_COUNT + 1)
    label = "ALL LIGHTS OFF" if state.dj_theme_index == DJ_THEME_COUNT else f"theme {state.dj_theme_index}"
    print(f"[ACTION] Btn8 THEME -> {label}")

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
    if state.mode != state.MODE_DJ:
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
# EVENT DISPATCHER
# ------------------------------------------
def process_events():
    global last_axis_x, last_axis_y

    # Section 1: as soon as the active deck's track is confidently
    # identified, keep its question queue topped up in the background --
    # no Btn6 press required. Cheap/no-op once the track's cache holds
    # TRACK_QUESTIONS_PER_TRACK questions.
    title, artist = _current_dj_track()
    confident = state.deck1_confident if state.active_deck == 1 else state.deck2_confident
    ensure_prefetch(title, artist, confident)

    _process_quiz_gate()
    deck_orchestrator.update(time.time())

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            return False

        elif event.type == pygame.JOYBUTTONDOWN:
            btn = event.button
            print(f"[BUTTON] Raw Button Pressed: {btn}")

            if state.mode == state.MODE_DJ:
                if btn == 0:  # Physical Btn1: EMERGENCY FORCE OVERRIDE -> GAME_MODE
                    if _debounced(btn):
                        handle_force_override()
                elif btn in (1, 2):
                    deck_orchestrator.trigger_track_move("back")
                elif btn == 4:  # Physical Btn5: tempo tap
                    if _debounced(btn):
                        handle_tempo_tap()
                elif btn == 5:  # Physical Btn6: quiz fetch / enter
                    if _debounced(btn, QUIZ_GATE_DEBOUNCE_SECONDS):
                        handle_quiz_gate_button()
                elif btn == 6:  # Physical Btn7: color cycle
                    if _debounced(btn):
                        handle_color_cycle()
                elif btn == 7:  # Physical Btn8: theme cycle
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
                elif btn == 4:      # Physical Btn5: GAME_MODE-only swap -> clear the current selection
                    if _debounced(btn):
                        clear_quiz_selection()
                elif btn == 5:      # Physical Btn6: GAME_MODE-only swap -> grade the current selection
                    if _debounced(btn):
                        grade_quiz_selection()
                elif btn == 6:      # Physical Btn7: EARLY EXIT -> abort back to DJ_MODE
                    if _debounced(btn):
                        abort_game_mode_early()

        elif event.type == pygame.JOYHATMOTION:
            hat_x, hat_y = event.value
            if state.mode == state.MODE_DJ:
                if hat_y == 1 or hat_x == 1:
                    handle_dj_volume(5)
                elif hat_y == -1 or hat_x == -1:
                    handle_dj_volume(-5)

        elif event.type == pygame.JOYAXISMOTION:
            if event.axis in (0, 1, 6, 7):
                val = 1 if event.value > 0.6 else (-1 if event.value < -0.6 else 0)

                if event.axis in (1, 7) and val != last_axis_y:
                    last_axis_y = val
                    if val != 0 and state.mode == state.MODE_DJ:
                        # Reversed per Section 2.1.
                        vol_delta = 5 if val == 1 else -5
                        handle_dj_volume(vol_delta)

                elif event.axis in (0, 6) and val != last_axis_x:
                    last_axis_x = val
                    if val == 1 and state.mode == state.MODE_GAME:
                        select_quiz_answer(2)
                    elif val == -1 and state.mode == state.MODE_DJ:
                        deck_orchestrator.trigger_track_move("next")

        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_TAB:
                state.toggle_mode()
            elif event.key == pygame.K_q:
                return False

            elif state.mode == state.MODE_DJ:
                if event.key == pygame.K_UP:
                    handle_dj_volume(5)
                elif event.key == pygame.K_DOWN:
                    handle_dj_volume(-5)
                elif event.key == pygame.K_RIGHT:
                    # Keyboard test shim for the X- "Next" axis mapping.
                    deck_orchestrator.trigger_track_move("next")
                elif event.key == pygame.K_LEFT:
                    # Keyboard test shim for the Btn1/2 "Back" mapping.
                    deck_orchestrator.trigger_track_move("back")

            elif state.mode == state.MODE_GAME:
                if event.key == pygame.K_1:
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

        elif event.type in (pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED):
            init_joysticks()

    _process_volume_hold()

    return True
