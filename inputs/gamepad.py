import time
import pygame
from config import (
    VOLUME_HOLD_INITIAL_DELAY_SECONDS, VOLUME_HOLD_REPEAT_INTERVAL_SECONDS,
    QUIZ_SELECT_DMX_RGB, QUIZ_SELECT_DMX_DIMMER,
)
from state import state
from drivers.midi_driver import handle_dj_volume, handle_dj_reject
from drivers.rekordbox_driver import trigger_next_track
from drivers.dmx_driver import dmx
from drivers.factoid_engine import build_mock_question
from audio.audio_engine import (
    play_processed_sound, raw_buzzer, raw_bigwin, raw_clear,
    stop_previous_audio, reverb_enabled
)

# Active DMX Animation State
active_animation = None
latched_rgb = None

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
# GAME SHOW & DMX ACTION HANDLERS
# ------------------------------------------
def trigger_loss():
    global active_animation, latched_rgb
    stop_previous_audio()
    latched_rgb = None
    state.active_option = None
    state.set_message("WRONG ANSWER! (LOSS BUZZER)", 1.5)
    print("[ACTION] Btn5 GRADE -> LOSS")
    play_processed_sound(raw_buzzer)

    def anim(elapsed, duration):
        if elapsed < duration:
            dmx.set_rgb(255, 0, 0, dimmer=255)
        else:
            fade_elapsed = elapsed - duration
            fade_dur = 0.25
            if fade_elapsed < fade_dur:
                factor = 1.0 - (fade_elapsed / fade_dur)
                dmx.set_rgb(int(255 * factor), 0, 0, dimmer=int(255 * factor))
            else:
                dmx.blackout()
                return False
        return True

    sound_len = raw_buzzer.get_length() + (0.3 if reverb_enabled else 0)
    active_animation = {"func": anim, "start": time.time(), "duration": sound_len}

def trigger_clear_latches():
    """Keyboard 'C': manual reset escape hatch. Clears any armed/graded
    selection and, if the currently-loaded question is the local TEST
    placeholder, rerolls a fresh one so the select/grade flow can be
    exercised repeatedly."""
    global active_animation, latched_rgb
    import audio.audio_engine as ae
    stop_previous_audio()
    active_animation = None
    latched_rgb = None
    state.active_option = None
    state.quiz_selected_index = -1
    state.quiz_locked = False

    if state.quiz_is_test:
        mock = build_mock_question()
        state.factoid_question = mock["question"]
        state.factoid_choices = mock["choices"]
        state.factoid_correct_index = mock["correct_index"]

    ae.reverb_enabled = not ae.reverb_enabled
    state_str = "ON" if ae.reverb_enabled else "OFF"
    state.set_message(f"CLEAR LATCHES | REVERB: {state_str}", 1.2)
    print(f"[ACTION] Keyboard 'C' -> CLEAR LATCHES | REVERB: {state_str}")
    play_processed_sound(raw_clear)
    dmx.blackout()

def select_quiz_answer(index):
    """Arms (but does not grade) the chosen answer: dim outline + bright
    green fill on that panel, neutral blue/white DMX 'selected' color.
    Grading happens separately via grade_quiz_selection() on Btn5."""
    global active_animation, latched_rgb
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
    active_animation = None
    latched_rgb = QUIZ_SELECT_DMX_RGB
    dmx.set_rgb(*QUIZ_SELECT_DMX_RGB, dimmer=QUIZ_SELECT_DMX_DIMMER)
    dmx.render()

def grade_quiz_selection():
    """Physical Btn5 / keyboard 5: grades whichever answer is currently
    armed via select_quiz_answer()."""
    if state.quiz_locked:
        return
    if state.quiz_selected_index < 0:
        print("[ACTION] Btn5 pressed but no answer is selected yet.")
        state.set_message("SELECT AN ANSWER FIRST", 1.2)
        return

    state.quiz_locked = True
    state.quiz_graded_at = time.time()
    letter = "ABCD"[state.quiz_selected_index]
    is_correct = (state.quiz_selected_index == state.factoid_correct_index)
    print(f"[ACTION] Btn5 GRADE -> Answer {letter} is {'CORRECT' if is_correct else 'WRONG'}")

    if is_correct:
        trigger_big_win()
    else:
        trigger_loss()

def trigger_big_win():
    global active_animation, latched_rgb
    stop_previous_audio()
    latched_rgb = None
    state.set_message("CORRECT ANSWER! BIG WIN!", 2.0)
    print("[ACTION] BIG WIN")
    play_processed_sound(raw_bigwin)

    sound_len = raw_bigwin.get_length() + (0.3 if reverb_enabled else 0)

    def anim(elapsed, duration):
        if elapsed < duration:
            cps = 4.0
            cycle_phase = (elapsed * cps) % 1.0
            saw_value = 1.0 - cycle_phase
            dimmer = int(40 + (saw_value * 215))
            dmx.set_rgb(255, 215, 0, dimmer=dimmer)
        else:
            fade_elapsed = elapsed - duration
            fade_dur = 0.5
            if fade_elapsed < fade_dur:
                factor = 1.0 - (fade_elapsed / fade_dur)
                dmx.set_rgb(int(255 * factor), int(215 * factor), 0, dimmer=int(255 * factor))
            else:
                dmx.blackout()
                return False
        return True

    active_animation = {"func": anim, "start": time.time(), "duration": sound_len}

def handle_dj_reject_action():
    state.set_message("POKING REKORDBOX: CC#10 VAL:127", 1.8)
    print("[REKORDBOX MIDI POKE] Fire 'NEXT TRACK' (CC#10 Val:127)")
    handle_dj_reject()

    # ADVANCE LED MATRIX TRACK DISPLAY INSTANTLY
    try:
        trigger_next_track()
    except Exception:
        pass

    dmx.set_rgb(255, 0, 0, dimmer=255)
    dmx.render()

# ------------------------------------------
# VOLUME HOLD-TO-REPEAT (TV remote style)
# ------------------------------------------
def _held_volume_direction():
    """Polls current input state (not events) so a held control keeps
    reporting a direction every frame. Returns +1 / -1 / 0. Sign
    conventions match the existing discrete JOYAXISMOTION handlers below."""
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
        for axis, positive_dir in ((1, -1), (7, -1), (0, 1), (6, 1)):
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
    global active_animation, last_axis_x, last_axis_y

    # Process ongoing DMX animations
    if active_animation:
        elapsed = time.time() - active_animation["start"]
        still_running = active_animation["func"](elapsed, active_animation["duration"])
        dmx.render()
        if not still_running:
            active_animation = None

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            return False

        elif event.type == pygame.JOYBUTTONDOWN:
            btn = event.button
            print(f"[BUTTON] Raw Button Pressed: {btn}")

            if btn in (6, 7, 8, 9):
                state.toggle_mode()

            elif state.mode == state.MODE_DJ:
                if btn in (1, 2):
                    handle_dj_reject_action()

            elif state.mode == state.MODE_GAME:
                if btn == 0:        # Physical Btn1: select Answer 4 (index 3)
                    select_quiz_answer(3)
                elif btn == 1:      # Physical Btn2: select Answer 2 (index 1)
                    select_quiz_answer(1)
                elif btn == 2:      # Physical Btn3: select Answer 3 (index 2)
                    select_quiz_answer(2)
                elif btn == 3:      # Physical Btn4: select Answer 1 (index 0)
                    select_quiz_answer(0)
                elif btn == 4:      # Physical Btn5: grade the current selection
                    grade_quiz_selection()
                elif btn == 5:      # Physical Btn6: manual win override
                    state.quiz_locked = True
                    state.quiz_graded_at = time.time()
                    trigger_big_win()

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
                        vol_delta = -5 if val == 1 else 5
                        handle_dj_volume(vol_delta)

                elif event.axis in (0, 6) and val != last_axis_x:
                    last_axis_x = val
                    if val != 0 and state.mode == state.MODE_DJ:
                        vol_delta = 5 if val == 1 else -5
                        handle_dj_volume(vol_delta)

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
                elif event.key == pygame.K_SPACE:
                    handle_dj_reject_action()

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
                    state.quiz_locked = True
                    state.quiz_graded_at = time.time()
                    trigger_big_win()
                elif event.key == pygame.K_c:
                    trigger_clear_latches()

        elif event.type in (pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED):
            init_joysticks()

    _process_volume_hold()

    return True
