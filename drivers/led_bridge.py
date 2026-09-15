"""Bridge to the physical 6-panel LED rig (see the companion ESP32 Arduino
sketch, "Display", which this pairs with). Reads the same six PANELS
regions matrix_canvas.py already renders into matrix_surface every frame
and sends them to the rig over a wired USB-serial link when the ESP32 is
plugged in (preferred -- no WiFi hop, so it avoids the RF-jitter stutter
the UDP path picks up from the venue network), falling back to UDP
broadcast otherwise. Individual frames are fire-and-forget (a dropped one
just gets superseded by the next), but the serial *connection* itself is
gated on a 1-byte heartbeat the firmware writes back once it's actually
past boot and in its main loop (see Display.ino's HEARTBEAT_BYTE) -- an
open file descriptor only proves the USB-serial chip enumerated, not that
anything on the other end is listening, and opening the port resets the
ESP32, so a blind "wait N seconds" guess isn't reliable when both devices
power on together and the board is fighting its own cold boot too.

Frame wire format (must match Display.ino's FRAME_MAGIC/FRAME_SIZE
exactly, identical on both transports): 1 magic byte (0xA5) + 6 panels x
64 bytes + 1 trailing XOR checksum byte over those 384 panel bytes = 386
bytes total. Panel order 1-6 matches the rig's chain-letter order A-F:
1=top-left ... 6=bottom-right, see config.py's PANELS dict. Each panel is
packed 1bpp, row-major, MSB-first, 4 bytes/row (32px wide) x 16 rows.

The checksum matters much more for serial than UDP: a UDP datagram is
always whole-or-absent, but the serial byte stream carries no framing of
its own, so a single byte lost to an RX overflow shifts the firmware's
magic-byte resync onto the wrong offset and everything after it decodes
as noise. The checksum lets the firmware recognize and drop those bad
windows instead of drawing them. Note it's a safety net, not the thing
that made serial usable -- see _MAX_SEND_HZ for that.

Pixel packing is vectorized with numpy/pygame.surfarray rather than
per-pixel get_at() calls -- the first version did ~3000 individual Python
calls per frame, which was slow enough (called synchronously in the main
render loop, up to 40x/sec) to stall the whole orchestrator loop. That
showed up as both stuttering animations (frames not computed on time) and
a flickering display (frames arriving at the rig irregularly instead of a
steady stream).
"""
import socket
import time

import numpy as np
import pygame.surfarray
import serial
import serial.tools.list_ports

import config
from config import PANELS
from drivers import serial_ports

_UDP_PORT = 6767
_BROADCAST_ADDR = "255.255.255.255"
_FRAME_MAGIC = 0xA5

# Must match Display.ino's SERIAL_BAUD. Far more headroom than the
# ~7.7KB/s a 386-byte frame at _MAX_SEND_HZ needs -- the bottleneck is the
# ESP32's render speed, never the line rate.
_SERIAL_BAUD = 921600
_SERIAL_RESCAN_INTERVAL_S = 3.0
# Must match Display.ino's HEARTBEAT_BYTE/HEARTBEAT_INTERVAL_MS. Opening
# the port resets the ESP32 (see the dtr/rts comment below), so a fresh
# connection isn't actually usable until the firmware finishes booting and
# starts writing this back -- an open file descriptor only proves the
# USB-serial chip enumerated, not that anything on the other end is
# listening or has finished its ~1.5s boot.
#
# 2026-08-18 history: first attempt at this used a 1.0s freshness window,
# which caused constant flapping between "SERIAL"/"SERIAL (booting)" on
# live hardware and a severe frame-rate/corruption problem, and was fully
# reverted to a blind post-connect timer (_SERIAL_SETTLE_S, no longer used)
# same day. Root cause found on review: 1.0s is too tight against this
# process's own real-world scheduling jitter (single-threaded pygame loop
# sharing time with audio, DMX, and the FastAPI/uvicorn thread) -- a
# routine stall just over a second, nothing actually wrong with the ESP32,
# was enough to flip "ready" to false, and the resulting mid-stream
# transport flapping (not the timeout logic itself) is what corrupted the
# frame alignment on the wire. _HEARTBEAT_TIMEOUT_S below is now 4.0s --
# still 16x the heartbeat interval (plenty fast to protect against the
# original race, which only needs to survive the ~1.5s boot window) but
# generous enough to absorb realistic jitter instead of chasing it.
_HEARTBEAT_BYTE = 0x5A
_HEARTBEAT_TIMEOUT_S = 4.0

# Separate, much more generous timeout for "this connection has NEVER once
# proven itself alive" -- distinct from _HEARTBEAT_TIMEOUT_S above, which
# re-checks freshness on an already-proven-good link and must tolerate this
# process's own scheduling jitter (see that constant's history note; the
# two must not be conflated or that same flapping bug comes back). This one
# fires at most once per bad connection: since the marquee WLED ESP32
# (2026-09) shares config.ESP32_USB_SERIAL_VID_PIDS with this board,
# _find_esp32_port() can grab the wrong one, and an open-but-wrong serial
# port never raises -- it just never heartbeats. 6s is comfortably longer
# than Display.ino's own ~1.5s boot + 250ms heartbeat interval, so it only
# ever trips on a genuinely wrong (or dead) board, never a normal cold boot.
_NEVER_READY_TIMEOUT_S = 6.0

# Max frames/sec to push over the wire. Measured on this ESP32 (2026-08-10):
# one full frame costs ~18.9ms to render (unpack -> canvas -> panel blit ->
# display buffer), and the LED persistence-of-vision scan interrupt eats
# another ~30% of the CPU on top, so ~20Hz is the real sustainable ceiling.
# At 30Hz+ the firmware renders only ~3 of 32 frames/sec and its serial RX
# buffer fills without recovering; the overflow then corrupts frame framing
# and the display sticks (that's the "works briefly then chokes" failure).
# UDP never showed this because excess datagrams are simply dropped by the
# network stack -- there's no backlog to corrupt. This throttle gives the
# serial path that same "don't send what can't be consumed" property.
# The orchestrator's own render loop stays at 40Hz; only the wire is capped.
_MAX_SEND_HZ = 20.0
_MIN_SEND_INTERVAL_S = 1.0 / _MAX_SEND_HZ

_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

_serial_link = None  # serial.Serial once connected, else None
_last_scan_time = 0.0
_connect_time = 0.0  # for the "(booting)" status label only, not routing
_last_heartbeat_time = 0.0
_last_send_time = 0.0
_udp_unreachable = False  # logs only on the down/up transition, not every throttled send
_last_ready_state = None  # None=unknown yet, else bool -- for transition-only logging


def _find_esp32_port():
    """VID/PID auto-discovery, skipping whatever any other module holds
    (see drivers/serial_ports.py) -- not just wled_engine.py by name, since
    a third CH340-identity device (e.g. a USB relay board) can now share
    config.ESP32_USB_SERIAL_VID_PIDS too and needs the same protection.
    Also de-prioritizes -- but doesn't permanently ban -- a port this
    module has itself previously rejected (_check_never_ready): without
    that, once wled_engine.py stops competing for the other candidate
    (e.g. config.WLED_SERIAL_ENABLED is off), nothing forces this scan off
    "the first matching port" anymore, and it would just keep re-picking
    the same wrong board on every rescan forever instead of ever trying
    the other one. Falls back to a self-rejected candidate only if it's
    the sole match left, so a stale or mistaken rejection can't
    permanently strand this module with no port at all."""
    rejected = serial_ports.rejected_port()
    candidates = [port.device for port in serial.tools.list_ports.comports()
                  if (port.vid, port.pid) in config.ESP32_USB_SERIAL_VID_PIDS
                  and serial_ports.held_by(port.device) is None]
    non_rejected = [d for d in candidates if d != rejected]
    if non_rejected:
        return non_rejected[0]
    return candidates[0] if candidates else None


def _try_connect_serial():
    global _serial_link, _last_scan_time, _connect_time, _last_heartbeat_time
    _last_scan_time = time.monotonic()
    device = _find_esp32_port()
    if device is None:
        return
    try:
        _serial_link = serial.Serial(device, baudrate=_SERIAL_BAUD, timeout=0,
                                      write_timeout=0.05, dsrdtr=False, rtscts=False)
        # pyserial defaults RTS/DTR to *asserted* the moment the port opens,
        # regardless of the dsrdtr/rtscts flow-control flags above -- and on
        # most ESP32 dev boards RTS is wired through the auto-reset circuit
        # straight to EN. Left asserted, that holds the chip in permanent
        # hardware reset for as long as this port stays open (symptom: the
        # panels go fully dark the instant this connects, not just a one-
        # time reboot blip). Releasing both immediately lets EN float back
        # to the board's own pull-up so the sketch actually runs.
        _serial_link.dtr = False
        _serial_link.rts = False
        _connect_time = time.monotonic()
        _last_heartbeat_time = 0.0  # unproven until a heartbeat actually arrives
        serial_ports.hold(device, "led_bridge")
        print(f"[LED BRIDGE] Connected to display over serial on {device}.")
    except Exception as e:
        print(f"[LED BRIDGE] Could not open {device}: {e}")


def _check_never_ready():
    """Closes and releases the current link if it has NEVER once
    heartbeated within _NEVER_READY_TIMEOUT_S of connecting -- see that
    constant's comment. Marks the port rejected so wled_engine.py can
    positively identify it as its own board instead of guessing, and
    pushes _last_scan_time out a full rescan interval (rather than
    retrying instantly) so wled_engine.py has time to notice the rejection
    and vacate whatever port it's wrongly squatting on before this module
    scans again -- without that gap the two can keep re-swapping the same
    wrong ports past each other indefinitely."""
    global _serial_link, _last_scan_time
    if _serial_link is None or _last_heartbeat_time != 0.0:
        return
    if time.monotonic() - _connect_time < _NEVER_READY_TIMEOUT_S:
        return
    device = _serial_link.port
    print(f"[LED BRIDGE] No heartbeat from {device} after {_NEVER_READY_TIMEOUT_S:.0f}s -- "
          f"probably the wrong board (see config.ESP32_USB_SERIAL_VID_PIDS). Releasing it.")
    try:
        _serial_link.close()
    except Exception:
        pass
    serial_ports.release(device)
    serial_ports.reject(device)
    _serial_link = None
    _last_scan_time = time.monotonic()


def _drain_heartbeat():
    """Non-blocking read of whatever's waiting on the RX line, looking for
    the firmware's heartbeat byte (see Display.ino's HEARTBEAT_BYTE). This
    is the only actual proof the ESP32 is booted and alive in its main
    loop -- an open file descriptor alone only proves the USB-serial chip
    enumerated."""
    global _last_heartbeat_time
    if _serial_link is None:
        return
    try:
        waiting = _serial_link.in_waiting
        if waiting and _HEARTBEAT_BYTE in _serial_link.read(waiting):
            _last_heartbeat_time = time.monotonic()
    except Exception:
        pass


def _serial_ready():
    """True only while a heartbeat has actually been seen recently -- see
    _HEARTBEAT_TIMEOUT_S's history note above for why this window is 4.0s,
    not something tighter. Logs on transition only (not every call, which
    would spam at up to 20Hz) so a future incident has real evidence of
    exactly when and how often readiness actually flips, instead of having
    to infer it after the fact from symptoms alone."""
    global _last_ready_state
    ready = (_serial_link is not None
             and time.monotonic() - _last_heartbeat_time < _HEARTBEAT_TIMEOUT_S)
    if ready != _last_ready_state:
        print(f"[LED BRIDGE] Serial ready: {_last_ready_state} -> {ready} "
              f"(last heartbeat {time.monotonic() - _last_heartbeat_time:.2f}s ago).")
        _last_ready_state = ready
    return ready


def current_transport():
    """For the operator overlay panel (graphics/overlay_panel.py)."""
    if _serial_link is None:
        return "WIFI UDP"
    return "SERIAL" if _serial_ready() else "SERIAL (booting)"


def _panel_bytes(red_channel, rect):
    """Slices one 32x16 panel out of `red_channel` (a full-surface
    [x, y]-indexed array from pygame.surfarray.pixels_red()) and packs it
    1bpp, row-major, MSB-first. A pixel counts as lit if its red channel
    is non-zero -- matrix_surface only ever uses BLACK (off), RED_DIM, or
    RED_FULL (both "on" as far as the real single-color, no-per-pixel-
    brightness hardware is concerned; only global brightness is
    controllable there, via the firmware's display.setBrightness())."""
    x0, y0, w, h = rect
    sub = red_channel[x0:x0 + w, y0:y0 + h]  # shape (w, h), indexed [x, y]
    lit = sub.T > 0  # transpose -> shape (h, w) = row-major (y, x)
    # bitorder='big' (numpy's default) packs the first element of each
    # group of 8 as the MSB -- matches the firmware's (byte >> (7-bit))
    # unpacking exactly.
    return np.packbits(lit, axis=-1).tobytes()


def send_frame(matrix_surface):
    """Call once per render tick with the fully-drawn matrix_surface.

    Safe to call at the full 40Hz render rate -- sending is internally
    throttled to what the rig can actually display (see _MAX_SEND_HZ).
    """
    global _serial_link, _last_send_time

    now = time.monotonic()
    if now - _last_send_time < _MIN_SEND_INTERVAL_S:
        return
    _last_send_time = now

    pixels = pygame.surfarray.pixels_red(matrix_surface)  # locks the surface
    red = np.array(pixels)  # detach a copy so the lock releases immediately
    del pixels

    payload = bytearray([_FRAME_MAGIC])
    for panel_id in range(1, 7):
        payload += _panel_bytes(red, PANELS[panel_id])

    checksum = 0
    for b in payload[1:]:
        checksum ^= b
    payload.append(checksum)

    _check_never_ready()
    if _serial_link is None and time.monotonic() - _last_scan_time >= _SERIAL_RESCAN_INTERVAL_S:
        _try_connect_serial()

    _drain_heartbeat()
    if _serial_ready():
        try:
            _serial_link.write(bytes(payload))
            return
        except Exception as e:
            print(f"[LED BRIDGE] Serial write failed ({e}); falling back to WiFi UDP.")
            device = _serial_link.port
            try:
                _serial_link.close()
            except Exception:
                pass
            serial_ports.release(device)
            _serial_link = None

    global _udp_unreachable
    try:
        _sock.sendto(bytes(payload), (_BROADCAST_ADDR, _UDP_PORT))
        if _udp_unreachable:
            _udp_unreachable = False
            print("[LED BRIDGE] WiFi UDP reachable again.")
    except OSError as e:
        # Confirmed 2026-08-14: this crashed the whole app on a real
        # autostart boot -- WiFi hadn't finished associating yet (the
        # interface has no route to the broadcast address until it has),
        # and send_frame() runs on every render tick with nothing above it
        # catching an unguarded socket error. Same "fire-and-forget, a
        # dropped frame just gets superseded by the next one" philosophy
        # as the serial write above -- log the transition once, not every
        # throttled attempt, since this can repeat for several seconds
        # while the network comes up.
        if not _udp_unreachable:
            _udp_unreachable = True
            print(f"[LED BRIDGE] WiFi UDP unreachable ({e}) -- will keep retrying silently.")


print("[LED BRIDGE] Ready (USB serial preferred, WiFi UDP fallback).")
