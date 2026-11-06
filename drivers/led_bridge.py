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
import threading
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
# Even send rhythm (2026-10-08): send on every 2nd render tick (40fps loop ->
# 20/s), so panel frames follow the loop's own cadence. The old "at least 50ms
# since the last send" rule turned every slightly-early tick into a 75ms gap
# (scroll hitches), and a fixed 50ms wall-clock grid skipped a slot whenever
# the loop's real tick ran a hair over 25ms (a rhythmic 2px scoot). The
# spacing floor keeps sends from bunching after a stalled frame.
_SEND_EVERY_TICKS = 2
_MIN_SEND_SPACING_S = 0.040

_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

_serial_link = None  # serial.Serial once connected, else None
_last_scan_time = 0.0
_connect_time = 0.0  # for the "(booting)" status label only, not routing
_last_heartbeat_time = 0.0
_last_send_time = 0.0
_ticks_since_send = 0
_frames_sent = 0  # drives frame_time()
_udp_unreachable = False  # logs only on the down/up transition, not every throttled send
_last_ready_state = None  # None=unknown yet, else bool -- for transition-only logging

# Serial sender thread (2026-10-08 CPU survey, same shape as wled_engine.py's
# and dmx_driver.py's): the main loop hands over the finished frame and moves
# on; this thread does the blocking USB write (which releases the GIL), so a
# slow or stuck write can never stall the render loop. One slot, latest frame
# wins -- never a backlog. Wire format, checksum, the _MAX_SEND_HZ cap and the
# heartbeat/readiness logic are all unchanged; only WHERE write() runs moved.
_tx_lock = threading.Lock()  # held around every write AND every close of _serial_link
_tx_cond = threading.Condition()
_tx_pending = None  # bytes of the newest frame not yet written
_tx_error = None  # (exception, link) from the sender, handled on the main thread
_tx_thread = None

# Write-stall tolerance (2026-11-06, confirmed live): a single 50ms USB write
# timeout used to drop the link at once -- yet the board's heartbeat was 0.1s
# old, i.e. it was fine. Dropping it closed the port (which itself blocked
# 5s on the stuck CP2102), freed it, and the rescan then grabbed the
# marquee's port: both boards dark ~10s. Now a write timeout only counts as a
# failure once it has persisted _WRITE_STALL_GIVEUP_S; meanwhile frames keep
# coming and a partial frame is just rejected by Display.ino's checksum.
# Readiness still falls back the normal way if heartbeats stop.
_WRITE_STALL_GIVEUP_S = 3.0
_write_stall_since = None


def _sender_loop():
    global _tx_pending, _tx_error, _write_stall_since
    while True:
        with _tx_cond:
            while _tx_pending is None:
                _tx_cond.wait()
            frame, _tx_pending = _tx_pending, None
        with _tx_lock:
            link = _serial_link
            if link is None or _tx_error is not None:
                continue
            try:
                link.write(frame)
            except serial.SerialTimeoutException as e:
                now = time.monotonic()
                if _write_stall_since is None:
                    _write_stall_since = now
                    print(f"[LED BRIDGE] Write stall on {link.port} -- holding the link. "
                          f"[{serial_ports.recovery_context()}]", flush=True)
                if now - _write_stall_since >= _WRITE_STALL_GIVEUP_S:
                    _write_stall_since = None
                    _tx_error = (e, link)
                continue
            except Exception as e:
                _write_stall_since = None
                _tx_error = (e, link)
                continue
            if _write_stall_since is not None:
                print(f"[LED BRIDGE] Write stall cleared after "
                      f"{time.monotonic() - _write_stall_since:.2f}s.", flush=True)
                _write_stall_since = None


def _queue_serial(frame):
    global _tx_pending, _tx_thread
    if _tx_thread is None:
        _tx_thread = threading.Thread(target=_sender_loop, name="led-bridge-tx", daemon=True)
        _tx_thread.start()
    with _tx_cond:
        _tx_pending = frame
        _tx_cond.notify()


def _log_failure_diagnostics(device):
    """Best-effort snapshot for the once-per-boot serial write timeout
    (2026-10-24): is it load, the port vanishing, or the board silent? Reads
    only; never raises; the kernel log is fetched on a throwaway thread."""
    try:
        import os
        import subprocess
        now = time.monotonic()
        try:
            load = open("/proc/loadavg").read().split()[:3]
        except OSError:
            load = "?"
        try:
            temp = f"{int(open('/sys/class/thermal/thermal_zone0/temp').read()) / 1000:.1f}C"
        except (OSError, ValueError):
            temp = "?"
        hb = "never" if _last_heartbeat_time == 0.0 else f"{now - _last_heartbeat_time:.1f}s ago"
        ports = sorted(p for p in os.listdir("/dev") if p.startswith("ttyUSB")) if os.path.isdir("/dev") else []
        print(f"[LED BRIDGE] DIAG write failure on {device}: load={load} "
              f"since_connect={now - _connect_time:.0f}s heartbeat={hb} "
              f"port_exists={os.path.exists(device)} ttyUSB={ports} "
              f"cpu_temp={temp} threads={threading.active_count()} frames_sent={_frames_sent} "
              f"[{serial_ports.recovery_context()}]", flush=True)

        def _dmesg():
            try:
                time.sleep(1.0)  # let the close()-time USB errors land
                out = subprocess.run(["dmesg", "-T"], capture_output=True, text=True, timeout=3).stdout
                print("[LED BRIDGE] DIAG dmesg tail: " + " | ".join(out.strip().splitlines()[-4:]), flush=True)
            except Exception:
                pass
        threading.Thread(target=_dmesg, daemon=True, name="led-diag").start()
    except Exception:
        pass


def _close_in_background(link, device, reject=False):
    """close() on a wedged CP2102 blocks ~5s in the kernel (dmesg "failed
    set request 0x12 status: -110" = purge timeout) -- that was the 5.2s
    main-loop freeze. Close on a throwaway thread; the port stays held until
    it is really closed so no other module opens it half-way."""
    def work():
        try:
            link.close()
        except Exception:
            pass
        serial_ports.release(device)
        if reject:
            serial_ports.reject(device)
    threading.Thread(target=work, daemon=True, name="led-bridge-close").start()


def _handle_tx_error():
    """Main thread: the same close-and-fall-back-to-UDP handling the inline
    write used to do, for a failure the sender thread reported."""
    global _serial_link, _tx_error
    if _tx_error is None:
        return
    e, link = _tx_error
    with _tx_lock:
        if link is _serial_link and _serial_link is not None:
            print(f"[LED BRIDGE] Serial write failed ({e}); reconnecting.")
            device = _serial_link.port
            _log_failure_diagnostics(device)
            _close_in_background(_serial_link, device)
            _serial_link = None
        _tx_error = None


def _find_esp32_port():
    """VID/PID auto-discovery, skipping whatever any other module holds
    (see drivers/serial_ports.py) -- not just wled_engine.py by name, since
    accent_engine.py's board shares config.ESP32_USB_SERIAL_VID_PIDS too and
    needs the same protection. Also de-prioritizes -- but doesn't
    permanently ban -- every port this module has itself previously
    rejected (_check_never_ready): without that, once wled_engine.py/
    accent_engine.py stop competing for the other candidates (e.g.
    config.WLED_SERIAL_ENABLED is off), nothing forces this scan off "the
    first matching port" anymore, and it would just keep re-picking a
    known-wrong board on every rescan forever instead of ever trying
    another one. Falls back to a rejected candidate only if no
    never-rejected one is available, so a stale or mistaken rejection
    can't permanently strand this module with no port at all (2026-09-21:
    tracks ALL rejected ports, not just the most recent -- see
    serial_ports.py's own history note for why a single-slot version
    ping-ponged between exactly two wrong ports once a third candidate
    existed)."""
    rejected = serial_ports.rejected_ports()
    ports = [port for port in serial.tools.list_ports.comports()
             if (port.vid, port.pid) in config.ESP32_USB_SERIAL_VID_PIDS
             and serial_ports.held_by(port.device) is None]
    # 2026-11-06: our own proven USB socket first; never a socket another
    # module (marquee / outline) has proven is its board.
    for port in ports:
        if serial_ports.identity_of_location(port.location or port.device) == "led_bridge":
            return port.device
    candidates = [port.device for port in ports if not serial_ports.other_owner(port, "led_bridge")]
    non_rejected = [d for d in candidates if d not in rejected]
    if non_rejected:
        return non_rejected[0]
    if candidates:
        # Diagnostic (2026-10-08: stuck re-trying a known-wrong port for
        # minutes while a third board sat unused) -- who holds what.
        all_esp = [p.device for p in serial.tools.list_ports.comports()
                   if (p.vid, p.pid) in config.ESP32_USB_SERIAL_VID_PIDS]
        print(f"[LED BRIDGE] Only rejected ports free -- ESP32 ports {all_esp}, "
              f"held {{{', '.join(f'{d}: {serial_ports.held_by(d)}' for d in all_esp)}}}, "
              f"rejected {sorted(rejected)}.")
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
    with _tx_lock:
        _close_in_background(_serial_link, device, reject=True)
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
            if _last_heartbeat_time == 0.0:
                serial_ports.claim_identity(_serial_link.port, "led_bridge")
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


def _send_due(now):
    return (_ticks_since_send + 1 >= _SEND_EVERY_TICKS
            and now - _last_send_time >= _MIN_SEND_SPACING_S)


def frame_time():
    """Animation clock counted in PANEL FRAMES, in seconds (frames sent x
    1/20 s): consecutive panel frames are always exactly 50ms apart in this
    clock, so motion at 20px/s (scrolling text) moves exactly 1px per frame
    the panels show -- no pause, no skip -- however the real loop timing
    wobbles. Called while drawing a tick that WILL be sent, it already
    returns that frame's time."""
    return (_frames_sent + (1 if _send_due(time.monotonic()) else 0)) * _MIN_SEND_INTERVAL_S


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
    global _serial_link, _last_send_time, _ticks_since_send, _frames_sent

    now = time.monotonic()
    if not _send_due(now):
        _ticks_since_send += 1
        return
    _ticks_since_send = 0
    _frames_sent += 1
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

    global _tx_error, _write_stall_since
    stall = _write_stall_since
    if (stall is not None and _tx_error is None and _serial_link is not None
            and time.monotonic() - stall >= _WRITE_STALL_GIVEUP_S):
        # Stall never cleared and heartbeats stopped too (so no more frames
        # were queued to trip the sender's own give-up): reconnect now.
        _write_stall_since = None
        _tx_error = (serial.SerialTimeoutException(
            f"write stalled {_WRITE_STALL_GIVEUP_S:.0f}s"), _serial_link)
    _handle_tx_error()
    _check_never_ready()
    if _serial_link is None and time.monotonic() - _last_scan_time >= _SERIAL_RESCAN_INTERVAL_S:
        _try_connect_serial()

    _drain_heartbeat()
    if _serial_ready():
        _queue_serial(bytes(payload))  # written by the led-bridge-tx thread
        return

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
