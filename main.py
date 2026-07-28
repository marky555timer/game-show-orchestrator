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
    print(" 256x64 MATRIX & REKORDBOX MIDI POKER     ")
    print("==========================================")
    print(f"  Enttec Port : {ENTTEC_PORT}")
    print(f"  MIDI Status : {midi_status_str}")
    print("  [TAB] / Btn 8    : Toggle Mode (DJ <-> GAME)")
    print("  DJ MODE          : Vol = MIDI CC#11, Reject = POKE CC#10")
    print("  GAME SHOW MODE   : Btn 5=Loss, Btn 4=Option A, Btn 3=Option B,")
    print("                     Btn 2=Option C, Btn 1=Clear/Reverb, Btn 6=Win")
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