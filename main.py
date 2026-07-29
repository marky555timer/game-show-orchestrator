import sys
import pygame
from config import ENTTEC_PORT
from drivers.midi_driver import midi_status_str
from drivers.dmx_driver import dmx
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
    print("  [TAB] / Btn 8    : Toggle Mode (DJ <-> GAME)")
    print("  DJ MODE          : Vol = MIDI CC#11, Reject = POKE CC#10")
    print("                     Up/Down or joystick = Volume (hold to keep advancing)")
    print("  GAME SHOW MODE   : Btn 4=Select Ans1, Btn 2=Select Ans2, Btn 3=Select Ans3, Btn 1=Select Ans4")
    print("                     Btn 5=Grade Selection, Btn 6=Manual Win Override")
    print("                     Keyboard: 1-4=Select Answer 1-4, 5=Grade, 6=Manual Win, C=Clear")
    print("  [Q] KEY          : Quit")
    print("==========================================\n")

    while running:
        clock.tick(40)
        
        # 1. Process Inputs & Game Logic
        running = process_events()
        
        # 2. Render Canvas & LED Grid
        update_matrix_canvas()
        render_led_grid()

    # Shutdown
    dmx.blackout()
    pygame.quit()
    sys.exit()

if __name__ == "__main__":
    main()