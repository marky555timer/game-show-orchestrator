"""drivers/relay_engine.py
LCUS-8 USB relay board (8-channel, CH340 USB-serial) -- drives brief relay
pulses for live show events. First user: relay 8 closes for 0.25s whenever
a Simon round is cleared (see drivers/simon_engine.py::press()'s round-clear
block, config.USB_RELAY_POINT_CHANNEL/USB_RELAY_PULSE_SECONDS). Relay 8
drives a physical electromagnetic doorbell -- a one-shot relay timer wired
in front of the doorbell coil itself caps how long the coil is actually
energized, so USB_RELAY_PULSE_SECONDS only needs to reliably trigger that
hardware timer; it is NOT what protects the coil from damage (see
config.py's comment on USB_RELAY_POINT_CHANNEL).

Protocol: the standard LCUS-1/LCUS-2/LCUS-8 4-byte command frame, shared by
this whole family of clone boards, at config.USB_RELAY_SERIAL_BAUD (9600):
    [0xA0, channel(1-8), state(0x00 off / 0x01 on), checksum]
    checksum = (0xA0 + channel + state) & 0xFF
Confirmed live 2026-09-14 (relay 2 audibly clicked on/off on command) --
this board's firmware does NOT echo the command frame back, unlike some
LCUS variants, so identity is established by VID/PID alone (config.
USB_RELAY_VID_PID), same as drivers/dmx_driver.py already does for the
Enttec box: today there's exactly one CH340 device on this rig (both
ESP32 boards are CP2102), so the match is unambiguous. This board's CH340
chip enumerates under the SAME VID/PID some ESP32 clones use (config.
ESP32_USB_SERIAL_VID_PIDS) though, so if a CH340-based ESP32 (e.g. the
still-pending accent board) ever joins the rig, VID/PID alone stops being
proof and this needs a real disambiguation step again. led_bridge.py/
wled_engine.py's own port scans were fixed the same day this module was
added (see their _find_esp32_port() comments) to skip any port drivers/
serial_ports.py shows as held by ANY other module, not just each other by
name, so they can't fight this module for its port once it's claimed.

Discovery/probing runs on a dedicated background thread (_reconnect_loop),
NOT from the per-frame poll() -- confirmed live 2026-09-14: opening the
port and blocking on the probe's read-with-timeout from the main thread
produced a 509ms frame stall every retry interval, the exact "blocking
serial I/O wedges the single-threaded main loop" failure class
wled_engine.py's own comments already document from its own 2026-09-08
incident. poll() (main thread, every frame) only ever touches the
already-open link for a quick write with a short write_timeout, same
convention wled_engine.py's serial sender uses.
"""
import threading
import time

import config
from drivers import serial_ports

try:
    import serial
    import serial.tools.list_ports
    _PYSERIAL_AVAILABLE = True
except ImportError:
    serial = None
    _PYSERIAL_AVAILABLE = False

_HEADER = 0xA0
_RECONNECT_INTERVAL_S = 3.0
_WRITE_TIMEOUT_S = 0.05

_lock = threading.Lock()
_link = None  # serial.Serial once connected+identity-confirmed, else None
_port = None
_pulse_off_at = {}  # {channel: time.monotonic() deadline to switch it back off}
_reconnect_thread = None
_stop_requested = False


def _frame(channel, state):
    checksum = (_HEADER + channel + state) & 0xFF
    return bytes([_HEADER, channel, state, checksum])


def available():
    with _lock:
        return _link is not None


def port():
    with _lock:
        return _port


def _find_candidate_ports():
    return [p.device for p in serial.tools.list_ports.comports()
            if (p.vid, p.pid) == config.USB_RELAY_VID_PID
            and serial_ports.held_by(p.device) is None]


def _probe(device):
    """Opens `device` and sends a harmless relay-1-OFF command (idempotent
    if it's already off) to prove the link is actually writable. Runs
    entirely on the background reconnect thread -- the open below can
    block for real, which is fine here but would stall the main render
    loop if ever called from poll(). Returns an open serial.Serial on
    success; closes and returns None on any error, leaving led_bridge.py/
    wled_engine.py free to try the port themselves (moot in practice --
    see module docstring on why VID/PID alone is trusted here)."""
    link = None
    try:
        link = serial.Serial(device, baudrate=config.USB_RELAY_SERIAL_BAUD,
                              timeout=1.0)
        link.write(_frame(1, 0x00))
    except Exception as e:
        print(f"[RELAY] Could not open {device}: {e}")
        if link is not None:
            try:
                link.close()
            except Exception:
                pass
        return None
    return link


def _reconnect_loop():
    """Background thread body: while not connected, rescans/probes every
    _RECONNECT_INTERVAL_S. All the potentially-blocking serial work (open
    + probe write) happens here, off the main thread -- only the final
    handoff into _link/_port is done under _lock, so poll()/pulse() on the
    main thread never wait on this thread doing real I/O."""
    global _link, _port
    while not _stop_requested:
        with _lock:
            connected = _link is not None
        if not connected:
            for device in _find_candidate_ports():
                link = _probe(device)
                if link is not None:
                    with _lock:
                        _link = link
                        _port = device
                    serial_ports.hold(device, "relay_engine")
                    print(f"[RELAY] Confirmed LCUS-8 relay board on {device}.")
                    break
        time.sleep(_RECONNECT_INTERVAL_S)


def init():
    """Called once from main.py's startup (alongside simon_hardware.init()/
    accent_engine.init()) -- starts the background reconnect thread, which
    claims this board's port before the per-frame loop's first led_bridge.py/
    wled_engine.py port scan runs in most cases (doesn't need to eliminate
    the race; both sides self-heal within a few seconds otherwise, same as
    any other port race on this rig)."""
    global _reconnect_thread
    if not _PYSERIAL_AVAILABLE or _reconnect_thread is not None:
        return
    _reconnect_thread = threading.Thread(target=_reconnect_loop, daemon=True)
    _reconnect_thread.start()


def _drop_link():
    """Must be called with _lock held."""
    global _link, _port
    if _link is not None:
        try:
            _link.close()
        except Exception:
            pass
    if _port is not None:
        serial_ports.release(_port)
    _link = None
    _port = None


def _send(channel, state):
    """Main-thread-safe: only ever writes to an already-open link (a quick
    op bounded by _WRITE_TIMEOUT_S), never opens/probes one -- see module
    docstring for why that split matters."""
    with _lock:
        if _link is None:
            return
        try:
            _link.write_timeout = _WRITE_TIMEOUT_S
            _link.write(_frame(channel, state))
        except Exception as e:
            print(f"[RELAY] Write failed on {_port}: {e}")
            _drop_link()


def pulse(channel, duration=None):
    """One-shot: energizes `channel` immediately, then queues it back off
    after `duration` seconds -- poll() (called every frame from main.py)
    does the actual timing check. Uses time.monotonic() so a wall-clock
    adjustment can't strand a pulse on, same convention drivers/
    dmx_driver.py's pulse_channel() already uses."""
    if duration is None:
        duration = config.USB_RELAY_PULSE_SECONDS
    _send(channel, 0x01)
    _pulse_off_at[channel] = time.monotonic() + duration


def pulse_point_relay():
    """Convenience wrapper for the "player scores a point" pulse -- see
    config.USB_RELAY_POINT_CHANNEL/USB_RELAY_PULSE_SECONDS. Currently
    called from drivers/simon_engine.py on a Simon round clear."""
    pulse(config.USB_RELAY_POINT_CHANNEL, config.USB_RELAY_PULSE_SECONDS)


def poll():
    """Per-frame poll (main.py's loop): switches off any channel whose
    pulse() duration has elapsed. Cheap no-op with nothing pending --
    does NOT touch discovery/reconnect, that's the background thread's job
    (see module docstring)."""
    if not _pulse_off_at:
        return
    now = time.monotonic()
    done = [ch for ch, off_at in _pulse_off_at.items() if now >= off_at]
    for ch in done:
        _send(ch, 0x00)
        del _pulse_off_at[ch]


def cleanup():
    """Called from main.py's shutdown path -- stops the reconnect thread
    and releases the port (mirrors accent_engine.cleanup()). Doesn't
    explicitly force channels off first: a mid-pulse app exit is already a
    rare 0.25s window, and the OS closing the serial handle doesn't change
    the relay's last commanded state either way."""
    global _stop_requested
    _stop_requested = True
    with _lock:
        _drop_link()
