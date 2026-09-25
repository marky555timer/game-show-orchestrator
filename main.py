import os

# Background Input (Feature Update): let joystick button/axis events keep
# flowing even when the orchestrator window lacks OS focus, so a physical
# gamepad press still moves a deck / adjusts volume while another window is
# in front. Must be set before pygame/SDL's joystick subsystem initializes --
# the very first import below (drivers.midi_driver) already triggers
# pygame.midi.init(), so this has to come before even that.
os.environ["SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS"] = "1"

# Sound-effects engine (2026-09-20): imported first, before anything else
# below (including drivers.midi_driver's pygame.midi.init() a few lines
# down) -- its import starts a daemon thread that immediately begins
# looping the startup sound effect on its own separate amplifier, so it's
# already playing before pygame/the DJ mixer/the LED display window exist.
# See drivers/sfx_engine.py's module docstring.
from drivers import sfx_engine  # noqa: F401 (imported for its startup side effect)

import signal
import subprocess
import sys
import time
import traceback
import pygame
from config import MAIN_LOOP_STALL_WARN_SECONDS
from state import state
from drivers.midi_driver import midi_status_str, set_dj_volume
from drivers.dmx_driver import dmx
from drivers import lighting_engine
from drivers import deck_orchestrator
from drivers import power_monitor
from drivers import wled_engine
from drivers import simon_hardware
from drivers import accent_engine
from drivers import relay_engine
from drivers.factoid_engine import save_track_cache
from graphics.matrix_canvas import update_matrix_canvas, render_led_grid
from graphics import secondary_canvas
from inputs.gamepad import process_events
from inputs import joypad_manual
from web import remote_server
from web import qr_popup
from drivers import tunnel_engine

def _handle_sigterm(signum, frame):
    # Fires when the OS itself is shutting down (systemd stopping the
    # session on poweroff -- including the exterior GPIO shutdown button,
    # once wired via dtoverlay=gpio-shutdown; see pi_deploy/README.md) and
    # sends this process SIGTERM. Just flips the same flag the web
    # remote/gamepad-combo/overlay-button triggers use so the loop's
    # regular teardown block (cache save, DMX blackout, driver stop) still
    # runs before the process dies, instead of being killed cold mid-frame.
    # Deliberately does NOT set state.poweroff_after_exit -- the OS is
    # already in the middle of shutting down in this case, so the app just
    # needs to exit cleanly, not request a second poweroff.
    if not state.shutdown_requested:
        state.shutdown_reason = "SIGTERM (OS shutdown)"
        state.shutdown_requested = True

def main():
    signal.signal(signal.SIGTERM, _handle_sigterm)
    clock = pygame.time.Clock()
    running = True
    qr_popup.init()
    secondary_canvas.init()
    joypad_manual.write_manual()
    tunnel_engine.start()
    simon_hardware.init()
    accent_engine.init()
    relay_engine.init()

    # Sync the DJ engine's internal volume trackers (audio/dj_engine.py's
    # _master_volume, midi_driver's own _current_fader_pct) to state.music_volume
    # right away, rather than letting the 600ms intro-handoff tween
    # (show_engine.py::_finish_intro) be the first thing to catch them up --
    # otherwise the first live track can briefly play louder than the
    # configured starting volume while that tween is still ramping down
    # from the stale 100%-default trackers.
    set_dj_volume(state.music_volume)

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
    print(f"  Enttec Port : {dmx.port or 'not found yet (will keep retrying)'}")
    print(f"  Relay Board : {relay_engine.port() or 'not found yet (will keep retrying)'}")
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
        # Loop-stall diagnostic (2026-08-19, config.MAIN_LOOP_STALL_WARN_SECONDS):
        # times each stage below and prints the worst offender the instant
        # one iteration runs noticeably over its 25ms (40fps) budget --
        # see config.py's comment for why (chasing the recurring ~4s
        # LED-heartbeat/DMX-ready flapping). clock.tick() itself is timed
        # too, but only for completeness -- it's deliberate frame-pacing
        # sleep, not app work, so it's never actually the culprit.
        _stage_t0 = time.perf_counter()
        clock.tick(40)
        _stage_t1 = time.perf_counter()

        # 1-5. Per-frame input/game-logic/render work, wrapped in a
        # catch-all (2026-09-20, added after an uncaught IndexError deep in
        # update_matrix_canvas() took the entire show down mid-run with no
        # recovery short of a manual SSH restart): any of these stages can
        # in principle throw on a future bug the same way -- state races
        # between drivers are exactly the kind of thing that slips past
        # testing but shows up live. Logging and skipping the frame beats
        # crashing the whole rig every time; `running` keeps its previous
        # value if process_events() itself is what threw, so the loop (and
        # the ability to quit) isn't stuck either way.
        try:
            # 1. Process Inputs & Game Logic
            running = process_events()
            _stage_t2 = time.perf_counter()
            qr_popup.pump()
            _stage_t3 = time.perf_counter()
            power_monitor.poll(time.time())
            relay_engine.poll()
            _stage_t4 = time.perf_counter()

            # 2. Render the full 176-channel DMX frame (DJ uplighting themes,
            # game-mode chase, Fixture 1 win/loss/reset)
            lighting_engine.update(time.time())
            _stage_t5 = time.perf_counter()

            # 3. Render Canvas & LED Grid
            update_matrix_canvas()
            _stage_t6 = time.perf_counter()
            render_led_grid()
            _stage_t7 = time.perf_counter()

            # 4. Compute + push the current frame to the panel-outline WLED
            # strip (see drivers/wled_engine.py) -- same "fire and forget every
            # frame" shape as dmx.render() above, just over UDP/DDP instead of
            # serial.
            wled_engine.maybe_reresolve()
            wled_engine.update(time.time())
            _stage_t8 = time.perf_counter()

            # 5. Sync the accent (outline) strip's WLED effect/color to the
            # current DJ theme/color (or Price Game white-out) -- see
            # drivers/accent_engine.py::sync_to_show_state(). Cheap no-op
            # unless the target look actually changed since last frame.
            accent_engine.sync_to_show_state()
            _stage_t9 = time.perf_counter()
        except Exception:
            print(f"[MAIN LOOP] Unhandled exception in frame body -- logging and "
                  f"skipping this frame instead of crashing the whole show:\n"
                  f"{traceback.format_exc()}")
            continue

        _stage_total = _stage_t9 - _stage_t0
        if _stage_total >= MAIN_LOOP_STALL_WARN_SECONDS:
            _stages = [
                ("clock.tick", _stage_t1 - _stage_t0),
                ("process_events", _stage_t2 - _stage_t1),
                ("qr_popup.pump", _stage_t3 - _stage_t2),
                ("power_monitor.poll+relay_engine.poll", _stage_t4 - _stage_t3),
                ("lighting_engine.update", _stage_t5 - _stage_t4),
                ("update_matrix_canvas", _stage_t6 - _stage_t5),
                ("render_led_grid", _stage_t7 - _stage_t6),
                ("wled_engine.render", _stage_t8 - _stage_t7),
                ("accent_engine.sync_to_show_state", _stage_t9 - _stage_t8),
            ]
            _worst_name, _worst_s = max(_stages, key=lambda s: s[1])
            print(f"[LOOP STALL] frame took {_stage_total * 1000:.0f}ms (budget 25ms) -- "
                  f"worst stage: {_worst_name} ({_worst_s * 1000:.0f}ms). "
                  f"All: {', '.join(f'{n}={s * 1000:.0f}ms' for n, s in _stages)}")

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
    simon_hardware.cleanup()
    accent_engine.cleanup()
    relay_engine.cleanup()
    sfx_engine.cleanup()
    pygame.quit()

    if state.poweroff_after_exit:
        # Admin-requested full Pi poweroff (web remote "SHUT DOWN PI"), as
        # opposed to the ordinary app-only triggers above. Requires the
        # passwordless sudoers entry for this exact command -- see
        # pi_deploy/README.md. No-op (with a log line, never a crash) on
        # anything that isn't Linux, same defensive pattern as
        # power_monitor.py, since this path is reachable from the Windows
        # dev machine too if the button is ever clicked there.
        if sys.platform.startswith("linux"):
            print("[SHUTDOWN] Admin poweroff requested -- shutting down the Pi now.")
            try:
                subprocess.run(["sudo", "shutdown", "-h", "now"], timeout=10)
            except Exception as e:
                print(f"[SHUTDOWN] Could not power off the Pi ({e}) -- "
                      f"is the passwordless sudoers entry set up? See pi_deploy/README.md.")
        else:
            print("[SHUTDOWN] Admin poweroff requested, but this isn't Linux -- skipping.")
    elif state.reboot_after_exit:
        # Admin-requested Pi reboot (web remote "RESTART PI") -- same shape
        # as poweroff above but `sudo reboot`, and its own separate
        # passwordless sudoers entry (see pi_deploy/README.md).
        if sys.platform.startswith("linux"):
            print("[SHUTDOWN] Admin reboot requested -- rebooting the Pi now.")
            try:
                subprocess.run(["sudo", "reboot"], timeout=10)
            except Exception as e:
                print(f"[SHUTDOWN] Could not reboot the Pi ({e}) -- "
                      f"is the passwordless sudoers entry set up? See pi_deploy/README.md.")
        else:
            print("[SHUTDOWN] Admin reboot requested, but this isn't Linux -- skipping.")
    elif state.restart_app_after_exit:
        # Admin-requested app relaunch (web remote "RESTART APP") -- re-exec
        # this same process in place rather than spawning start.sh fresh:
        # os.execv() keeps the current process's already-inherited env vars
        # (SDL_VIDEODRIVER=wayland etc, exported by pi_deploy/start.sh
        # before its own `exec` launched this process) and open file
        # descriptors (stdout/stderr already redirected to startup.log), so
        # no separate relaunch logic has to duplicate start.sh's setup. Only
        # returns on failure -- falls through to the ordinary sys.exit()
        # below if so, rather than leaving the rig fully down.
        print("[SHUTDOWN] Admin app restart requested -- relaunching now.")
        try:
            os.execv(sys.executable, [sys.executable, "-u", os.path.abspath(__file__)])
        except Exception as e:
            print(f"[SHUTDOWN] Could not relaunch the app ({e}) -- exiting instead.")

    sys.exit()

if __name__ == "__main__":
    main()