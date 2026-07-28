import time
import pygame
from state import state
from drivers.midi_driver import handle_dj_volume, handle_dj_reject
from drivers.rekordbox_driver import trigger_next_track
from drivers.dmx_driver import dmx
from audio.audio_engine import (
    play_processed_sound, raw_buzzer, raw_ding, raw_bigwin, raw_clear,
    stop_previous_audio, reverb_enabled
)

# Active DMX Animation State
active_animation = None
latched_rgb = None

# Gamepad Axis State Tracking
last_axis_x = 0
last_axis_y = 0
joysticks = []

def init_joysticks():
    global joysticks
    pygame.joystick.init()
    count = pygame.joystick.get_count()
    print(f"\n[JOYSTICK STATUS] Detected {count} controller(s).")
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
    print("[ACTION] Physical Btn 5 -> LOSS")
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

def trigger_color_latch(r, g, b, option_code, color_name, btn_id):
    global active_animation, latched_rgb
    stop_previous_audio()
    active_animation = None
    latched_rgb = (r, g, b)
    state.active_option = option_code
    state.set_message(f"OPTION {option_code} LATCHED ({color_name})", 1.5)
    print(f"[ACTION] Physical Btn {btn_id} -> LATCH OPTION {option_code} ({color_name})")
    play_processed_sound(raw_ding)
    dmx.set_rgb(*latched_rgb, dimmer=255)
    dmx.render()

def trigger_clear_latches():
    global active_animation, latched_rgb
    import audio.audio_engine as ae
    stop_previous_audio()
    active_animation = None
    latched_rgb = None
    state.active_option = None
    ae.reverb_enabled = not ae.reverb_enabled
    state_str = "ON" if ae.reverb_enabled else "OFF"
    state.set_message(f"CLEAR LATCHES | REVERB: {state_str}", 1.2)
    print(f"[ACTION] Physical Btn 1 -> CLEAR LATCHES | REVERB: {state_str}")
    play_processed_sound(raw_clear)
    dmx.blackout()

def trigger_big_win():
    global active_animation, latched_rgb
    stop_previous_audio()
    latched_rgb = None
    state.set_message("CORRECT ANSWER! BIG WIN!", 2.0)
    print("[ACTION] Physical Btn 6 -> BIG WIN")
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
                if btn == 4:       # Physical Btn 5
                    trigger_loss()
                elif btn == 3:     # Physical Btn 4
                    trigger_color_latch(255, 20, 147, "A", "Hot Pink", 4)
                elif btn == 2:     # Physical Btn 3
                    trigger_color_latch(137, 207, 240, "B", "Baby Blue", 3)
                elif btn == 1:     # Physical Btn 2
                    trigger_color_latch(46, 139, 87, "C", "Sea Green", 2)
                elif btn == 0:     # Physical Btn 1
                    trigger_clear_latches()
                elif btn == 5:     # Physical Btn 6
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
                    trigger_color_latch(255, 20, 147, "A", "Hot Pink", 4)
                elif event.key == pygame.K_2:
                    trigger_color_latch(137, 207, 240, "B", "Baby Blue", 3)
                elif event.key == pygame.K_3:
                    trigger_color_latch(46, 139, 87, "C", "Sea Green", 2)
                elif event.key == pygame.K_4:
                    trigger_loss()
                elif event.key == pygame.K_5:
                    trigger_big_win()
                elif event.key == pygame.K_c:
                    trigger_clear_latches()

        elif event.type in (pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED):
            init_joysticks()

    return True