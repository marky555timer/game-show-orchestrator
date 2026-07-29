import sys
import time
import pygame
from config import ENTTEC_PORT
from drivers.midi_driver import midi_status_str
from drivers.dmx_driver import dmx
from drivers import lighting_engine
from graphics.matrix_canvas import update_matrix_canvas, render_led_grid
from inputs.gamepad import process_events

def main():
    clock = pygame.time.Clock()
    running = True

    print("\n==========================================")
    print(" 6-PANEL LED MATRIX & REKORDBOX MIDI POKER ")
    print("==========================================")
    print(f"  Enttec Port : {ENTTEC_PORT}")
    print(f"  MIDI Status : {midi_status_str}")
    print("  [TAB]            : Manual DJ <-> QUIZ mode override")
    print("  DJ MODE          : Vol = MIDI CC#11 (Up/Down, joystick Y-axis reversed, hold to repeat)")
    print("                     Btn1/2 or Left Arrow = Back track, X- axis or Right Arrow = Next track")
    print("                     Btn5 = Tempo Tap, Btn6 = Fetch quiz Q (press again to enter QUIZ mode)")
    print("                     Btn7 = Uplight Color Cycle, Btn8 = Uplight Theme Cycle")
    print("  QUIZ MODE        : Btn4=Select Ans1, Btn2=Select Ans2, X+ axis=Select Ans3, Btn1=Select Ans4")
    print("                     Btn 5=Grade Selection, Btn 6=Clear Selection")
    print("                     Keyboard: 1-4=Select Answer 1-4, 5=Grade, 6=Clear Selection, C=Clear/Reroll")
    print("                     Round auto-returns to DJ mode after grading + score display")
    print("  [Q] KEY          : Quit")
    print("==========================================\n")

    while running:
        clock.tick(40)
        
        # 1. Process Inputs & Game Logic
        running = process_events()

        # 2. Render the full 176-channel DMX frame (DJ uplighting themes,
        # game-mode chase, Fixture 1 win/loss/reset)
        lighting_engine.update(time.time())

        # 3. Render Canvas & LED Grid
        update_matrix_canvas()
        render_led_grid()

    # Shutdown
    dmx.blackout()
    pygame.quit()
    sys.exit()

if __name__ == "__main__":
    main()