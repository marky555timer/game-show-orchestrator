"""drivers/accent_engine.py
Driver for the third ESP32 (stock WLED, 120-lamp "outlines" accent
strip) -- ON HOLD as of 2026-09-14. See the long comment above
config.ACCENT_SERIAL_BAUD for the full story: the originally-purchased
board (WeGoIOT DOM-WLE-18P) turned out to have no working wired-control
path at all, so it's being replaced with a plain ESP32 DevKit V1
flashed with stock WLED (same construction as the marquee board in
drivers/wled_engine.py). init() is a deliberate no-op until that
replacement board arrives and its actual USB identity is confirmed --
see config's comment for what to check before wiring this back up
(don't trust VID/PID alone; a USB relay on this same rig shares the
CH340 identity WLED clones often use).

Still backs the web remote's Advanced-panel "Next Effect" control (see
web/remote_server.py's /api/accent/* routes) -- available() just always
reports False until this module is rebuilt against the real board.
"""
import json
import threading

import config

try:
    import serial
    _AVAILABLE = True
except ImportError:
    serial = None
    _AVAILABLE = False

_link = None
_lock = threading.Lock()
_effect_index = 0


def available():
    return _link is not None


def init():
    """No-op on hold -- see module docstring. Once the replacement board
    arrives, this needs a VID/PID (or heartbeat-style) scan like
    drivers/wled_engine.py's _find_esp32_port(), not a fixed device
    path -- the last attempt (config.ACCENT_SERIAL_PORT pointing at the
    Pi's hardware UART) was for the now-abandoned board and no longer
    applies."""
    return


def cleanup():
    global _link
    if _link is not None:
        _link.close()
        _link = None


def current_effect():
    return config.ACCENT_TEST_EFFECTS[_effect_index]


def next_effect():
    """Steps to the next effect in config.ACCENT_TEST_EFFECTS and sends
    it to the board. Returns the effect id sent, or None if the link
    isn't available."""
    global _effect_index
    if not available():
        return None
    _effect_index = (_effect_index + 1) % len(config.ACCENT_TEST_EFFECTS)
    fx = config.ACCENT_TEST_EFFECTS[_effect_index]
    _send({"seg": {"fx": fx}})
    return fx


def _send(payload):
    if _link is None:
        return
    with _lock:
        try:
            _link.write((json.dumps(payload) + "\n").encode("utf-8"))
        except (OSError, serial.SerialException) as e:
            print(f"[ACCENT] Write failed: {e}")
