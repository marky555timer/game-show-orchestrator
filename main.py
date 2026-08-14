import os

# Background Input (Feature Update): let joystick button/axis events keep
# flowing even when the orchestrator window lacks OS focus, so a physical
# gamepad press still moves a deck / adjusts volume while another window is
# in front. Must be set before pygame/SDL's joystick subsystem initializes --
# the very first import below (drivers.midi_driver) already triggers
# pygame.midi.init(), so this has to come before even that.
os.environ["SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS"] = "1"

import sys
import time
import pygame
from config import ENTTEC_PORT
from state import state
from drivers.midi_driver import midi_status_str
from drivers.dmx_driver import dmx
from drivers import lighting_engine
from drivers import deck_orchestrator
from drivers.factoid_engine import save_track_cache
from graphics.matrix_canvas import update_matrix_canvas, render_led_grid
from graphics import secondary_canvas
from inputs.gamepad import process_events
from inputs import joypad_manual
from web import remote_server
from web import qr_popup
from drivers import tunnel_engine

def main():
    clock = pygame.time.Clock()
    running = True
    qr_popup.init()
    secondary_canvas.init()
    joypad_manual.write_manual()
    tunnel_engine.start()

    # Trivia Night show flow (2026-08-13): the deck stays silent at launch
    # now -- state.show_phase starts at "setup", LEDs show "START UP", and
    # the first real track only starts once the ShowStart.mp3 intro
    # choreography finishes (drivers/show_engine.py::_finish_intro(), which
    # fires the same announced-transition call this used to make here at
    # boot). See drivers/show_engine.py for the whole Setup -> Countdown ->
    # scripted open -> live show -> scripted close flow.

    print("\n==========================================")
    print(" 6-PANEL LED MATRIX & NATIVE DJ ORCHESTRATOR ")
    print("==========================================")
    print(f"  Enttec Port : {ENTTEC_PORT}")
    print(f"  MIDI Status : {midi_status_str}")
    print(f"  Web Remote  : {remote_server.get_remote_url()}")
    print("  [TAB]            : Manual DJ <-> QUIZ mode override")
    print("  DJ MODE          : Vol = native deck volume (Up/Down, joystick Y-axis reversed, hold to repeat)")
    print("                     Track questions pre-fetch in the background (3/track, Haiku) the")
    print("                     instant a deck's track is confidently identified -- no button needed.")
    print("                     Btn1 TAP = Auto-Announcement ON/OFF toggle (default ON; station VO")
    print("                     overlay from audio/announcements/ bridges each Auto-DJ transition).")
    print("                     Btn1 HOLD = status overlay (star=questions ready, $=Price Game ready,")
    print("                     red down-arrow=no questions/AI exhausted); stays up 10s after release")
    print("                     -- a quick re-tap in that window dismisses it without toggling voice.")
    print("                     Left Arrow (dev shim) = Back track, X- axis or Right Arrow = Next track,")
    print("                     X+ axis = Back track (previous-track, mirrors X-).")
    print("                     TRIVIA NIGHT INTRO: X- axis or Right Arrow = end the intro NOW and start")
    print("                     the live show (the intro has no fixed end time -- this is the only way")
    print("                     out of it, so the host's live spoken intro gets as much room as needed).")
    print("                     Btn2 = Normal trivia: instant pull from pre-fetch queue (10s cold-start")
    print("                     timeout -> offline fallback if AI-exhausted, else buzz)")
    print("                     Btn3 TAP = QR popup for the mobile Web Remote (2nd tap or 10s = auto")
    print("                     dismiss). Btn3 HOLD = session AI token-usage overlay (hides on release)")
    print("                     Btn4 = no DJ-mode action (Auto-DJ toggle moved to the web remote --")
    print("                     too easy to bump by accident on the physical pad). Default ON; arms")
    print("                     the transition sequence ~15s before track end -- Auto-Announcement ON")
    print("                     plays a sweeper+VO with the next track ducking/swelling under it; OFF")
    print("                     fires a plain crossfade)")
    print("                     Btn5 = Tempo Tap, Btn6 = FORCE PRICE GAME (always works -- one question")
    print("                     drawn instantly from the local price_game/price_game_bank.csv, no AI)")
    print("                     Btn5+Btn6 HOLD = decade-themed Price Game (AI-fetched, only if armed)")
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
        qr_popup.pump()

        # 2. Render the full 176-channel DMX frame (DJ uplighting themes,
        # game-mode chase, Fixture 1 win/loss/reset)
        lighting_engine.update(time.time())

        # 3. Render Canvas & LED Grid
        update_matrix_canvas()
        render_led_grid()

    # Shutdown -- shared graceful teardown regardless of which of the three
    # triggers ended the loop (pygame QUIT/ALT+F4, the web remote's
    # SHUTDOWN APP button, or the gamepad Btn5+Btn2 5s hold combo -- all
    # three ultimately just flip running/state.shutdown_requested to False/
    # True and let this same block run once, on the main thread).
    if state.shutdown_reason:
        print(f"[SHUTDOWN] Graceful exit -- reason: {state.shutdown_reason}")
    else:
        print("[SHUTDOWN] Graceful exit -- window closed / ALT+F4.")
    save_track_cache()
    deck_orchestrator.dj_engine.stop()
    deck_orchestrator._decode_process.stop()
    dmx.blackout()
    pygame.quit()
    sys.exit()

if __name__ == "__main__":
    main()