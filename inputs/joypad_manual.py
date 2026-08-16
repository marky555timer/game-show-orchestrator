# inputs/joypad_manual.py
"""Writes JOYPAD_MANUAL.txt -- a plain-text reference of the current gamepad
button map -- to config.APP_ROOT_DIR (next to main.exe in a frozen build, so
it's an outboard file a DJ can open/print, not buried in the bundled
resource dir). Called once from main.py::main() at startup, same place
qr_popup.init() is called.

The table below mirrors main.py's console startup banner. It's a static
description rather than something introspected from inputs/gamepad.py's
process_events() (too fragile to keep in sync automatically) -- except for
the shutdown combo and Space Invaders entry/exit buttons, which are pulled
live from config so this file can't drift out of sync with those after a
remap."""
import os

import config


# Raw pygame JOYBUTTONDOWN index -> the app's semantic "BtnN" name. Used to
# be a plain `index + 1` (Windows/XInput happened to number every physical
# button in order, 0-7 -> Btn1-8), but the Pi's controller doesn't: it has
# more physical buttons than the pad this app was designed against (extra
# triggers/stick-clicks occupy indices in between), pushing Select and
# Start out to raw 9/10 while the face buttons stay at 0-3 and the
# shoulders land on 5/6 -- confirmed live via the console's "[BUTTON] Raw
# Button Pressed" log (2026-08-14). This table is the actual physical
# mapping, not a formula -- update it here (and the matching raw-index
# checks in inputs/gamepad.py's JOYBUTTONDOWN dispatch) if the controller
# ever changes.
_RAW_INDEX_TO_BTN_LABEL = {
    0: "Btn1", 1: "Btn2", 2: "Btn3", 3: "Btn4",
    5: "Btn5", 6: "Btn6", 9: "Btn7", 10: "Btn8",
}


def _btn_label(index):
    """Physical Btn number (1-based) for a pygame JOYBUTTONDOWN index."""
    return _RAW_INDEX_TO_BTN_LABEL.get(index, f"Btn?(raw {index})")


def _combo_label(indices):
    return "+".join(_btn_label(i) for i in indices)


def _build_manual_text():
    shutdown_combo = _combo_label(config.SHUTDOWN_COMBO_BUTTONS)
    si_entry = _combo_label(config.SI_ENTRY_BUTTONS)
    si_exit = " or ".join(_btn_label(i) for i in config.SI_EXIT_BUTTONS)

    return f"""GAME SHOW ORCHESTRATOR -- JOYPAD MANUAL
(auto-generated at startup -- reflects the currently active button map)

DJ MODE
  Btn1 TAP        Auto-Announcement ON/OFF toggle (default ON)
  Btn1 HOLD       Status overlay (star=questions ready, $=Price Game ready,
                  red down-arrow=no questions/AI exhausted)
  Btn2            Normal trivia: instant pull from pre-fetch quiz queue
  Btn3            Toggle SWEAR tag on the last-played announcement clip --
                  hold to see Panel 4 confirm ("*#@!" tagged / "a-ok" cleared)
  Btn4            No action (Auto-DJ toggle lives on the web remote only --
                  too easy to bump by accident here; Auto-DJ default ON)
  Btn5            Tempo Tap
  Btn6            FORCE PRICE GAME (always works -- one question drawn
                  instantly from the local price_game_bank.csv, no AI)
  Btn5+Btn6 HOLD  Decade-themed Price Game (AI-fetched, only if armed)
  Btn7            Uplight Color Cycle
  Btn8            Uplight Theme Cycle
  D-Pad / Y-axis  Volume Up/Down (hold to repeat)
  X- axis         Next track
  X+ axis         Previous track (Back)
  {si_entry} (held together)   SPACE INVADERS mini-game

SPACE INVADERS
  D-pad / X-axis  Move Cannon
  Any button      Fire
  {si_exit}   IMMEDIATE EXIT to DJ mode

QUIZ MODE
  Btn4            Select Answer 1
  Btn2            Select Answer 2
  Btn3            Select Answer 3 (or X+ axis)
  Btn1            Select Answer 4
  Btn5            Grade Selection
  Btn6            Clear Selection
  Btn7            EARLY EXIT (abort round, back to DJ mode)

ADMIN
  {shutdown_combo} (held {config.SHUTDOWN_COMBO_HOLD_SECONDS:.0f}s together)   Graceful app shutdown
"""


def write_manual():
    """Call once from main.py before the game loop starts. A failure here
    (read-only dir, permissions) must never block startup -- log and move
    on, same convention as every other outboard-file write in this app."""
    path = os.path.join(config.APP_ROOT_DIR, "JOYPAD_MANUAL.txt")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(_build_manual_text())
        print(f"[JOYPAD MANUAL] Wrote {path}")
    except Exception as e:
        print(f"[JOYPAD MANUAL ERROR] Could not write {path}: {e}")
