"""Bridge to the physical 6-panel LED rig (see the companion ESP32 Arduino
sketch, "Display", which this pairs with). Reads the same six PANELS
regions matrix_canvas.py already renders into matrix_surface every frame
and sends them to the rig over a wired USB-serial link when the ESP32 is
plugged in (preferred -- no WiFi hop, so it avoids the RF-jitter stutter
the UDP path picks up from the venue network), falling back to UDP
broadcast otherwise. Either way it's fire-and-forget, no acks needed: the
firmware handles its own reconnect/timeout/LOADING fallback independently,
so a dropped frame here just gets superseded by the next one.

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

from config import PANELS

_UDP_PORT = 6767
_BROADCAST_ADDR = "255.255.255.255"
_FRAME_MAGIC = 0xA5

# Must match Display.ino's SERIAL_BAUD. Far more headroom than the
# ~7.7KB/s a 386-byte frame at _MAX_SEND_HZ needs -- the bottleneck is the
# ESP32's render speed, never the line rate.
_SERIAL_BAUD = 921600
# USB-UART bridge chips used on ESP32 DevKit V1 boards/clones (Silicon
# Labs CP2102, and the two common CH340/CH9102 variants).
_ESP32_VID_PIDS = {(0x10C4, 0xEA60), (0x1A86, 0x7523), (0x1A86, 0x55D4)}
_SERIAL_RESCAN_INTERVAL_S = 3.0
# Opening the port resets the ESP32 (see the dtr/rts comment below), and
# the firmware takes ~1.5s to boot before its main loop starts draining
# the UART. Streaming into that window just piles up backlog in a buffer
# nothing is reading yet, so the firmware starts life digging out of a
# hole instead of from a clean slate. Fall back to UDP for a couple
# seconds after every fresh connection to let the boot finish first.
_SERIAL_SETTLE_S = 2.0

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
_connect_time = 0.0
_last_send_time = 0.0


def _find_esp32_port():
    for port in serial.tools.list_ports.comports():
        if (port.vid, port.pid) in _ESP32_VID_PIDS:
            return port.device
    return None


def _try_connect_serial():
    global _serial_link, _last_scan_time, _connect_time
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
        print(f"[LED BRIDGE] Connected to display over serial on {device}.")
    except Exception as e:
        print(f"[LED BRIDGE] Could not open {device}: {e}")


def current_transport():
    """For the operator overlay panel (graphics/overlay_panel.py)."""
    if _serial_link is None:
        return "WIFI UDP"
    if time.monotonic() - _connect_time < _SERIAL_SETTLE_S:
        return "SERIAL (booting)"
    return "SERIAL"


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

    red = pygame.surfarray.pixels_red(matrix_surface)  # locks the surface
    try:
        payload = bytearray([_FRAME_MAGIC])
        for panel_id in range(1, 7):
            payload += _panel_bytes(red, PANELS[panel_id])
    finally:
        del red  # releases the surface lock

    checksum = 0
    for b in payload[1:]:
        checksum ^= b
    payload.append(checksum)

    if _serial_link is None and time.monotonic() - _last_scan_time >= _SERIAL_RESCAN_INTERVAL_S:
        _try_connect_serial()

    settling = _serial_link is not None and time.monotonic() - _connect_time < _SERIAL_SETTLE_S
    if _serial_link is not None and not settling:
        try:
            _serial_link.write(bytes(payload))
            return
        except Exception as e:
            print(f"[LED BRIDGE] Serial write failed ({e}); falling back to WiFi UDP.")
            try:
                _serial_link.close()
            except Exception:
                pass
            _serial_link = None

    _sock.sendto(bytes(payload), (_BROADCAST_ADDR, _UDP_PORT))


print("[LED BRIDGE] Ready (USB serial preferred, WiFi UDP fallback).")
