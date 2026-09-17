# drivers/bluetooth_engine.py
"""Bluetooth gamepad reconnect (2026-08-16): the paired controller
occasionally drops out of range (or the Pi's Bluetooth stack just doesn't
notice it come back) and doesn't auto-reconnect on its own, leaving the
rig with no gamepad input until someone re-pairs it by hand. Exposes a
single action for the web remote's "Reconnect Gamepad" button -- re-runs
`bluetoothctl connect <mac>` against every already-paired device (in
practice, just the one gamepad on this rig). Confirmed directly on the
Pi (BlueZ 5.82) that `bluetoothctl devices Paired` / `bluetoothctl
connect <mac>` both work as one-shot commands, no interactive REPL
needed.

pygame already re-scans joysticks whenever the OS reports a device
add/remove (inputs/gamepad.py::init_joysticks(), wired to
JOYDEVICEADDED/JOYDEVICEREMOVED) -- so once BlueZ reports the connection
here, the app picks the controller back up on its own with no restart."""
import re
import subprocess
import threading
import time

import config
from state import state

_MAC_RE = re.compile(r"Device ([0-9A-Fa-f:]{17})")

_LIST_TIMEOUT_S = 5
_CONNECT_TIMEOUT_S = 8

# Guards reconnect_paired_devices_async() (below) against a second physical
# button press piling up an overlapping attempt while one's already running
# (this can take several seconds -- the list call plus one connect attempt
# per paired device, each with its own timeout above).
_connecting = False


def reconnect_paired_devices():
    """Attempts to reconnect every currently-paired Bluetooth device.
    Never raises -- any failure (bluetoothctl missing, no paired devices,
    a connect attempt timing out) just shows up in the returned dict for
    the web remote to display. Reconnecting an already-connected device
    is a harmless no-op (confirmed live), so this doesn't need to check
    connection state first."""
    try:
        listing = subprocess.run(
            ["bluetoothctl", "devices", "Paired"],
            capture_output=True, text=True, timeout=_LIST_TIMEOUT_S,
        ).stdout
    except Exception as e:
        return {"ok": False, "error": str(e), "devices": []}

    devices = []
    for line in listing.splitlines():
        match = _MAC_RE.search(line)
        if not match:
            continue
        mac = match.group(1)
        name = line.split(mac, 1)[1].strip() or mac
        try:
            proc = subprocess.run(
                ["bluetoothctl", "connect", mac],
                capture_output=True, text=True, timeout=_CONNECT_TIMEOUT_S,
            )
            connected = proc.returncode == 0
        except Exception:
            connected = False
        devices.append({"mac": mac, "name": name, "connected": connected})

    return {"ok": True, "devices": devices}


def _reconnect_worker():
    global _connecting
    try:
        result = reconnect_paired_devices()
        success = result["ok"] and any(d["connected"] for d in result["devices"])
        state.gamepad_connect_feedback_text = "SUCCESS" if success else "NO GAMEPAD"
    finally:
        state.gamepad_connect_feedback_until = time.time() + config.GAMEPAD_CONNECT_FEEDBACK_HOLD_SECONDS
        _connecting = False


def reconnect_paired_devices_async():
    """Physical blue-button hook (drivers/simon_engine.py::
    _poll_setup_hardware(), show_phase == "setup"): runs
    reconnect_paired_devices() on a background thread -- the same blocking
    subprocess.run() calls that function makes (up to ~5s list + ~8s per
    paired device) would otherwise stall the single-threaded main loop, the
    same class of problem drivers/wled_engine.py's _resolve_worker()/
    _verify_candidate_mac() already exist to avoid for their own blocking
    calls. state.gamepad_connect_feedback_text/_until (set by the caller to
    "CONNECTING..." immediately on press) get overwritten with SUCCESS/NO
    GAMEPAD once this resolves. No-op re-press while an attempt is already
    in flight."""
    global _connecting
    if _connecting:
        return
    _connecting = True
    threading.Thread(target=_reconnect_worker, daemon=True).start()
