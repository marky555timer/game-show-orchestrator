"""drivers/accent_serial_test.py
Bring-up/test driver for the third ESP32 (stock WLED, 120-lamp "outlines"
accent strip) wired point-to-point to the Pi's hardware UART -- see the
wiring note above config.ACCENT_SERIAL_PORT. Separate board from the
marquee/outline ESP32 (config.WLED_HOST/MARQUEE_*, driven by drivers/
wled_engine.py over USB-serial + DDP for raw pixel chase effects); this
one is controlled purely by WLED's JSON API sent over a dedicated UART
(no USB, no WiFi client), since only preset/effect switching is needed
here, not frame-accurate pixel timing.

Not part of any real show effect yet -- this module only cycles a
handful of WLED's built-in effects every config.ACCENT_EFFECT_CYCLE_SECONDS
so the physical wiring (and WLED's own Sync Setup -> Serial config on
that board) can be proven working, same bring-up role as drivers/
simon_hardware.py.

No-op everywhere (with a one-line log at import) if pyserial isn't
available, so this stays safe to import from the Windows dev machine.
Run directly (`python3 drivers/accent_serial_test.py`) for a standalone
visual test -- it is NOT yet wired into main.py's startup.
"""
import json
import threading
import time

import config

try:
    import serial
    _AVAILABLE = True
except ImportError:
    serial = None
    _AVAILABLE = False

_link = None
_thread = None
_running = False

if not _AVAILABLE:
    print("[ACCENT TEST] pyserial not available on this host -- accent-strip test will report unavailable.")


def available():
    return _AVAILABLE and _link is not None


def start():
    """Opens the UART and starts the effect-cycling thread. Safe to call
    on a host without pyserial (no-ops), or if the port is already open
    (no-ops)."""
    global _link, _thread, _running
    if not _AVAILABLE or _running:
        return
    try:
        _link = serial.Serial(config.ACCENT_SERIAL_PORT, baudrate=config.ACCENT_SERIAL_BAUD, timeout=1)
    except (OSError, serial.SerialException) as e:
        print(f"[ACCENT TEST] Could not open {config.ACCENT_SERIAL_PORT}: {e}")
        _link = None
        return
    _running = True
    _thread = threading.Thread(target=_cycle_loop, daemon=True)
    _thread.start()
    print(f"[ACCENT TEST] Cycling {len(config.ACCENT_TEST_EFFECTS)} effects every "
          f"{config.ACCENT_EFFECT_CYCLE_SECONDS}s on {config.ACCENT_SERIAL_PORT}.")


def stop():
    """Stops the cycling thread and releases the port."""
    global _running, _link
    _running = False
    if _link is not None:
        _link.close()
        _link = None


def _cycle_loop():
    i = 0
    while _running:
        fx = config.ACCENT_TEST_EFFECTS[i % len(config.ACCENT_TEST_EFFECTS)]
        _send({"seg": {"fx": fx}})
        print(f"[ACCENT TEST] -> effect {fx}")
        i += 1
        time.sleep(config.ACCENT_EFFECT_CYCLE_SECONDS)


def _send(payload):
    if _link is None:
        return
    try:
        _link.write((json.dumps(payload) + "\n").encode("utf-8"))
    except (OSError, serial.SerialException) as e:
        print(f"[ACCENT TEST] Write failed: {e}")


if __name__ == "__main__":
    start()
    if not available():
        raise SystemExit(1)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop()
