"""drivers/wled_engine.py
Bridge to the marquee lights -- the WS2811 "bullet bulb" LEDs outlining the
panel edges -- driven by a separate ESP32 running third-party WLED firmware
(see config.py's MARQUEE section for the hardware background/wiring
notes). Now that this board lives permanently on the rig next to the Pi
(2026-09) rather than moving between networks, prefers a direct USB-serial
link over WLED's native Adalight/"LEDstream" realtime-input protocol --
same "wired preferred, wireless fallback" shape drivers/led_bridge.py uses
for the matrix ESP32 -- and falls back to DDP (Distributed Display
Protocol) over WiFi/UDP when serial isn't connected/ready, which is also
what keeps the dev-laptop-over-WiFi workflow working unchanged.

Unlike led_bridge.py's link to Display.ino (firmware this project owns and
can add a custom heartbeat byte to), WLED is stock third-party firmware --
there's no proof-of-life byte to read back, so "ready" here just means
"port open and past WLED's own boot window" (_SERIAL_BOOT_SETTLE_S), not
a continuously-reverified heartbeat. Adalight framing (3 magic bytes + LED
count-minus-1 hi/lo + checksum, then raw RGB triplets) and the 115200 baud
rate were verified byte-for-byte against WLED's own wled_serial.cpp source
and confirmed correct -- the protocol implementation here isn't the
problem.

config.WLED_SERIAL_ENABLED was False 2026-09-02 through the evening while
the actual board on this rig was flashed with WLED's "esp32dev_debug"
build, which printed verbose diagnostics out the same Serial/USB line
Adalight needs clean -- those interleaved with our frames, the checksum
never validated, and WLED just sat on its last locally-set color/effect
(confirmed live via its own /json/info: "live" stayed continuously false,
not intermittently, while holding a static amber). Board has since been
reflashed with a standard (non-debug) WLED 0.15.2 release build via its
own web OTA updater (confirmed via /json/info: release "ESP32", not
"ESP32_DEBUG"), and config.WLED_SERIAL_ENABLED is back to True.

DDP packet format (10-byte header + raw RGB payload), per the DDP spec:
  byte 0   flags: 0x41 = version 1 + PUSH (render immediately on receipt)
  byte 1   sequence number, 0-15, purely diagnostic -- WLED doesn't require it
  byte 2   data type: 0x01 (RGB, 8-bit/channel)
  byte 3   output/device ID: 1 (WLED's default realtime output)
  bytes 4-7  channel offset into the target's pixel buffer, big-endian (0 --
             this app always addresses the whole strip from pixel 0)
  bytes 8-9  payload length in bytes, big-endian (3 x LED count)
  bytes 10+  RGB triplets, one per pixel, in strip order

Like drivers/dmx_driver.py's render(), DDP sends are fire-and-forget UDP --
a dropped frame just gets superseded by the next one, no retry/ack needed
for a realtime stream. The serial write below gets the same tolerance for
the same reason.
"""
import colorsys
import json
import math
import random
import socket
import threading
import time

import serial
import serial.tools.list_ports

import config
from drivers import color_utils, live_round_engine, oz_story, serial_ports, transition_shapes
from state import state

_DDP_FLAGS_VERSION1_PUSH = 0x41
_DDP_TYPE_RGB8 = 0x01
_DDP_OUTPUT_ID = 1
_RESOLVE_RETRY_INTERVAL_S = 5.0

_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
_pixels = bytearray(config.MARQUEE_TOTAL_LEDS * 3)
_ip = None  # resolved from config.WLED_HOST once, re-resolved on failure
_last_resolve_attempt = 0.0

# DDP send hand-off to _ddp_sender_loop's dedicated thread -- see that
# function's docstring for why sendto() can't run on the shared main
# thread here.
_ddp_lock = threading.Lock()
_ddp_send_ready = threading.Event()
_ddp_pending = None  # (packet_bytes, ip) tuple, or None once consumed
_resolving = False  # guards against piling up overlapping resolver threads
_sequence = 0

# WLED's fixed baud rate for its Adalight/"LEDstream" serial realtime input
# -- not configurable from this side, unlike led_bridge.py's _SERIAL_BAUD
# (that one matches a rate this project's own Display.ino sketch chose).
_SERIAL_BAUD = 115200
_SERIAL_RESCAN_INTERVAL_S = 3.0
# ESP32 boot time to wait out after DTR/RTS release (see the dtr/rts
# comment in _try_connect_serial below -- opening the port resets the
# chip) before trusting the link enough to start sending frames. There's
# no heartbeat byte to wait for instead, unlike led_bridge.py's link to
# Display.ino -- WLED is stock firmware we don't control the source of.
_SERIAL_BOOT_SETTLE_S = 2.0
# Adalight header: 3 magic bytes ("Ada") + (LED count - 1) as big-endian
# hi/lo + checksum (hi ^ lo ^ 0x55). Built once at import since
# MARQUEE_TOTAL_LEDS is fixed for the process lifetime.
_led_count_minus_1 = config.MARQUEE_TOTAL_LEDS - 1
_ada_hi = (_led_count_minus_1 >> 8) & 0xFF
_ada_lo = _led_count_minus_1 & 0xFF
_ADA_HEADER = bytes([0x41, 0x64, 0x61, _ada_hi, _ada_lo, _ada_hi ^ _ada_lo ^ 0x55])
# Bandwidth cap for the serial path: (3 x LED count + 6)-byte frames at
# _SERIAL_BAUD (115200 = ~11.5KB/s) top out around ~36fps on wire time
# alone before WLED even has to process anything -- cap well under that,
# same "don't send what can't be consumed" reasoning as led_bridge.py's
# own _MAX_SEND_HZ, so a backlog can't build up on a link with no
# backpressure of its own.
_MAX_SERIAL_SEND_HZ = 30.0
_MIN_SERIAL_SEND_INTERVAL_S = 1.0 / _MAX_SERIAL_SEND_HZ

_serial_link = None  # serial.Serial once connected, else None
_last_serial_scan_time = 0.0
_serial_connect_time = 0.0
_last_serial_send_time = 0.0

# MAC-based candidate verification (2026-09-15) -- see _find_esp32_port()'s
# docstring. Runs off the main thread since it needs a blocking serial
# round-trip; _mac_verify_result is only ever written by that background
# thread and read here, a plain dict is fine under the GIL for this.
_mac_verify_thread = None
_mac_verify_result = {}  # device path -> True (confirmed marquee) / False (confirmed not)
_mac_verify_tries = {}  # device path -> inconclusive probes so far (least-tried probed first)
# Display.ino's heartbeat byte (drivers/led_bridge.py _HEARTBEAT_BYTE): a port
# that answers with nothing but these is the matrix display board, not WLED.
_DISPLAY_HEARTBEAT_BYTE = 0x5A

# Serial write hand-off to _serial_sender_loop's dedicated thread -- same
# shape/reasoning as _ddp_lock/_ddp_send_ready/_ddp_pending above. See that
# thread's docstring; this one exists for the exact same reason, just found
# later (2026-09-08, confirmed live: two full-minute main-loop freezes,
# [LOOP STALL] frame took 65697ms/66706ms, both "worst stage:
# wled_engine.render"). write_timeout on the Serial object below is a
# userspace guard only -- once this rig's ESP32 actually wedges, the
# underlying write() syscall can block in the kernel USB-serial driver far
# past that configured timeout, freezing the single-threaded main loop
# (matrix, DMX, gamepad input -- audio is a separate process so it's the
# one thing that stayed unaffected) until the board comes back or the app
# is restarted.
_serial_lock = threading.Lock()
_serial_send_ready = threading.Event()
_serial_pending = None  # (serial.Serial instance, frame_bytes) tuple, or None once consumed


def _find_esp32_port():
    """VID/PID auto-discovery among ports not already held by another
    module (drivers/serial_ports.py) -- since drivers/accent_engine.py's
    board now shares this same VID/PID pool too, a candidate is only
    ever returned once positively verified as THIS board specifically,
    by asking it for its WLED info and checking config.MARQUEE_WLED_MAC
    (see _verify_candidate_mac below), the same approach
    drivers/accent_engine.py uses for its own board.

    Replaced 2026-09-15: the previous version just took the first
    unclaimed candidate, de-prioritizing whatever led_bridge.py had most
    recently rejected -- reasonable when exactly two boards could ever
    match, but with a third (accent) board also matching, that guess
    could land on the real matrix board with nothing to ever catch the
    mistake (_reconcile_with_led_bridge, which used to catch exactly
    this, itself only worked for the two-board case and is disabled --
    see its own docstring). Confirmed live: this exact failure took the
    physical LED matrix down for several minutes before being caught.

    Verification happens off the main thread since it's a blocking
    serial round-trip; this function only ever returns a device once
    its background check lands on True, kicking off a check for one
    not-yet-verified candidate per call otherwise (throttled by the
    caller's own _SERIAL_RESCAN_INTERVAL_S, so this is at most one new
    background probe every few seconds, not a tight loop)."""
    # Least-probed first (2026-10-08): an inconclusive port used to be
    # re-probed back-to-back forever because it came first in comports()
    # order -- live deadlock: this kept the display board's port "verifying"
    # nearly nonstop, so led_bridge.py never got it and kept grabbing the
    # marquee's port instead, and this never reached the marquee's port.
    ports = sorted(serial.tools.list_ports.comports(),
                   key=lambda p: _mac_verify_tries.get(p.device, 0))
    for port in ports:
        if (port.vid, port.pid) not in config.ESP32_USB_SERIAL_VID_PIDS:
            continue
        if serial_ports.held_by(port.device) is not None:
            continue
        if serial_ports.other_owner(port, "wled_engine"):
            continue  # proven to be another module's board (2026-11-06)
        result = _mac_verify_result.get(port.device)
        if result is True:
            return port.device
        if result is False:
            continue
        if _mac_verify_thread is None or not _mac_verify_thread.is_alive():
            _start_mac_verify(port.device)
        break  # only ever have one unverified candidate in flight at a time
    return None


def _start_mac_verify(device):
    global _mac_verify_thread
    # Placeholder hold so led_bridge.py/accent_engine.py's own scans see
    # this port as taken for the whole probe window, not just after the
    # fact -- same fix drivers/accent_engine.py needed for the same
    # reason (see that module's history).
    serial_ports.hold(device, "wled_engine_verifying")
    _mac_verify_thread = threading.Thread(target=_verify_candidate_mac, args=(device,), daemon=True)
    _mac_verify_thread.start()


def _verify_candidate_mac(device):
    """Runs off the main thread -- opens `device` briefly, asks WLED for
    its info ({"v":true}), and records in _mac_verify_result whether its
    MAC matches config.MARQUEE_WLED_MAC. Always releases the placeholder
    hold itself, whether matched or not; _try_connect_serial() re-holds
    properly under "wled_engine" once _find_esp32_port() sees a True
    result and returns this device.

    2026-09-21 fix: a probe that raises (serial hiccup, or a truncated/
    malformed read racing another module's own probe of the same port --
    confirmed live, "Unterminated string" JSON errors during a cold-boot
    port scramble) used to fall through to `_mac_verify_result[device] =
    False`, PERMANENTLY ruling the port out for the rest of the session --
    even if it's genuinely this board and the failure was just bad timing.
    Only a probe that actually completes and parses a reply now records a
    result at all; anything inconclusive leaves the port unrecorded so a
    later rescan gets a fresh attempt instead of a false-permanent
    rejection."""
    matched = False
    probed_ok = False
    try:
        probe = serial.Serial(device, baudrate=_SERIAL_BAUD, timeout=0.5, dsrdtr=False, rtscts=False)
        probe.dtr = False
        probe.rts = False
        time.sleep(0.3)
        probe.reset_input_buffer()
        probe.write(b'{"v":true}\n')
        time.sleep(0.5)
        data = probe.read(4096)
        probe.close()
        if data and all(b == _DISPLAY_HEARTBEAT_BYTE for b in data):
            # Only the matrix display's heartbeat came back: definitely not
            # WLED (2026-10-08) -- record it rather than re-probe forever.
            probed_ok = True
            print(f"[WLED] {device} is the matrix display board (heartbeat only) -- not the marquee.")
        text = "" if probed_ok else data.decode("utf-8", errors="replace").strip()
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("{"):
                mac = json.loads(line).get("info", {}).get("mac")
                matched = (mac == config.MARQUEE_WLED_MAC)
                probed_ok = True
                break
    except Exception as e:
        print(f"[WLED] MAC verify probe of {device} failed: {e}")
    serial_ports.release(device)
    if probed_ok:
        _mac_verify_result[device] = matched
    else:
        _mac_verify_result.pop(device, None)
        _mac_verify_tries[device] = _mac_verify_tries.get(device, 0) + 1


def _try_connect_serial():
    global _serial_link, _last_serial_scan_time, _serial_connect_time
    _last_serial_scan_time = time.monotonic()
    if _usb_resetting:
        return
    device = _find_esp32_port()
    if device is None:
        return
    try:
        _serial_link = serial.Serial(device, baudrate=_SERIAL_BAUD, timeout=0,
                                      write_timeout=0.05, dsrdtr=False, rtscts=False)
        # Same RTS/DTR release led_bridge.py does -- pyserial asserts both
        # the moment the port opens, and on most ESP32 dev boards RTS is
        # wired through the auto-reset circuit straight to EN, which would
        # otherwise hold the chip in permanent reset for as long as this
        # port stays open.
        _serial_link.dtr = False
        _serial_link.rts = False
        # Also doubles as the proactive-reconnect clock's reference point
        # (_PROACTIVE_RECONNECT_INTERVAL_S below) -- each fresh connection
        # gets its own full interval before that timer fires again.
        _serial_connect_time = time.monotonic()
        serial_ports.hold(device, "wled_engine")
        serial_ports.claim_identity(device, "wled_engine")
        print(f"[WLED] Connected over serial on {device} (confirmed by MAC).")
    except Exception as e:
        print(f"[WLED] Could not open {device}: {e}")
        if _mac_verify_result.get(device) is True and getattr(config, "WLED_HEARTBEAT_RESET", False):
            _usb_reenumerate(device)  # the confirmed marquee port won't open -> its USB chip is stuck
        _mac_verify_result.pop(device, None)  # let a future scan re-verify rather than never trying this port again


def _serial_ready():
    """No heartbeat to check (see module docstring) -- just "port open and
    past WLED's own boot window"."""
    return (_serial_link is not None
            and time.monotonic() - _serial_connect_time >= _SERIAL_BOOT_SETTLE_S)


def current_transport():
    """For the operator overlay panel (graphics/overlay_panel.py), same
    shape as led_bridge.current_transport()."""
    if _serial_link is None:
        return "WIFI DDP" if _ip else "DISCONNECTED"
    return "SERIAL" if _serial_ready() else "SERIAL (booting)"


def _resolve_worker():
    """Runs off the main thread -- see maybe_reresolve() for why. A stock
    socket.gethostbyname() lookup has no timeout of its own, so when this
    board isn't currently reachable (WiFi off, moved networks, mDNS not
    routed) the call can block for several seconds before failing."""
    global _ip, _resolving
    try:
        ip = socket.gethostbyname(config.WLED_HOST)
        _ip = ip
        print(f"[WLED] Resolved {config.WLED_HOST} -> {ip}")
    except Exception as e:
        print(f"[WLED] Could not resolve {config.WLED_HOST}: {e}")
    finally:
        _resolving = False


def _ddp_sender_loop():
    """Runs forever on its own daemon thread, started once at import.

    2026-09-02: confirmed live (startup.log's [LOOP STALL] instrumentation)
    that render()'s old inline `_sock.sendto(...)` could block the shared
    single-threaded main loop for 0.5-4+ seconds at a time -- the same
    "stops moving, then scrambles to catch up" symptom the chase patterns
    showed, since their position math is wall-clock-based (now = time.time())
    and a multi-second gap in when frames actually get sent shows up as a
    visible jump once the loop unblocks, not a smooth pause. Root cause is
    almost certainly the kernel blocking sendto() on ARP resolution when
    this board's WiFi radio doesn't answer promptly -- same class of bug
    _resolve_worker() above already exists to guard gethostbyname() against,
    just one layer lower (the send itself, not the DNS lookup before it).
    UDP sends are already fire-and-forget by design (a dropped frame just
    gets superseded by the next one), so moving the actual socket call onto
    a thread that's free to block as long as it needs to -- without ever
    holding up matrix rendering, DMX, or anything else sharing the main
    loop -- costs nothing behaviorally and fixes the stall at its source."""
    global _ip, _ddp_pending
    while True:
        _ddp_send_ready.wait()
        with _ddp_lock:
            pending = _ddp_pending
            _ddp_pending = None
            _ddp_send_ready.clear()
        if pending is None:
            continue
        packet, ip = pending
        try:
            _sock.sendto(packet, (ip, config.WLED_DDP_PORT))
        except Exception as e:
            # Same tolerance as before this moved to a thread: assume the
            # resolved IP went stale and let maybe_reresolve() pick a fresh
            # one back up rather than retrying this exact address forever.
            print(f"[WLED] Send failed ({e}) -- will retry resolution.")
            _ip = None


threading.Thread(target=_ddp_sender_loop, daemon=True, name="wled-ddp-sender").start()


def _fail_serial_link(link, error):
    """Shared teardown for a serial link that just failed a write (or hit
    its proactive periodic reconnect below), called from
    _serial_sender_loop's own thread. Guards against clobbering a
    *different*, newer connection: _try_connect_serial() runs on the main
    thread and could in principle reconnect while a stale write from the
    old link is still unwinding here (e.g. it was blocked for a while
    before finally raising) -- only clear the shared _serial_link if it's
    still pointing at the exact object that just failed.

    Closing and re-opening the port (which _try_connect_serial() does on
    its own next pass, same as any other reconnect) is what actually
    recovers a wedged board here -- opening a pyserial port on this
    hardware resets the ESP32 via the RTS-wired auto-reset circuit (see
    _try_connect_serial()'s own dtr/rts comment), the same effect a manual
    power cycle has, just triggered from code instead of a hand on the
    plug."""
    global _serial_link
    print(f"[WLED] Serial link failed ({error}); falling back to WiFi DDP. "
          f"[{serial_ports.recovery_context()}]")
    device = link.port
    try:
        link.close()
    except Exception:
        pass
    serial_ports.release(device)
    if _serial_link is link:
        _serial_link = None


# Proactive periodic reconnect (2026-09-20, confirmed live: this rig's
# marquee ESP32 crashed/reset entirely independently of the Pi -- its own
# WLED firmware came back up sitting on its default boot-preset color
# ("live" stuck false, exactly the symptom this module's own docstring
# already describes from the 2026-09-02 debug-firmware incident) while
# this module kept blindly streaming valid, changing Adalight frames into
# a UART nothing was reading correctly anymore. link.write() succeeding
# only proves the OS handed bytes to the port -- it says nothing about
# whether the ESP32 on the other end is actually parsing them, and unlike
# led_bridge.py's link to our own Display.ino firmware, there was no
# proof-of-life check at all here once the initial connection was made.
#
# An earlier version of this fix tried to actively verify liveness with
# WLED's own '{"v":true}' JSON query (the same one _verify_candidate_mac()
# already sends at connect time), repeated periodically. Live-tested and
# reverted the same night: it reported "not live" 100% of the time, even
# seconds after a fresh reconnect with a confirmed-healthy board (verified
# by eye) still streaming continuously -- consistent with WLED's serial
# handler simply not answering JSON commands while it's busy consuming the
# realtime Adalight stream, which would make that technique unable to ever
# see a healthy reply regardless of actual board state. Chasing that
# further would mean guessing at WLED firmware internals with no way to
# verify against the real hardware except by repeatedly disrupting a live
# rig, so this takes a dumber but verifiably-safe approach instead: since
# a full reconnect is already known to reset the chip (the same effect a
# manual power cycle had), just do that on a fixed timer regardless of any
# health signal -- no detection to get wrong, and a real wedge can never
# persist longer than one interval before self-healing. Costs a brief
# (~_SERIAL_BOOT_SETTLE_S) visible reset blip every interval even when
# nothing was wrong, which is the accepted tradeoff (2026-09-20, operator
# choice) for guaranteed bounded recovery during unattended operation.
#
# Interval dropped from an initial 10 minutes to 90 seconds the same night
# (2026-09-20), confirmed live: the board wedged again on its own only
# ~7 minutes into a completely fresh process, well before the original
# 10-minute timer would ever have fired -- the real-world wedge frequency
# is clearly higher than first assumed, so bounding recovery to 10 minutes
# wasn't actually satisfying "must never freeze" in practice. 90s keeps any
# future wedge to a brief, easy-to-miss blip instead of a multi-minute
# outage someone has to notice and report.
# DISABLED ON PURPOSE (2026-09-21 finding, operator decision): a 4h17m run
# with this timer off showed zero organic wedges -- the periodic reset blip
# was itself the visible problem. Recovery is manual instead: the physical
# recovery button (drivers/recovery_button.py) calls force_reconnect()
# below. Left as a huge value rather than removed so the safety-net code
# path stays available; set to e.g. 1800.0 to re-enable a gentle periodic
# fallback.
_PROACTIVE_RECONNECT_INTERVAL_S = 999999.0

# Heartbeat (2026-10-03). The board froze mid-medley holding its last frame
# while the Pi kept streaming -- nothing here could tell. WLED's serial
# handler (wled_serial.cpp, AdaState::Header_A) answers a bare 'v' byte with
# "WLED <version>\r\n", and Header_A is exactly the state it returns to after
# every Adalight frame -- so unlike the '{"v":true}' JSON probe that was
# abandoned above, a 'v' written straight after a frame is answered even
# while realtime streaming is running. No reply for _HEARTBEAT_TIMEOUT_S =
# the firmware's loop has stopped; config.WLED_HEARTBEAT_RESET decides
# whether that only logs or also resets the board (port reopen, the same
# thing force_reconnect() does).
_HEARTBEAT_INTERVAL_S = 1.0
_HEARTBEAT_TIMEOUT_S = 6.0
_hb_last_probe = 0.0
_hb_last_reply = 0.0
_hb_link = None  # the link the two timestamps above belong to
_hb_rx = b""
_hb_missing_logged = False
_hb_replies = 0


def _heartbeat(link, now):
    """Called on the sender thread right after each frame write."""
    global _hb_last_probe, _hb_last_reply, _hb_link, _hb_rx, _hb_missing_logged, _hb_replies
    if link is not _hb_link:
        _hb_link = link
        _hb_last_probe = 0.0
        _hb_last_reply = now  # boot window counts as alive
        _hb_rx = b""
        _hb_missing_logged = False
    try:
        waiting = link.in_waiting
        if waiting:
            _hb_rx = (_hb_rx + link.read(waiting))[-256:]
    except Exception as e:
        _fail_serial_link(link, e)
        return
    if b"WLED" in _hb_rx:
        if _hb_last_probe and _hb_replies == 0:
            ver = _hb_rx[_hb_rx.rfind(b"WLED"):].split(b"\r")[0].decode("ascii", "replace")
            print(f"[WLED] Heartbeat OK ({ver}).")
        _hb_rx = b""
        _hb_last_reply = now
        _hb_replies += 1
        if _hb_missing_logged:
            print("[WLED] Heartbeat back.")
            _hb_missing_logged = False
    if now - _hb_last_probe >= _HEARTBEAT_INTERVAL_S:
        if _hb_last_probe and now - _hb_last_probe > 3 * _HEARTBEAT_INTERVAL_S:
            _hb_last_reply = now  # we stopped sending for a while -- restart the clock, don't blame the board
        _hb_last_probe = now
        try:
            link.write(b"v")
        except Exception as e:
            _fail_serial_link(link, e)
            return
    silent = now - _hb_last_reply
    if silent >= _HEARTBEAT_TIMEOUT_S and now - _serial_connect_time >= _SERIAL_BOOT_SETTLE_S + _HEARTBEAT_TIMEOUT_S:
        if getattr(config, "WLED_HEARTBEAT_RESET", False):
            device = link.port
            _fail_serial_link(link, f"no heartbeat for {silent:.1f}s -- resetting board")
            _usb_reenumerate(device)
        elif not _hb_missing_logged:
            print(f"[WLED] No heartbeat for {silent:.1f}s (log-only mode, not resetting).")
            _hb_missing_logged = True


_usb_resetting = False


def _usb_reenumerate(device):
    """2026-10-03, confirmed live: the marquee's CP2102 USB chip itself
    stopped answering (dmesg "cp210x_open - Unable to enable UART", reopen
    failed with EIO) -- a port reopen can't fix that, but de-authorizing and
    re-authorizing the USB device (a software unplug/replug) did. Linux only,
    needs passwordless sudo; blocks reconnect attempts while it runs."""
    global _usb_resetting
    import os
    import subprocess
    try:
        usb_dir = os.path.realpath(f"/sys/class/tty/{os.path.basename(device)}/device/../..")
    except Exception:
        return
    auth = os.path.join(usb_dir, "authorized")
    if not os.path.exists(auth):
        return

    def work():
        global _usb_resetting
        try:
            for val, pause in (("0", 1.5), ("1", 2.5)):
                subprocess.run(["sudo", "-n", "tee", auth], input=val.encode(),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                time.sleep(pause)
            print(f"[WLED] USB re-enumerated {os.path.basename(usb_dir)} ({device}).")
        except Exception as e:
            print(f"[WLED] USB re-enumerate of {device} failed: {e}")
        finally:
            _usb_resetting = False

    _usb_resetting = True
    threading.Thread(target=work, daemon=True, name="wled-usb-reset").start()


def heartbeat_status():
    """For diagnostics: (replies seen this process, seconds since last reply or None)."""
    if _hb_link is None or _hb_link is not _serial_link:
        return _hb_replies, None
    return _hb_replies, round(time.monotonic() - _hb_last_reply, 1)


def force_reconnect():
    """Manual recovery (recovery button): tears down the marquee's serial
    link exactly like a failed write does, so _try_connect_serial() reopens
    it on its next pass -- reopening resets the ESP32 through the RTS-wired
    auto-reset circuit, the same effect as a power cycle. Returns True if
    there was a live link to reset."""
    link = _serial_link
    if link is None:
        return False
    _fail_serial_link(link, "manual recovery requested")
    return True


_WRITE_STALL_GIVEUP_S = 3.0
_write_stall_since = None


def _serial_sender_loop():
    """Runs forever on its own daemon thread, started once at import -- same
    pattern as _ddp_sender_loop above, just for the serial write instead of
    the UDP send. See _serial_pending's declaration for why this exists.
    Also runs the proactive periodic reconnect (_PROACTIVE_RECONNECT_
    INTERVAL_S above) on this same thread -- it needs the same exclusive
    access to `link` that frame writes already have here, so piggybacking
    on this loop avoids a second thread contending for the same serial
    connection."""
    global _serial_pending, _write_stall_since
    while True:
        _serial_send_ready.wait()
        with _serial_lock:
            pending = _serial_pending
            _serial_pending = None
            _serial_send_ready.clear()
        if pending is None:
            continue
        link, frame = pending
        try:
            link.write(frame)
        except serial.SerialTimeoutException as e:
            # 2026-11-06: one brief USB stall (a 50ms write timeout) used to
            # tear the link down on the spot -- the marquee went dark AND its
            # freed port got grabbed by led_bridge. The board is usually fine;
            # keep the link and let the next frame through. Only a stall that
            # persists past _WRITE_STALL_GIVEUP_S (or the 'v' heartbeat going
            # silent) counts as a real failure. A partial frame just garbles
            # one Adalight frame; WLED resyncs on the next "Ada" header.
            now = time.monotonic()
            if _write_stall_since is None:
                _write_stall_since = now
                print(f"[WLED] Write stall on {link.port} -- holding the link. "
                      f"[{serial_ports.recovery_context()}]")
            if now - _write_stall_since >= _WRITE_STALL_GIVEUP_S:
                _write_stall_since = None
                _fail_serial_link(link, f"{e} for {_WRITE_STALL_GIVEUP_S:.0f}s")
            continue
        except Exception as e:
            _write_stall_since = None
            _fail_serial_link(link, e)
            continue
        if _write_stall_since is not None:
            print(f"[WLED] Write stall cleared after {time.monotonic() - _write_stall_since:.2f}s.")
            _write_stall_since = None
        _heartbeat(link, time.monotonic())
        if link is not _serial_link:
            continue
        if time.monotonic() - _serial_connect_time >= _PROACTIVE_RECONNECT_INTERVAL_S:
            _fail_serial_link(link, f"proactive reconnect ({_PROACTIVE_RECONNECT_INTERVAL_S:.0f}s interval)")


threading.Thread(target=_serial_sender_loop, daemon=True, name="wled-serial-sender").start()


def maybe_reresolve():
    """Poll once per frame from main.py, same shape as dmx.maybe_reconnect()
    -- cheap no-op once resolved; retries on a timer if mDNS lookup failed
    (e.g. this board hasn't joined WiFi yet, or the network doesn't route
    mDNS between the Pi and it).

    2026-08-28: confirmed live that a failed gethostbyname() on this rig's
    network takes ~5s to time out. That used to run inline here, which
    stalled the single-threaded main loop (shared with led_bridge.py's
    frame sending to the completely unrelated LED-matrix ESP32) for the
    same ~5s -- long enough to blow past Display.ino's FRAME_TIMEOUT_MS
    (2000ms) and flip the matrix panels back to their LOADING screen every
    retry cycle, even though this failure has nothing to do with that
    board. Resolution now happens on a background thread so a slow/absent
    WLED board can never stall matrix rendering."""
    global _last_resolve_attempt, _resolving
    if _ip is not None:
        return
    if _resolving:
        return
    if time.monotonic() - _last_resolve_attempt < _RESOLVE_RETRY_INTERVAL_S:
        return
    _last_resolve_attempt = time.monotonic()
    _resolving = True
    threading.Thread(target=_resolve_worker, daemon=True).start()


def set_pixel(index, r, g, b):
    """Raw single-LED write, 0-based across the whole marquee strip."""
    if not (0 <= index < config.MARQUEE_TOTAL_LEDS):
        return
    off = index * 3
    _pixels[off] = max(0, min(255, int(r)))
    _pixels[off + 1] = max(0, min(255, int(g)))
    _pixels[off + 2] = max(0, min(255, int(b)))


def set_segment(name, r, g, b):
    """Fills every LED in one of config.MARQUEE_SEGMENTS with a solid color."""
    seg = config.MARQUEE_SEGMENTS.get(name)
    if seg is None:
        return
    for i in range(seg["start"], seg["start"] + seg["count"]):
        set_pixel(i, r, g, b)


def fill(r, g, b):
    """Fills the entire wired marquee strip (all segments) with a solid color."""
    for i in range(config.MARQUEE_TOTAL_LEDS):
        set_pixel(i, r, g, b)


def render():
    """Sends the current pixel buffer as one frame, preferring direct USB
    serial (Adalight, throttled to _MAX_SERIAL_SEND_HZ) over DDP/WiFi when
    the serial link is connected and past its boot window -- see the module
    docstring. Falls through to DDP whenever serial isn't usable (not
    cabled yet, still booting, or a write just failed), and is a no-op if
    neither transport is currently available. Call once per frame (see
    main.py)."""
    global _sequence, _ip, _last_serial_send_time, _ddp_pending, _serial_pending

    if config.WLED_SERIAL_ENABLED:
        if _serial_link is None and time.monotonic() - _last_serial_scan_time >= _SERIAL_RESCAN_INTERVAL_S:
            _try_connect_serial()

    if config.WLED_SERIAL_ENABLED and _serial_ready():
        now = time.monotonic()
        if now - _last_serial_send_time < _MIN_SERIAL_SEND_INTERVAL_S:
            return  # healthy and connected, just not due for a send this tick
        _last_serial_send_time = now
        # Hand off to _serial_sender_loop's dedicated thread rather than
        # writing here directly -- see that function's docstring for why.
        # Bind the frame to THIS specific link instance so a write that's
        # still stuck blocking when _try_connect_serial() later reconnects
        # can't be confused with the new connection.
        with _serial_lock:
            _serial_pending = (_serial_link, _ADA_HEADER + bytes(_pixels))
        _serial_send_ready.set()
        return

    if _ip is None:
        return
    header = bytearray(10)
    header[0] = _DDP_FLAGS_VERSION1_PUSH
    header[1] = _sequence & 0x0F
    header[2] = _DDP_TYPE_RGB8
    header[3] = _DDP_OUTPUT_ID
    # bytes 4-7 (channel offset) left at 0 -- always addressing from pixel 0
    length = len(_pixels)
    header[8] = (length >> 8) & 0xFF
    header[9] = length & 0xFF
    packet = bytes(header) + bytes(_pixels)
    # Hand off to _ddp_sender_loop's dedicated thread rather than calling
    # sendto() here directly -- see that function's docstring for why. Just
    # replaces whatever frame was still pending (latest-wins, same
    # tolerance this protocol already has for a dropped frame).
    with _ddp_lock:
        _ddp_pending = (packet, _ip)
    _ddp_send_ready.set()
    _sequence = (_sequence + 1) % 16


# ------------------------------------------------------------
# Effects (2026-08-23, expanded same day). v1 scope -- see config.py's
# MARQUEE section comment for what this deliberately does NOT yet cover.
# ------------------------------------------------------------
_flash_color = None
_flash_until = 0.0


def flash(r, g, b, duration=None):
    """Brief solid-color override across the whole marquee, taking priority
    over whatever update() would otherwise render -- same shape as
    dmx_driver.py's pulse_channel(), but a color flash instead of a relay
    pulse. Self-expiring; nothing needs to explicitly turn it back off."""
    global _flash_color, _flash_until
    _flash_color = (r, g, b)
    _flash_until = time.time() + (duration if duration is not None else config.MARQUEE_FLASH_SECONDS)


def _resolve_marquee_color(color):
    """The DJ_COLOR_PALETTE entries built around the DMX fixtures' dedicated
    White/Amber/UV emitters (config.py: "white lamp", "amber lamp", "uv")
    carry (0, 0, 0) as their plain RGB tuple -- correct for the DMX rig,
    but a marquee LED just unpacking `r, g, b = color` would go dark
    whenever the operator picks one of those three looks, for no visible
    reason. Marquee LEDs are plain RGB with no separate emitters, so this
    substitutes the nearest plain-RGB approximation instead."""
    r, g, b = color
    if color.white:
        return 255, 230, 180   # warm-white approximation
    if color.amber:
        return 255, 140, 20    # same warm-amber family as the "amber" palette entry
    if color.uv:
        return 120, 0, 255     # nearest visible analog to blacklight -- deep violet
    return r, g, b


def _twinkle(r, g, b, pixels=None):
    """Per-pixel random brightness scaling, independently re-rolled every
    call -- looks like a sparkle regardless of physical wiring direction
    (unlike a moving chase, which needs a confirmed pixel order to look
    right; see config.py's note that panel3's loop direction isn't
    confirmed yet). `pixels` defaults to the whole strip; pass a narrower
    iterable (see _twinkle_segment() below) to sparkle just one segment."""
    if pixels is None:
        pixels = range(config.MARQUEE_TOTAL_LEDS)
    for i in pixels:
        level = random.uniform(0.15, 1.0)
        set_pixel(i, r * level, g * level, b * level)


def _twinkle_segment(name, r, g, b):
    """_twinkle(), scoped to one config.MARQUEE_SEGMENTS entry -- used by
    _apply_mystery_marquee()'s "Who is this?" sparkle stage, which only
    wants the "title" segment sparkling while panels 3-6 stay dark."""
    seg = config.MARQUEE_SEGMENTS[name]
    _twinkle(r, g, b, pixels=range(seg["start"], seg["start"] + seg["count"]))


_intro_twinkle_levels = [1.0] * config.MARQUEE_TOTAL_LEDS
_intro_twinkle_next = 0.0


def _intro_ramp(el):
    """0..1 brightness of the show open's twinkle: up from SHOW_INTRO_TWINKLE_
    START to _FULL, held, then down over SHOW_INTRO_FADE_OUT_SECONDS from
    SHOW_INTRO_FADE_OUT_AT_SECONDS. Shared shape with drivers/accent_engine.py's
    outline ramp."""
    c = config
    if el < c.SHOW_INTRO_TWINKLE_START_SECONDS:
        return 0.0
    if el < c.SHOW_INTRO_TWINKLE_FULL_SECONDS:
        return (el - c.SHOW_INTRO_TWINKLE_START_SECONDS) / (
            c.SHOW_INTRO_TWINKLE_FULL_SECONDS - c.SHOW_INTRO_TWINKLE_START_SECONDS)
    if el < c.SHOW_INTRO_FADE_OUT_AT_SECONDS:
        return 1.0
    return max(0.0, 1.0 - (el - c.SHOW_INTRO_FADE_OUT_AT_SECONDS) / c.SHOW_INTRO_FADE_OUT_SECONDS)


def _build_intro_name_bulbs():
    """Bulbs framing "Trivia"/"cade" (panels 3 + 4) as ONE outline
    (2026-10-28): both panels' loops minus the two inner side runs at the
    seam between them (panel 3's right edge, panel 4's left edge), picked
    off config.MARQUEE_LAYOUT's photo coordinates."""
    xs = {}
    for name in ("panel3", "panel4"):
        i = config.MARQUEE_SEGMENTS[name]["start"]
        for x0, y0, x1, y1, n in config.MARQUEE_LAYOUT[name]:
            for k in range(n):
                f = k / (n - 1) if n > 1 else 0.0
                xs[i] = x0 + (x1 - x0) * f
                i += 1
    p3 = [x for i, x in xs.items() if i in _segment_range("panel3")]
    p4 = [x for i, x in xs.items() if i in _segment_range("panel4")]
    seam = (max(p3) + min(p4)) / 2.0
    gap = (min(p4) - max(p3)) / 2.0
    return [i for i, x in sorted(xs.items()) if abs(x - seam) > gap + 15]


def _segment_range(name):
    seg = config.MARQUEE_SEGMENTS[name]
    return range(seg["start"], seg["start"] + seg["count"])


def _intro_twinkle(now, level, pixels=None):
    """The "Twinkle Base" look in white at config.SHOW_INTRO_TWINKLE_SPEED:
    a dim wash with occasional brighter sparkles, re-rolled at that fraction
    of the normal rate, all scaled by `level` (0..1) every frame so the
    brightness ramp is smooth between rolls. `pixels` limits it to those
    bulbs (the rest go dark)."""
    global _intro_twinkle_next
    if now >= _intro_twinkle_next:
        _intro_twinkle_next = max(_intro_twinkle_next + _DANCE_FRAME_S / config.SHOW_INTRO_TWINKLE_SPEED,
                                  now - _DANCE_FRAME_S)
        density = 0.08
        for i in range(config.MARQUEE_TOTAL_LEDS):
            _intro_twinkle_levels[i] = random.uniform(0.6, 1.0) if random.random() < density else 0.25
    if pixels is not None:
        fill(0, 0, 0)
    for i in (range(config.MARQUEE_TOTAL_LEDS) if pixels is None else pixels):
        v = 255 * _intro_twinkle_levels[i] * level
        set_pixel(i, v, v, v)


def _apply_intro(now):
    """Show open (2026-10-14, timings in config.SHOW_INTRO_*): white movie
    chase on every segment while the top strip's words play, lights out as
    "Let's Play" cuts in at 9s, white twinkle (one outline around panels 3 + 4
    only, 2026-10-28) ramping up 10-14s and fading out
    from 15s, dark while the host talks, then the wheel spin when the show
    moves on (_apply_wheel)."""
    c = config
    el = now - state.show_phase_started_at
    if state.show_wheel_started_at:
        _apply_wheel(now)
    elif el < c.SHOW_INTRO_LETS_PLAY_AT_SECONDS:
        for name in c.MARQUEE_SEGMENTS:
            _theater_chase_segment(name, now, 255, 255, 255)
    elif el < c.SHOW_INTRO_FADE_OUT_AT_SECONDS + c.SHOW_INTRO_FADE_OUT_SECONDS:
        level = _intro_ramp(el)
        if level > 0.0:
            # only around "Trivia" / "cade" (panels 3 + 4), one outline
            _intro_twinkle(now, level, _INTRO_NAME_BULBS)
        else:
            fill(0, 0, 0)
    else:
        fill(0, 0, 0)


_INTRO_NAME_BULBS = _build_intro_name_bulbs()


def _apply_wheel(now):
    """A wheel spin: every lower-panel outline chases white with the square
    under the pointer solid; after the stop only the landed panel's outline
    blinks (in step with its LED square, drivers/intro_wheel.py)."""
    from drivers import intro_wheel
    wheel_el = now - state.show_wheel_started_at
    _angle, hot, landed = intro_wheel.pointer(wheel_el)
    fill(0, 0, 0)
    if landed:
        if intro_wheel.blink_on(wheel_el):
            set_segment(f"panel{hot}", 255, 255, 255)
    else:
        for name in _SIMON_PANEL_SEGMENTS:
            _theater_chase_segment(name, now, 255, 255, 255)
        set_segment(f"panel{hot}", 255, 255, 255)


def _apply_outro(now):
    if now < state.show_outro_music_ends_at:
        _twinkle(255, 255, 255)
    else:
        fill(0, 0, 0)


def _segment_pixel_index(seg, offset):
    """Maps a 0-based position *within* a segment's own loop to its real
    index in the global pixel buffer, honoring that segment's "reverse"
    flag (config.py) -- every pattern below that walks a segment's loop
    routes through this rather than assuming index order matches physical
    travel direction."""
    pos = (seg["count"] - 1 - offset) if seg.get("reverse") else offset
    return seg["start"] + pos


def _dj_pattern_breathe(now, r, g, b):
    """Pattern 0: the whole marquee fades in/out together as one color."""
    level = 0.55 + 0.45 * math.sin(now * 2 * math.pi / config.MARQUEE_DJ_BREATHE_PERIOD_SECONDS)
    fill(r * level, g * level, b * level)


def _dj_pattern_wave(now, r, g, b):
    """Pattern 1: a brightness wave travels along each segment's own loop
    -- direction-agnostic (a sine wave looks like smooth motion whichever
    way it's actually flowing), so safe to use before loop direction is
    confirmed."""
    phase = now / config.MARQUEE_DJ_WAVE_SPEED_SECONDS * 2 * math.pi
    for seg in config.MARQUEE_SEGMENTS.values():
        for offset in range(seg["count"]):
            angle = phase + (offset / config.MARQUEE_DJ_WAVE_LENGTH_LEDS) * 2 * math.pi
            level = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(angle))
            set_pixel(_segment_pixel_index(seg, offset), r * level, g * level, b * level)


def _dj_pattern_twinkle_base(now, r, g, b):
    """Pattern 2: a dim base wash of the current color with occasional
    brighter sparkle pixels re-rolled every frame (at 100% speed; slower
    speeds hold each roll longer, faster ones sparkle more densely). Depth
    sets the base wash: 0.25 at the default 50%, black at 100%."""
    global _twinkle_next
    if now < _twinkle_next:
        return  # hold the current roll (slow speed)
    _twinkle_next = max(_twinkle_next + _DANCE_FRAME_S, now - _DANCE_FRAME_S)  # ~1 roll per frame at 100%
    base = 0.5 * (1.0 - state.marquee_depth / 100.0)
    density = min(0.5, 0.08 * max(1.0, state.marquee_speed / 100.0))
    for i in range(config.MARQUEE_TOTAL_LEDS):
        level = random.uniform(0.6, 1.0) if random.random() < density else base
        set_pixel(i, r * level, g * level, b * level)


_soft_levels = [0.0] * config.MARQUEE_TOTAL_LEDS


def _dj_pattern_twinkle_soft(now, r, g, b):
    """Pattern 10 (2026-10-05): Twinkle Base with a trailing glow -- each
    sparkle pops on and then fades out smoothly (~0.3 s half-life at 100%
    speed) instead of blinking off on the next roll. Same depth response as
    Twinkle Base: depth sets the base wash the glows fade down to (0.25 at
    the default 50%, black at 100%); speed scales spark rate and fade."""
    k = _dance_dt / _DANCE_FRAME_S        # frames' worth of animation this tick
    if k <= 0:
        return
    base = 0.5 * (1.0 - state.marquee_depth / 100.0)
    spark = 1.0 - (1.0 - config.MARQUEE_TWINKLE_SOFT_SPARK_RATE) ** k
    decay = config.MARQUEE_TWINKLE_SOFT_DECAY ** k
    for i in range(config.MARQUEE_TOTAL_LEDS):
        glow = _soft_levels[i] * decay
        if random.random() < spark:
            glow = max(glow, random.uniform(0.6, 1.0))
        _soft_levels[i] = glow
        level = base + (1.0 - base) * glow
        set_pixel(i, r * level, g * level, b * level)


def _dj_pattern_call_response(now, r, g, b):
    """Pattern 3: the title outline and the panel3 outline breathe against
    each other in opposite phase, rather than in lockstep."""
    for name, seg in config.MARQUEE_SEGMENTS.items():
        offset_phase = 0.0 if name == "title" else math.pi
        level = 0.5 + 0.5 * math.sin(now * 2 * math.pi / config.MARQUEE_DJ_BREATHE_PERIOD_SECONDS + offset_phase)
        level = 0.15 + 0.85 * level
        for i in range(seg["start"], seg["start"] + seg["count"]):
            set_pixel(i, r * level, g * level, b * level)


def _dj_pattern_complement_chase(now, r, g, b):
    """Pattern 4: the current color and its complement (opposite hue) as one
    continuous gradient all the way around each segment's own loop --
    color -> complement -> color, so the loop has no seam -- travelling at
    the Rainbow Chase's pace (2026-10-05; was a 7-LED comet on black). The
    blend is in RGB, then lifted back to full level, so the midpoint reads
    as a pale mix (red/cyan -> white) instead of sagging to grey."""
    comp = color_utils.hue_shift(r, g, b, 180)
    for seg in config.MARQUEE_SEGMENTS.values():
        count = seg["count"]
        for offset in range(count):
            p = (offset / count + now * config.MARQUEE_DJ_RAINBOW_SPEED) % 1.0
            set_pixel(_segment_pixel_index(seg, offset), *_complement_mix((r, g, b), comp, p))


def _complement_mix(col, comp, p):
    """Color at phase p (0..1) of a color -> complement -> color loop: an RGB
    blend lifted back to the brighter end's level, so the midpoint reads as
    a pale mix rather than sagging to grey."""
    t = 0.5 - 0.5 * math.cos(2 * math.pi * p)
    mix = [a * (1 - t) + c * t for a, c in zip(col, comp)]
    m = max(mix)
    k = max(max(col), max(comp)) / m if m > 0 else 0.0
    return mix[0] * k, mix[1] * k, mix[2] * k


def _mirror_positions(name, seg):
    """(global LED index, distance 0..count/2 from the segment's bounce pivot
    -- config.MARQUEE_SEGMENT_CENTERS, a horizontal edge's center -- measured
    either way round the loop). Patterns built on the distance are mirror-
    symmetric and turn around at the pivot and the opposite center, so the
    wiring seam (a corner) never shows. Wiring direction doesn't matter."""
    n = seg["count"]
    pivot = config.MARQUEE_SEGMENT_CENTERS.get(name, 0) % n
    for pos in range(n):
        d = (pos - pivot) % n
        yield seg["start"] + pos, min(d, n - d)


def _dj_pattern_rainbow_chase(now, r, g, b):
    """Pattern 5: a hue-cycling rainbow travels around each segment's own
    loop -- ignores the current DJ color entirely, always vibrant. The
    "granular detail" showcase pattern."""
    for seg in config.MARQUEE_SEGMENTS.values():
        count = seg["count"]
        for offset in range(count):
            hue = (offset / count + now * config.MARQUEE_DJ_RAINBOW_SPEED) % 1.0
            pr, pg, pb = colorsys.hsv_to_rgb(hue, 1.0, _dance_bri)
            set_pixel(_segment_pixel_index(seg, offset), pr * 255, pg * 255, pb * 255)


def _dj_pattern_bounce_comet(now, r, g, b):
    """Pattern 6: a comet sweeps back and forth, current color at the head
    fading into an adjacent hue (a small +30 deg shift, not a full
    complement) at the tail. Since 2026-10-05 it's a mirrored PAIR per
    segment: they leave one edge's center together, run up opposite sides,
    meet at the other edge's center and bounce back -- the turnarounds are
    at the centers, not the wiring seam in a corner."""
    ar, ag, ab = color_utils.hue_shift(r, g, b, 30)
    t = (now % config.MARQUEE_DJ_BOUNCE_PERIOD_SECONDS) / config.MARQUEE_DJ_BOUNCE_PERIOD_SECONDS
    triangle = t * 2 if t < 0.5 else 2 - t * 2  # 0 -> 1 -> 0
    tail = 5
    for name, seg in config.MARQUEE_SEGMENTS.items():
        pos = triangle * seg["count"] / 2
        for i, d in _mirror_positions(name, seg):
            dist = abs(d - pos)
            if dist < tail:
                t2 = dist / tail
                lr, lg, lb = r * (1 - t2) + ar * t2, g * (1 - t2) + ag * t2, b * (1 - t2) + ab * t2
                level = 1.0 - t2 * 0.6
                set_pixel(i, lr * level, lg * level, lb * level)
            else:
                set_pixel(i, 0, 0, 0)


def _dj_pattern_bounce_continuous(now, r, g, b):
    """Pattern 9 (2026-10-05): the Complement Chase gradient, mirrored about
    the bounce pivot (color at one edge's center, complement at the other),
    flowing outward from the center for half of
    MARQUEE_DJ_BOUNCE_GRADIENT_PERIOD_SECONDS, then back in."""
    comp = color_utils.hue_shift(r, g, b, 180)
    t = (now % config.MARQUEE_DJ_BOUNCE_GRADIENT_PERIOD_SECONDS) / config.MARQUEE_DJ_BOUNCE_GRADIENT_PERIOD_SECONDS
    triangle = t * 2 if t < 0.5 else 2 - t * 2
    for name, seg in config.MARQUEE_SEGMENTS.items():
        n = seg["count"]
        for i, d in _mirror_positions(name, seg):
            set_pixel(i, *_complement_mix((r, g, b), comp, d / n - triangle))


def _dj_pattern_confetti(now, r, g, b):
    """Pattern 7: random sparks in the current color or its complement,
    decaying into the existing frame rather than a hard clear each tick --
    a trailing confetti/glitter look. The only pattern that reads directly
    from/writes directly to _pixels instead of going through set_pixel()
    for every LED every frame, since the decay needs last frame's values.

    Rates are per 40 fps frame at 100% speed, scaled by the speed dial's
    clock step. Depth: the default 50% decays to black at 0.85/frame; higher
    decays faster (crisper sparks, down to 0.6/frame at 100%); lower decays
    only down to a glow of the current color (up to 40% of it at 0%)."""
    cr, cg, cb = color_utils.hue_shift(r, g, b, 180)
    k = _dance_dt / _DANCE_FRAME_S        # frames' worth of animation this tick
    if k <= 0:
        return
    depth = state.marquee_depth / 100.0
    decay = (0.85 - 0.5 * max(0.0, depth - 0.5)) ** k
    floor = 0.8 * max(0.0, 0.5 - depth)
    fl = (r * floor, g * floor, b * floor)
    spark = 1.0 - (1.0 - 0.06) ** k
    for i in range(config.MARQUEE_TOTAL_LEDS):
        if random.random() < spark:
            pr, pg, pb = (cr, cg, cb) if random.random() < 0.5 else (r, g, b)
            set_pixel(i, pr, pg, pb)
        else:
            off = i * 3
            for c in range(3):
                v = _pixels[off + c]
                _pixels[off + c] = int(min(255, fl[c] + (v - fl[c]) * decay if v > fl[c] else fl[c]))


def _dj_pattern_movie_chase(now, r, g, b):
    """Pattern 8: classic theater-marquee bulb chase (every
    SIMON_MARQUEE_CHASE_SPACING'th pixel lit, shifting over time) across
    every segment at once, in the current DJ color -- the same
    _theater_chase_segment() primitive already used for Simon's top-strip
    pattern and the mystery-reveal cascade, now selectable directly as its
    own DJ-mode theme (2026-09-18 request)."""
    for name in config.MARQUEE_SEGMENTS:
        _theater_chase_segment(name, now, r, g, b)


_DJ_PATTERNS = [
    _dj_pattern_breathe, _dj_pattern_wave, _dj_pattern_twinkle_base, _dj_pattern_call_response,
    _dj_pattern_complement_chase, _dj_pattern_rainbow_chase, _dj_pattern_bounce_comet, _dj_pattern_confetti,
    _dj_pattern_movie_chase, _dj_pattern_bounce_continuous, _dj_pattern_twinkle_soft,
]


# Admin-panel dials (2026-10-05): the patterns run on their own clock, which
# advances at state.marquee_speed % of real time; _dance_dt is this tick's
# step of it (Confetti's decay), _dance_bri the brightness dial (Rainbow,
# which ignores the color). Speed/brightness/depth/off are per-song.
_DANCE_FRAME_S = 1.0 / 40      # main loop rate the per-frame patterns were tuned at
_dance_clock = 0.0
_dance_last = None
_dance_dt = 0.0
_dance_bri = 1.0
_twinkle_next = 0.0


_story_seg_of = None     # bulb index -> segment name
_story_gap = None        # bulb indices along the inner edges between a lit pair (kept dark)
_story_centers = {}      # row -> (cx, cy) of the pair


def _story_prepare():
    """Static maps for the story accents: which segment each bulb is in, the
    bulbs on the facing inner edges of each side-by-side pair of panels (left
    pair member's right side, right member's left side) so a lit pair reads
    as one long box, and each pair's centre."""
    global _story_seg_of, _story_gap
    _story_seg_of = [""] * config.MARQUEE_TOTAL_LEDS
    for name, seg in config.MARQUEE_SEGMENTS.items():
        for i in range(seg["start"], seg["start"] + seg["count"]):
            if i < len(_story_seg_of):
                _story_seg_of[i] = name
    _story_gap = set()
    for row, (lname, rname) in oz_story.ROW_SEGMENTS.items():
        idx = {}
        for n in (lname, rname):
            s = config.MARQUEE_SEGMENTS[n]
            idx[n] = list(range(s["start"], s["start"] + s["count"]))
        lx = [_bulb_xy[i][0] for i in idx[lname]]
        rx = [_bulb_xy[i][0] for i in idx[rname]]
        lw, rw = max(lx) - min(lx), max(rx) - min(rx)
        _story_gap.update(i for i in idx[lname] if _bulb_xy[i][0] >= max(lx) - 0.06 * lw)
        _story_gap.update(i for i in idx[rname] if _bulb_xy[i][0] <= min(rx) + 0.06 * rw)
        both = idx[lname] + idx[rname]
        _story_centers[row] = (sum(_bulb_xy[i][0] for i in both) / len(both),
                               sum(_bulb_xy[i][1] for i in both) / len(both))


def _apply_story(now, pos):
    """Story song (drivers/oz_story.py). Mostly DARK -- the red panel
    animation is the show, and a lit frame around every panel just reads as
    scoreboards. Lit only for the big moments (oz_story.full_marquee: every
    bulb coloured from its measured board position) and as an accent circling
    the pair of lower panels while a vignette plays, with the bulbs between
    the two panels switched off so the pair looks like one long box."""
    if _story_seg_of is None:
        _story_prepare()
    bri = max(0.0, min(1.0, state.marquee_brightness / 100.0))
    if oz_story.full_marquee(pos):
        for i in range(config.MARQUEE_TOTAL_LEDS):
            x, y = _bulb_xy[i]
            r, g, b = oz_story.marquee_color(x, y, pos, now)
            set_pixel(i, r * bri, g * bri, b * bri)
        return
    fill(0, 0, 0)
    acc = oz_story.lower_accent(pos)
    if acc is None:
        return
    row, strength, kind = acc
    cx, cy = _story_centers[row]
    for name in oz_story.ROW_SEGMENTS[row]:
        seg = config.MARQUEE_SEGMENTS[name]
        for i in range(seg["start"], seg["start"] + seg["count"]):
            if i in _story_gap:
                continue
            x, y = _bulb_xy[i]
            r, g, b = oz_story.accent_color(kind, strength, math.atan2(y - cy, x - cx), now)
            set_pixel(i, r * bri, g * bri, b * bri)


def _apply_dj_dance(now):
    global _dance_clock, _dance_last, _dance_dt, _dance_bri, _twinkle_next
    dt = 0.0 if _dance_last is None else max(0.0, min(0.25, now - _dance_last))
    _dance_last = now
    _dance_dt = dt * state.marquee_speed / 100.0
    _dance_clock += _dance_dt
    if _twinkle_next > _dance_clock + 1.0:
        _twinkle_next = 0.0
    if state.marquee_off:
        fill(0, 0, 0)
        return
    _dance_bri = max(0.0, min(1.0, state.marquee_brightness / 100.0))
    t = _dance_clock
    r, g, b = _resolve_marquee_color(config.DJ_COLOR_PALETTE[state.marquee_color_index])
    r, g, b = r * _dance_bri, g * _dance_bri, b * _dance_bri
    gradient_mode = state.marquee_gradient_mode
    if gradient_mode == "rainbow":
        # No "color" to shift -- always the rainbow sweep regardless of
        # marquee_theme_index, same override wled_engine's DMX/accent
        # counterparts use for their own rainbow gradient mode.
        _dj_pattern_rainbow_chase(t, r, g, b)
        return
    if gradient_mode in ("adjacent", "complementary"):
        degrees = 30 if gradient_mode == "adjacent" else 180
        r, g, b = color_utils.hue_shift(r, g, b, degrees)
    pattern_fn = _DJ_PATTERNS[state.marquee_theme_index % len(_DJ_PATTERNS)]
    pattern_fn(t, r, g, b)


def _apply_game_chase(now):
    """Game mode: an all-white comet-style chase looping within each
    segment's own bounds (title and panel3 are separate physical loops,
    not one continuous run, so each gets its own independent comet rather
    than one chase walking the whole buffer), interrupted every
    MARQUEE_GAME_FLASH_INTERVAL_SECONDS by a quick double flash. White
    rather than the current DJ color -- "go directly to white" is the
    marquee's dedicated game-mode identity, distinct from DJ mode's color
    patterns."""
    cycle = now % config.MARQUEE_GAME_FLASH_INTERVAL_SECONDS
    burst = config.MARQUEE_GAME_FLASH_BURST_SECONDS
    if cycle < burst or burst * 2 <= cycle < burst * 3:
        fill(255, 255, 255)
        return
    if burst <= cycle < burst * 2:
        fill(0, 0, 0)
        return

    for seg in config.MARQUEE_SEGMENTS.values():
        count = seg["count"]
        step = int(now / config.MARQUEE_GAME_CHASE_STEP_SECONDS) % count
        for offset in range(count):
            dist = (offset - step) % count
            if dist < config.MARQUEE_GAME_COMET_LENGTH:
                level = 1.0 - (dist / config.MARQUEE_GAME_COMET_LENGTH)
                level = int(255 * level)
                set_pixel(_segment_pixel_index(seg, offset), level, level, level)
            else:
                set_pixel(_segment_pixel_index(seg, offset), 0, 0, 0)


_SIMON_PANEL_SEGMENTS = ["panel3", "panel4", "panel5", "panel6"]


def _blackout_lower_panels():
    """Zeroes panels 3-6, leaving "title" untouched -- shared by
    _apply_get_ready_marquee() and _apply_mystery_marquee() below, both of
    which want the lower panels dark while the top strip keeps doing its
    own thing (2026-09-16 operator feedback: the marquee should stay
    top-panel-primary during these moments instead of running its usual
    full multi-panel DJ-dance patterns everywhere)."""
    for name in _SIMON_PANEL_SEGMENTS:
        set_segment(name, 0, 0, 0)


def _theater_chase_segment(name, now, r, g, b):
    """Classic theater-marquee bulb chase (every SIMON_MARQUEE_CHASE_
    SPACING'th pixel lit solid, the whole pattern shifting by one pixel
    every SIMON_MARQUEE_CHASE_STEP_SECONDS), in the given color, confined
    to one segment's own loop. Factored out of _apply_simon() below (which
    uses this same pattern, fixed to white on the "title" segment) so
    _apply_mystery_marquee() can reuse it per answer panel, each in that
    panel's own button color, for the "Who is this?" reveal cascade."""
    seg = config.MARQUEE_SEGMENTS[name]
    count = seg["count"]
    spacing = config.SIMON_MARQUEE_CHASE_SPACING
    step = int(now / config.SIMON_MARQUEE_CHASE_STEP_SECONDS) % spacing
    for offset in range(count):
        if (offset - step) % spacing == 0:
            set_pixel(_segment_pixel_index(seg, offset), r, g, b)
        else:
            set_pixel(_segment_pixel_index(seg, offset), 0, 0, 0)


def _steady_panel_color(color_name):
    """A panel color's RGB, scaled down per config.SIMON_HW_COLOR_RGB_
    STEADY_SCALE (2026-09-18, operator feedback that steady/selected
    yellow was still too bright even after its earlier amber hue-shift).
    Only for solid/steady blocks -- mid-chase-animation calls
    (_theater_chase_segment) stay at full brightness since those already
    read fine, not being a static block."""
    r, g, b = config.SIMON_HW_COLOR_RGB[color_name]
    scale = config.SIMON_HW_COLOR_RGB_STEADY_SCALE.get(color_name, 1.0)
    return r * scale, g * scale, b * scale


# Final-reveal hold (2026-09-18, redesigned 2026-09-18): once every choice
# has cascaded in, ALL revealed panels keep chasing together for this long
# before settling in unison to the dimmed, pulsing steady look. Earlier
# version singled out just the last-revealed ("blue") panel to keep
# chasing solo for 3s while the rest had already gone steady -- that read
# as blue being flagged as the answer, and held far too long besides.
_FINAL_REVEAL_HOLD_SECONDS = 1.0


def _double_pulse_level(now, period=3.0, floor=2 / 3):
    """Steady `floor` brightness level with two brief, softly-faded pulses
    to full brightness near the end of each `period`-second cycle -- "poof
    poof" (2026-09-18). Pure function of `now`, no internal state, same
    sine-multiplier idiom as _dj_pattern_breathe() above, just with two
    narrow raised-cosine bumps instead of one wide sine wave."""
    phase = now % period
    pulse_width = 0.25  # seconds each bump takes to rise from floor to full and back
    bump = 0.0
    for center in (period - 0.6, period - 0.3):
        d = abs(phase - center)
        d = min(d, period - d)  # wrap around the cycle boundary
        if d < pulse_width:
            bump = max(bump, 0.5 * (1 + math.cos(math.pi * d / pulse_width)))
    return floor + (1 - floor) * bump


# Jazz "awaiting answer" look (2026-11-06): replaces the old subtle double
# pulse on the lit A/B/C/D panels. Locked to the track's tempo
# (state.dj_tempo_period = seconds per beat, same source the DMX uplights
# dance to). A 4-beat bar with a backbeat comp on 2 and 4, a soft swung
# "and-of-3" ghost note, and a gentle downbeat; each hit pops to full and
# decays fast (clearly visible vs. the old 2/3 -> 1 fade). On top, every
# few bars each panel in turn gets a diagonal shimmer sweep across it.
_JAZZ_FLOOR = 0.40
_JAZZ_HITS = ((0.0, 1.0),)  # (beat in bar, strength): whole-note step only, once per bar
_JAZZ_DECAY = 4.5            # per beat; higher = snappier
_JAZZ_SHIMMER_CYCLE = 16.0   # beats between shimmer starts on one panel
_JAZZ_SHIMMER_LEN = 1.5      # beats a sweep takes
_JAZZ_SHIMMER_STAGGER = 0.0  # all four panels shimmer together
_panel_diag = {}


def _jazz_beat(now):
    p = getattr(state, "dj_tempo_period", None) or 0.5
    return now / max(0.3, min(1.2, p))


def _jazz_level(beat):
    pos = beat % 4.0
    best = 0.0
    for hit, strength in _JAZZ_HITS:
        d = (pos - hit) % 4.0
        best = max(best, strength * math.exp(-d * _JAZZ_DECAY))
    return _JAZZ_FLOOR + (1.0 - _JAZZ_FLOOR) * min(1.0, best)


def _panel_diag_values(name):
    """Per-LED diagonal coordinate (0..1, top-left -> bottom-right) within
    one panel's own bounding box, from the photo-measured bulb map."""
    cached = _panel_diag.get(name)
    if cached is not None:
        return cached
    seg = config.MARQUEE_SEGMENTS[name]
    idx = range(seg["start"], seg["start"] + seg["count"])
    vals = [_bulb_xy[i][0] + _bulb_xy[i][1] for i in idx]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    out = [(v - lo) / span for v in vals]
    _panel_diag[name] = out
    return out


def _apply_steady_jazz(name, i, now):
    """Drive one lit answer panel (index i) with the jazz pulse + shimmer."""
    seg = config.MARQUEE_SEGMENTS[name]
    beat = _jazz_beat(now)
    level = _jazz_level(beat)
    r, g, b = _steady_panel_color(config.SIMON_HW_COLOR_ORDER[i])
    t = ((beat - i * _JAZZ_SHIMMER_STAGGER) % _JAZZ_SHIMMER_CYCLE) / _JAZZ_SHIMMER_LEN
    if t >= 1.0:
        set_segment(name, r * level, g * level, b * level)
        return
    front = -0.2 + 1.4 * t   # band centre sweeps past both corners
    diag = _panel_diag_values(name)
    for k in range(seg["count"]):
        w = math.exp(-((diag[k] - front) / 0.16) ** 2)  # band profile
        lv = level + (1.0 - level) * w
        white = 0.65 * w
        set_pixel(seg["start"] + k,
                  (r * lv) * (1 - white) + 255 * white,
                  (g * lv) * (1 - white) + 255 * white,
                  (b * lv) * (1 - white) + 255 * white)


def _trip_segment(name, i, now, rnd):
    """The lit pad's marquee loop, getting trippier every round (round 1 =
    the plain solid block). Per bulb along the loop: travelling brightness
    waves (more of them, faster, and counter-rotating as rounds climb), the
    pad's own colour drifting into a rotating rainbow from mid-rounds, a
    beat strobe, and white sparkle bulbs late. Always keeps some bulbs lit in
    the pad's colour so the player can still read which pad it is."""
    seg = config.MARQUEE_SEGMENTS[name]
    n = seg["count"]
    trip = 0.0 if rnd <= config.SIMON_TRIP_STRAIGHT_ROUNDS else min(1.0, max(0.0, (rnd - 1) / (config.SIMON_TRIP_ROUNDS_TO_MAX - 1))) ** 0.75
    br, bg, bb = _steady_panel_color(config.SIMON_HW_COLOR_ORDER[i])
    if trip <= 0.0:
        set_segment(name, br, bg, bb)
        return
    period = max(0.2, state.dj_tempo_period or 0.5)
    kick = math.exp(-((now / period) % 1.0) * 5.0)
    waves = 1 + int(trip * 4)
    direction = 1 if i % 2 == 0 else -1
    for off in range(n):
        u = off / n
        w1 = math.sin(2 * math.pi * (u * waves - direction * now * (0.5 + 2.5 * trip)))
        w2 = math.sin(2 * math.pi * (u * (waves + 1) + direction * now * (0.8 + 1.5 * trip)))
        v = 0.5 + 0.25 * w1 + 0.25 * w2
        amp = min(1.0, trip * 1.6)
        lvl = 1.0 - amp * (1.0 - v)
        if trip > 0.65:                                   # hard-edged late
            lvl = 1.0 if lvl > 0.55 else 0.0
        r, g, b = br, bg, bb
        mix = max(0.0, trip - 0.3) * 1.3                  # rainbow creeps in
        if mix > 0:
            hr, hg, hb = colorsys.hsv_to_rgb((u + now * 0.35 * direction) % 1.0, 1.0, 1.0)
            r, g, b = (br * (1 - mix) + 255 * hr * mix, bg * (1 - mix) + 255 * hg * mix, bb * (1 - mix) + 255 * hb * mix)
        lvl = min(1.0, lvl + kick * 0.5 * trip)
        if trip > 0.5 and hash((off, int(now * 14))) % 11 == 0:
            r, g, b, lvl = 255, 255, 255, 1.0              # sparkle
        set_pixel(_segment_pixel_index(seg, off), r * lvl, g * lvl, b * lvl)


def _apply_simon(now):
    """Simon mini-game (drivers/simon_engine.py), any phase (intro,
    playback, input, or the loss sequence): panels 3-6 show a solid block
    of that pad's own color -- the ONE place in the whole marquee system
    that departs from "current DJ color" and shows fixed, meaningful colors
    instead, since these are true RGB and (unlike the red matrix panels)
    can actually convey which pad is lit. Single source of truth is
    state.simon_active_pad, which simon_engine.py already drives
    identically for every phase (including the loss sequence's own
    on/off flash cadence), so no per-phase branching is needed here.

    The top strip runs a classic theater-marquee bulb chase (every
    SIMON_MARQUEE_CHASE_SPACING'th pixel lit solid white across the ENTIRE
    loop at once, the whole pattern shifting by one pixel every
    SIMON_MARQUEE_CHASE_STEP_SECONDS) -- but ONLY during "intro",
    "get_ready", and "score_review"; it's fully dark during "playback"/
    "input" (2026-08-14, operator feedback that it was distracting during
    actual play), and during a JOYSTICK "fail" (which auto-restarts into a
    fresh round, still mid-play). A HARDWARE "fail" gets it too though
    (2026-09-17): that's the one that actually ends the game, paired with
    drivers/simon_engine.py::press()'s new buzzer+applause+immediate-
    music-restore on the same miss -- so the chase comes back on right
    away instead of waiting for score_review, rather than sitting through
    the whole loss sequence dark and silent. Confined to just "title" here
    regardless, since panels 3-6 are doing their own thing."""
    show_chase = (state.simon_phase in ("intro", "get_ready", "score_review")
                  or (state.simon_phase == "fail" and state.simon_source == "hardware"))
    if show_chase:
        _theater_chase_segment("title", now, 255, 255, 255)
    else:
        set_segment("title", 0, 0, 0)

    for i, name in enumerate(_SIMON_PANEL_SEGMENTS):
        if state.simon_active_pad == i:
            if state.simon_phase in ("playback", "input", "fail"):
                _trip_segment(name, i, now, state.simon_round)
            else:
                r, g, b = _steady_panel_color(config.SIMON_HW_COLOR_ORDER[i])
                set_segment(name, r, g, b)
        else:
            set_segment(name, 0, 0, 0)


def _in_get_ready_banner(now):
    """True during the pre-song "GET READY" announcement-banner window
    (drivers/announcement_engine.py sets these three fields for a
    sweeper-only track transition) -- the exact same condition graphics/
    matrix_canvas.py::_render_dj_mode already checks to show the "GET
    READY" banner text itself, so the marquee and matrix agree on the
    window."""
    return (state.announcement_banner_from <= now < state.announcement_banner_until
            and state.announcement_banner_text_override == "GET READY")


def _apply_get_ready_marquee(now):
    """Pre-song "GET READY" transition: keeps only the top ("title") strip
    doing its normal DJ-dance pattern -- panels 3-6 blackout, so the room's
    attention stays on the banner instead of four lower panels still
    dancing through an unrelated pattern underneath it (2026-09-16 operator
    feedback)."""
    _apply_dj_dance(now)
    _blackout_lower_panels()


def _apply_mystery_marquee(now):
    """"Who is this?" marquee choreography (2026-09-16): keeps the room's
    attention on the top strip through the whole question -- sparkle "Who
    is this?" -> solid "Is this:" -> panels 3-6 cascade in one at a time,
    in button color, staying dark until their own turn -- then holds that
    button-colored look (no DJ-dance patterns) all the way through grading
    and the post-answer artist/title blink on panels 1+2 (graphics/
    matrix_canvas.py's mystery_reveal branch). update() below only calls
    this while state.mystery_active is True; the instant that goes False
    (the blink window ending), update()'s own dispatch falls back to normal
    _apply_dj_dance() on its own -- no explicit hand-off needed here.

    Stage timing is owned by drivers/mystery_band_engine.py::reveal_stage(),
    shared with graphics/matrix_canvas.py's panel text so both stay in
    lockstep without duplicating the timing logic in either place."""
    from drivers import mystery_band_engine
    stage, revealed = mystery_band_engine.reveal_stage(now)
    if stage == "sparkle":
        _twinkle_segment("title", 255, 255, 255)
        _blackout_lower_panels()
    elif stage == "solid":
        set_segment("title", 255, 255, 255)
        _blackout_lower_panels()
    elif stage == "cascade":
        set_segment("title", 0, 0, 0)
        for i, name in enumerate(_SIMON_PANEL_SEGMENTS):
            if i < revealed:
                r, g, b = config.SIMON_HW_COLOR_RGB[config.SIMON_HW_COLOR_ORDER[i]]
                _theater_chase_segment(name, now, r, g, b)
            else:
                set_segment(name, 0, 0, 0)
    else:  # "done" -- resolved (graded or timed out), still mystery_active (blink hold)
        set_segment("title", 0, 0, 0)
        # All 4 panels finish their cascade together and keep chasing in
        # unison for _FINAL_REVEAL_HOLD_SECONDS -- computed directly rather
        # than read from `stage`/`revealed` above, since those already say
        # "done" the moment this branch is reached even if grading
        # happened early.
        reveal_complete_at = (state.mystery_started_at + config.MYSTERY_SPARKLE_SECONDS
                               + config.MYSTERY_SOLID_SECONDS + 4 * config.MYSTERY_CASCADE_STEP_SECONDS)
        still_chasing = now < reveal_complete_at + _FINAL_REVEAL_HOLD_SECONDS
        for i, name in enumerate(_SIMON_PANEL_SEGMENTS):
            if still_chasing:
                r, g, b = config.SIMON_HW_COLOR_RGB[config.SIMON_HW_COLOR_ORDER[i]]
                _theater_chase_segment(name, now, r, g, b)
            else:
                _apply_steady_jazz(name, i, now)


def _apply_question_marquee(now):
    """Per-option cascade for every Game Mode question (2026-09-18) -- same
    presentation shape as the Mystery Band teaser's own marquee
    choreography above (_apply_mystery_marquee's "cascade"/"done" stages),
    minus its sparkle/solid pre-roll (a normal question's own matrix text
    already displays immediately, there's no "Who is this?" suspense to
    build first) and driven by drivers/factoid_engine.py::
    question_reveal_count()'s much shorter timing instead of
    mystery_band_engine.py's own longer sequence. update() below only
    calls this while drivers/live_round_engine.py::is_round_active() is
    True; the instant a round is graded that goes False and the dispatch
    below falls back to _apply_game_chase() on its own -- no explicit
    hand-off needed here, same as the mystery branch's own comment notes."""
    from drivers import factoid_engine
    choices = state.factoid_choices
    revealed = factoid_engine.question_reveal_count(now)
    set_segment("title", 0, 0, 0)
    if revealed < len(choices):
        for i, name in enumerate(_SIMON_PANEL_SEGMENTS):
            if i < revealed:
                r, g, b = config.SIMON_HW_COLOR_RGB[config.SIMON_HW_COLOR_ORDER[i]]
                _theater_chase_segment(name, now, r, g, b)
            else:
                set_segment(name, 0, 0, 0)
    else:
        # All revealed panels finish their cascade together and keep
        # chasing in unison for _FINAL_REVEAL_HOLD_SECONDS -- same
        # treatment as _apply_mystery_marquee's "done" branch above.
        # Uses len(choices) (not a hardcoded 4) so True/False's 2-choice
        # timing lines up with question_reveal_count()'s own math.
        reveal_complete_at = state.factoid_question_started_at + len(choices) * config.QUESTION_CASCADE_STEP_SECONDS
        still_chasing = now < reveal_complete_at + _FINAL_REVEAL_HOLD_SECONDS
        for i, name in enumerate(_SIMON_PANEL_SEGMENTS):
            if i >= len(choices):
                set_segment(name, 0, 0, 0)
            elif still_chasing:
                r, g, b = config.SIMON_HW_COLOR_RGB[config.SIMON_HW_COLOR_ORDER[i]]
                _theater_chase_segment(name, now, r, g, b)
            else:
                _apply_steady_jazz(name, i, now)


def _apply_wrong_answer_marquee(now):
    """Wrong single-player grade (2026-09-18 redesign): blinks the correct
    answer's own panel segment on/off at 1Hz in its button color, other 3
    panels dark, for config.QUIZ_WRONG_ANSWER_HOLD_SECONDS -- same on/off
    cadence (elapsed seconds since state.quiz_graded_at, even second =
    on) as graphics/matrix_canvas.py::_draw_correct_answer_flash_text()
    uses for that same answer's matrix text, so panel text and marquee
    light blink in lockstep. Multiplayer grades never reach this (see
    this function's dispatch condition in update() below) -- multiplayer
    already has its own "who got it right" display with no single
    correct-answer panel to call out."""
    correct = state.factoid_correct_index
    on = (now - state.quiz_graded_at) % 2.0 < 1.0
    for i, name in enumerate(_SIMON_PANEL_SEGMENTS):
        if i == correct and on:
            r, g, b = _steady_panel_color(config.SIMON_HW_COLOR_ORDER[i])
            set_segment(name, r, g, b)
        else:
            set_segment(name, 0, 0, 0)


def _graded_round_correct():
    """Did the just-graded round end 'right'? Solo: the operator's pick was
    correct. With app players signed up (2026-09-29: this used to be excluded
    from every graded-round marquee look, dumping app rounds onto the old
    all-white chase): anyone right (state.round_any_correct, set by
    inputs/gamepad.py's multiplayer grade). A timeout always counts as the
    'reveal' look, same as before."""
    if state.round_timed_out:
        return True
    if state.quiz_players:
        return state.round_any_correct
    return state.quiz_selected_index == state.factoid_correct_index


def _graded_round_celebration():
    return (state.mode == state.MODE_GAME and state.quiz_locked
            and bool(state.factoid_choices) and _graded_round_correct())


def _graded_round_wrong_hold(now):
    return (state.mode == state.MODE_GAME and state.quiz_locked
            and bool(state.factoid_choices) and not _graded_round_correct()
            and now - state.quiz_graded_at < config.QUIZ_WRONG_ANSWER_HOLD_SECONDS)


def _graded_round_idle():
    """Graded round whose celebration / wrong-answer call-out has finished:
    the marquee goes DARK until the next question (2026-09-29, replaces the
    old all-white all-panel game chase for this window)."""
    return (state.mode == state.MODE_GAME and state.quiz_locked
            and bool(state.factoid_choices))


_last_marquee_branch = None


def _log_marquee_branch_transition(now):
    """TEMPORARY DIAGNOSTIC (2026-09-24): logs which branch of update()'s
    dispatch below actually fires, on transition only, to catch a reported
    bug -- panels 5/6 render blank text (correct for a 2-choice question)
    but their marquee segments stay lit as if still showing a 4-choice
    question. Mirrors the exact same condition order as the real dispatch
    below, purely for observation -- remove once the mechanism is
    confirmed and fixed."""
    global _last_marquee_branch
    if time.time() < _flash_until:
        branch = "flash"
    elif state.show_phase in ("setup", "countdown", "dark"):
        branch = f"show_phase={state.show_phase}"
    elif state.show_phase == "intro":
        branch = "intro"
    elif state.show_phase == "outro":
        branch = "outro"
    elif state.westminster_active:
        branch = "westminster"
    elif state.mode == state.MODE_SIMON:
        branch = "simon"
    elif _in_get_ready_banner(now):
        branch = "get_ready"
    elif state.mystery_active:
        branch = "mystery"
    elif _graded_round_celebration():
        branch = "correct_celebration"
    elif state.mode == state.MODE_DJ and not state.price_game_active:
        branch = "dj_dance"
    elif live_round_engine.is_round_active():
        branch = "question_marquee"
    elif _graded_round_wrong_hold(now):
        branch = "wrong_answer"
    elif _graded_round_idle():
        branch = "graded_dark"
    else:
        branch = "game_chase"

    if branch != _last_marquee_branch:
        print(f"[WLED DEBUG] marquee branch: {_last_marquee_branch} -> {branch} "
              f"(mode={state.mode!r}, mystery_active={state.mystery_active}, "
              f"choices={len(state.factoid_choices)}, quiz_locked={state.quiz_locked}, "
              f"round_active={live_round_engine.is_round_active()})")
        _last_marquee_branch = branch


def _apply_locate():
    """Admin "Show bounce centers" (config.MARQUEE_LOCATE_SECONDS): every
    segment dim white, its bounce pivot GREEN, the opposite center BLUE, its
    first wired LED RED then ORANGE, YELLOW (wiring direction) -- to check
    config.MARQUEE_SEGMENT_CENTERS and map the physical layout by eye."""
    fill(25, 25, 25)
    for name, seg in config.MARQUEE_SEGMENTS.items():
        n = seg["count"]
        pivot = config.MARQUEE_SEGMENT_CENTERS.get(name, 0) % n
        set_pixel(seg["start"] + 1, 255, 90, 0)
        set_pixel(seg["start"] + 2, 255, 220, 0)
        set_pixel(seg["start"], 255, 0, 0)
        set_pixel(seg["start"] + pivot, 0, 255, 0)
        set_pixel(seg["start"] + (pivot + n // 2) % n, 0, 0, 255)


def _build_bulb_xy():
    """Normalized (x, y) per LED, 0..1 across the board (y down), from
    config.MARQUEE_LAYOUT's photo-measured runs."""
    pts = [(0.0, 0.0)] * config.MARQUEE_TOTAL_LEDS
    raw = {}
    for name, runs in config.MARQUEE_LAYOUT.items():
        i = config.MARQUEE_SEGMENTS[name]["start"]
        for x0, y0, x1, y1, n in runs:
            for k in range(n):
                f = k / (n - 1) if n > 1 else 0.0
                raw[i] = (x0 + (x1 - x0) * f, y0 + (y1 - y0) * f)
                i += 1
    xs = [p[0] for p in raw.values()]
    ys = [p[1] for p in raw.values()]
    w, h = max(xs) - min(xs) or 1.0, max(ys) - min(ys) or 1.0
    for i, (x, y) in raw.items():
        if 0 <= i < len(pts):
            pts[i] = ((x - min(xs)) / w, (y - min(ys)) / h)
    return pts


_bulb_xy = _build_bulb_xy()
_trans_seen = 0.0
_trans_old = bytes(len(_pixels))


_trans_rnd = [0.0] * config.MARQUEE_TOTAL_LEDS  # pixel dissolve order, re-rolled per transition


def _apply_transition(now):
    """Song-change transition (config.MARQUEE_TRANSITION_* / TRANSITION_*),
    in step with graphics/matrix_canvas.py's board wipe. The order bulbs
    change in comes from drivers/transition_shapes.pos() for
    state.transition_style:
    1. clear: a white band sweeps through, dark behind it, the old song's
       last frame (frozen) ahead of it;
    2. hold dark until state.marquee_reveal_at (crossfade/VO);
    3. reveal: a band in the new marquee color sweeps through, the new look
       (already rendered into _pixels this frame) behind it, dark ahead --
       same moment/speed as the matrix reveal.
    "flash" pops the whole board instead (white fading to dark, then the
    new color fading into the new look); "sparkle_wipe" leaves a trail of
    white sparkles behind the reveal band."""
    style = state.transition_style
    start = state.marquee_transition_start
    t = now - start
    clear = config.MARQUEE_TRANSITION_SECONDS
    reveal_at = max(state.marquee_reveal_at, start + clear)
    rdur = config.TRANSITION_REVEAL_SECONDS
    band = config.MARQUEE_TRANSITION_BAND
    tail = config.TRANSITION_SPARKLE_TRAIL if style == "sparkle_wipe" else 0.0
    end = reveal_at + rdur * (1.0 + tail / (1.0 + 2 * band))
    if t < 0 or now >= end:
        return
    bri = max(0.0, min(1.0, state.marquee_brightness / 100.0))
    if clear <= t and now < reveal_at:
        fill(0, 0, 0)
        return
    revealing = t >= clear
    if revealing and state.marquee_off:
        return
    if revealing:
        p = (now - reveal_at) / rdur
        r, g, b = _resolve_marquee_color(config.DJ_COLOR_PALETTE[state.marquee_color_index])
        r, g, b = r * bri, g * bri, b * bri
    else:
        p = t / clear
        r = g = b = 255 * bri

    if style == "flash":
        k = max(0.0, 1.0 - p)  # the pop fades out over the phase
        for i in range(config.MARQUEE_TOTAL_LEDS):
            off = i * 3
            if revealing:  # new color melting into the new look
                nr, ng, nb = _pixels[off], _pixels[off + 1], _pixels[off + 2]
                set_pixel(i, nr + (r - nr) * k, ng + (g - ng) * k, nb + (b - nb) * k)
            else:          # white pop fading to dark
                set_pixel(i, r * k, g * k, b * k)
        return

    edge = -band + (1.0 + 2 * band) * p
    behind, ahead = ("new", "dark") if revealing else ("dark", "old")
    for i, (x, y) in enumerate(_bulb_xy):
        d = transition_shapes.pos(style, x, y, _trans_rnd[i])
        if d < edge - band:
            if revealing and tail and d >= edge - band - tail:
                # sparkle trail: dense right behind the band, thinning out
                closeness = 1.0 - (edge - band - d) / tail
                if random.random() < 0.45 * closeness:
                    v = random.uniform(0.5, 1.0) * 255 * bri
                    set_pixel(i, v, v, v)
                    continue
            zone = behind
        elif d < edge + band:
            set_pixel(i, r, g, b)
            continue
        else:
            zone = ahead
        if zone == "dark":
            set_pixel(i, 0, 0, 0)
        elif zone == "old":
            off = i * 3
            _pixels[off:off + 3] = _trans_old[off:off + 3]


def update(now):
    """Per-frame effects dispatch, called once per frame from main.py right
    before render(). Mirrors drivers/lighting_engine.py's show-phase
    dispatch shape, kept as its own independent branch (same modularity
    drivers/matrix_canvas.py's own state.show_phase handling already has)
    rather than folding into that module, since this is a separate
    hardware surface with its own transport.

    `not state.price_game_active` (rather than just checking state.mode)
    is what makes the marquee snap to the game chase the INSTANT Price
    Game starts -- see config.py's MARQUEE_GAME_* comment for why mode
    alone left a ~3.5s lag."""
    _log_marquee_branch_transition(now)
    global _trans_seen, _trans_old
    if state.marquee_transition_start != _trans_seen:
        _trans_seen = state.marquee_transition_start
        _trans_old = bytes(_pixels)  # last frame of the old song, for the wipe
        for i in range(len(_trans_rnd)):
            _trans_rnd[i] = random.random()
    if time.time() < state.marquee_locate_until:
        _apply_locate()
    elif time.time() < _flash_until:
        fill(*_flash_color)
    else:
        phase = state.show_phase
        if phase in ("setup", "countdown", "dark"):
            fill(0, 0, 0)
        elif phase == "intro":
            _apply_intro(now)
        elif phase == "outro":
            _apply_outro(now)
        elif state.show_spin_active:
            if state.show_wheel_started_at:
                _apply_wheel(now)
            else:
                fill(0, 0, 0)   # the song fading out before a mid-show spin
        elif state.westminster_active:
            _twinkle(255, 180, 0)
        elif state.mode == state.MODE_SIMON:
            _apply_simon(now)
        elif _in_get_ready_banner(now):
            _apply_get_ready_marquee(now)
        elif state.mystery_active:
            _apply_mystery_marquee(now)
        elif _graded_round_celebration():
            # Follow-up question graded CORRECT (any path -- panel button,
            # joystick, or a timeout that happened to land on the right
            # pick) or ended via TIMEOUT at all, right or wrong (2026-09-18,
            # generalized from a panel-button-correct-only flag): top strip
            # chases white with the correct answer's own text (graphics/
            # matrix_canvas.py's matching redesign). Panels 3-6 (2026-09-18
            # redesign): the correct answer's own segment stays lit,
            # chasing in ITS OWN button color instead of going dark with
            # the rest, so the room can see which square it was, not just
            # read the bare text up top; the other three go dark.
            _theater_chase_segment("title", now, 255, 255, 255)
            correct = state.factoid_correct_index
            for i, name in enumerate(_SIMON_PANEL_SEGMENTS):
                if i == correct:
                    r, g, b = config.SIMON_HW_COLOR_RGB[config.SIMON_HW_COLOR_ORDER[i]]
                    _theater_chase_segment(name, now, r, g, b)
                else:
                    set_segment(name, 0, 0, 0)
        elif state.mode == state.MODE_DJ and not state.price_game_active:
            story_pos = oz_story.position()
            if story_pos is not None:
                _apply_story(now, story_pos)
            else:
                _apply_dj_dance(now)
            if state.blank_lower_marquees and story_pos is None:
                _blackout_lower_panels()
        elif live_round_engine.is_round_active():
            # Ordinary Game Mode question, still live/unlocked -- same
            # condition that already gates whether the panel buttons
            # themselves do anything (drivers/simon_engine.py::
            # poll_hardware()) and whether their LEDs light up (inputs/
            # gamepad.py::_sync_panel_leds()). Placed after the
            # state.mystery_active check above so the mystery teaser's own
            # choreography keeps taking priority during that window (this
            # condition is also true then, but never reached).
            _apply_question_marquee(now)
        elif _graded_round_wrong_hold(now):
            # Single-player wrong grade (2026-09-18): call out the correct
            # answer's panel with a 1Hz blink instead of falling through to
            # the generic white game chase below -- see graphics/
            # matrix_canvas.py's matching matrix-text redesign for why.
            _apply_wrong_answer_marquee(now)
        elif _graded_round_idle():
            fill(0, 0, 0)  # graded round, call-out over: dark until the next question
        else:
            _apply_game_chase(now)
    if config.MARQUEE_TRANSITION_ENABLED and time.time() >= state.marquee_locate_until:
        _apply_transition(time.time())
    if state.lights_out:
        fill(0, 0, 0)  # admin blackout / after-show dark bed (drivers/blackout_engine.py)
    render()


maybe_reresolve()
