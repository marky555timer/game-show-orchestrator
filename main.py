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
    print("                     Track questions pre-fetch in the background (3/track, Haiku) the")
    print("                     instant a deck's track is confidently identified -- no button needed.")
    print("                     Btn1 = Auto-Announcement ON/OFF toggle (default ON; station VO overlay")
    print("                     from audio/announcements/ bridges each Auto-DJ track transition)")
    print("                     Btn2 or Left Arrow = Back track, X- axis or Right Arrow = Next track")
    print("                     Btn4 = Auto-DJ ON/OFF toggle (default ON; arms the transition sequence")
    print("                     ~15s before track end -- Auto-Announcement ON plays a VO then fires the")
    print("                     deck-start MIDI + TrackSearch 2s before it ends; OFF fires immediately)")
    print("                     Btn5 = Tempo Tap, Btn6 = Instant pull from pre-fetch queue")
    print("                     (10s cold-start timeout -> error buzz only, stays in DJ mode)")
    print("                     Btn7 = Uplight Color Cycle, Btn8 = Uplight Theme Cycle")
    print("                     Btn1+Btn3 (held together) = SPACE INVADERS mini-game")
    print("  SPACE INVADERS   : D-pad/X-axis or Left/Right Arrow = Move Cannon, any other")
    print("                     button (or Space) = Fire. Btn7 or Btn8 = IMMEDIATE EXIT to DJ mode")
    print("  QUIZ MODE        : Btn4=Select Ans1, Btn2=Select Ans2, X+ axis=Select Ans3, Btn1=Select Ans4")
    print("                     Btn5=Grade Selection, Btn6=Clear Selection")
    print("                     Btn7 = EARLY EXIT (abort round, back to DJ mode immediately)")
    print("                     Keyboard: 1-4=Select Answer 1-4, 5=Grade, 6=Clear Selection,")
    print("                     7=Early Exit, C=Clear/Reroll")
    print("                     After grading: 5s scorecard, then auto-advances to the next queued")
    print("                     question (same track) or returns to DJ mode if none remain.")
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