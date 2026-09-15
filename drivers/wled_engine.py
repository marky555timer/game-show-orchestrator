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
import math
import random
import socket
import threading
import time

import serial
import serial.tools.list_ports

import config
from drivers import serial_ports
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
    """VID/PID auto-discovery, coordinated with led_bridge.py over
    drivers/serial_ports.py since both boards can enumerate on the same
    USB-UART chip (see that module's docstring for the full picture).
    Skips any port already held by another module (not just led_bridge.py
    by name) -- a third CH340-identity device (e.g. a USB relay board) can
    now share config.ESP32_USB_SERIAL_VID_PIDS too and needs the same
    protection.

    If led_bridge.py has ever positively ruled out a port as its own
    board, that's a direct answer -- take it immediately rather than
    falling through to a plain VID/PID scan, which (with nothing held on
    either port) could just as easily re-pick the wrong one by enumeration
    order and bounce the two modules past each other indefinitely."""
    rejected = serial_ports.rejected_port()
    if rejected is not None:
        for port in serial.tools.list_ports.comports():
            if port.device == rejected:
                return rejected
    for port in serial.tools.list_ports.comports():
        if ((port.vid, port.pid) in config.ESP32_USB_SERIAL_VID_PIDS
                and serial_ports.held_by(port.device) is None):
            return port.device
    return None


def _try_connect_serial():
    global _serial_link, _last_serial_scan_time, _serial_connect_time
    _last_serial_scan_time = time.monotonic()
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
        _serial_connect_time = time.monotonic()
        serial_ports.hold(device, "wled_engine")
        print(f"[WLED] Connected over serial on {device}.")
    except Exception as e:
        print(f"[WLED] Could not open {device}: {e}")


def _serial_ready():
    """No heartbeat to check (see module docstring) -- just "port open and
    past WLED's own boot window"."""
    return (_serial_link is not None
            and time.monotonic() - _serial_connect_time >= _SERIAL_BOOT_SETTLE_S)


def _reconcile_with_led_bridge():
    """led_bridge.py can positively verify its board (Display.ino writes
    back a serial heartbeat); this module can't -- stock WLED has no
    equivalent, so _find_esp32_port() above is always a guess whenever two
    boards share a VID/PID. If led_bridge.py has since ruled out a
    specific port, that's positive proof (only two boards match
    config.ESP32_USB_SERIAL_VID_PIDS on this rig) that port is actually
    this module's -- and if we're connected to a *different* one, we
    guessed wrong and are squatting on led_bridge.py's own board. Drop it
    immediately rather than waiting for the next scan interval, since
    led_bridge.py just retries blindly and can't make progress while we're
    holding what it actually needs."""
    global _serial_link
    rejected = serial_ports.rejected_port()
    if rejected is None or _serial_link is None or _serial_link.port == rejected:
        return
    device = _serial_link.port
    print(f"[WLED] {device} is led_bridge's board, not ours -- releasing it.")
    try:
        _serial_link.close()
    except Exception:
        pass
    serial_ports.release(device)
    _serial_link = None


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
    """Shared teardown for a serial link that just failed a write, called
    from _serial_sender_loop's own thread. Guards against clobbering a
    *different*, newer connection: _try_connect_serial() runs on the main
    thread and could in principle reconnect while a stale write from the
    old link is still unwinding here (e.g. it was blocked for a while
    before finally raising) -- only clear the shared _serial_link if it's
    still pointing at the exact object that just failed."""
    global _serial_link
    print(f"[WLED] Serial write failed ({error}); falling back to WiFi DDP.")
    device = link.port
    try:
        link.close()
    except Exception:
        pass
    serial_ports.release(device)
    if _serial_link is link:
        _serial_link = None


def _serial_sender_loop():
    """Runs forever on its own daemon thread, started once at import -- same
    pattern as _ddp_sender_loop above, just for the serial write instead of
    the UDP send. See _serial_pending's declaration for why this exists."""
    global _serial_pending
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
        except Exception as e:
            _fail_serial_link(link, e)


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
        _reconcile_with_led_bridge()
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


def _hue_shift(r, g, b, degrees):
    """Rotates an RGB color's hue by `degrees` (0-360), holding saturation
    and value fixed -- used for color-complement (180 deg) and adjacent-hue
    (small offsets) treatments, a much cleaner "opposite/neighbor color"
    than naive RGB math (255-r etc.) gives."""
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    h = (h + degrees / 360.0) % 1.0
    r2, g2, b2 = colorsys.hsv_to_rgb(h, s, v)
    return r2 * 255, g2 * 255, b2 * 255


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


def _twinkle(r, g, b):
    """Per-pixel random brightness scaling, independently re-rolled every
    call -- looks like a sparkle regardless of physical wiring direction
    (unlike a moving chase, which needs a confirmed pixel order to look
    right; see config.py's note that panel3's loop direction isn't
    confirmed yet)."""
    for i in range(config.MARQUEE_TOTAL_LEDS):
        level = random.uniform(0.15, 1.0)
        set_pixel(i, r * level, g * level, b * level)


def _apply_intro(now):
    elapsed = now - state.show_phase_started_at
    if elapsed < config.SHOW_INTRO_DMX_FLASH_AT_SECONDS:
        _twinkle(255, 255, 255)
        return
    beat = elapsed - config.SHOW_INTRO_DMX_FLASH_AT_SECONDS
    f = config.SHOW_INTRO_DMX_FLASH_SECONDS
    if beat < f:
        fill(255, 255, 255)
    elif beat < f * 2:
        fill(0, 0, 0)
    elif beat < f * 3:
        fill(255, 255, 255)
    else:
        # Matches the DMX rig's post-flash green marquee chase's color
        # (drivers/lighting_engine.py::_render_show_intro_dmx) -- solid
        # rather than a moving chase for the same reason _twinkle() above
        # avoids one.
        r, g, b = _resolve_marquee_color(config.DJ_COLOR_PALETTE[3])
        fill(r, g, b)


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
    brighter sparkle pixels re-rolled every frame."""
    for i in range(config.MARQUEE_TOTAL_LEDS):
        level = random.uniform(0.6, 1.0) if random.random() < 0.08 else 0.25
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
    """Pattern 4: a comet in the current color chases each segment's own
    loop, its fading tail blending into the color's complement (opposite
    hue) instead of just dimming to black."""
    cr, cg, cb = _hue_shift(r, g, b, 180)
    tail = 7
    for seg in config.MARQUEE_SEGMENTS.values():
        count = seg["count"]
        pos = (now / config.MARQUEE_DJ_CHASE_LAP_SECONDS * count) % count
        for offset in range(count):
            dist = (offset - pos) % count
            if dist < tail:
                t = dist / tail
                lr, lg, lb = r * (1 - t) + cr * t, g * (1 - t) + cg * t, b * (1 - t) + cb * t
                level = 1.0 - t * 0.5
                set_pixel(_segment_pixel_index(seg, offset), lr * level, lg * level, lb * level)
            else:
                set_pixel(_segment_pixel_index(seg, offset), 0, 0, 0)


def _dj_pattern_rainbow_chase(now, r, g, b):
    """Pattern 5: a hue-cycling rainbow travels around each segment's own
    loop -- ignores the current DJ color entirely, always vibrant. The
    "granular detail" showcase pattern."""
    for seg in config.MARQUEE_SEGMENTS.values():
        count = seg["count"]
        for offset in range(count):
            hue = (offset / count + now * config.MARQUEE_DJ_RAINBOW_SPEED) % 1.0
            pr, pg, pb = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
            set_pixel(_segment_pixel_index(seg, offset), pr * 255, pg * 255, pb * 255)


def _dj_pattern_bounce_comet(now, r, g, b):
    """Pattern 6: a comet sweeps back and forth (not looping) within each
    segment, current color at the head fading into an adjacent hue (a
    small +30 deg shift, not a full complement) at the tail."""
    ar, ag, ab = _hue_shift(r, g, b, 30)
    t = (now % config.MARQUEE_DJ_BOUNCE_PERIOD_SECONDS) / config.MARQUEE_DJ_BOUNCE_PERIOD_SECONDS
    triangle = t * 2 if t < 0.5 else 2 - t * 2  # 0 -> 1 -> 0
    tail = 5
    for seg in config.MARQUEE_SEGMENTS.values():
        pos = triangle * (seg["count"] - 1)
        for offset in range(seg["count"]):
            dist = abs(offset - pos)
            if dist < tail:
                t2 = dist / tail
                lr, lg, lb = r * (1 - t2) + ar * t2, g * (1 - t2) + ag * t2, b * (1 - t2) + ab * t2
                level = 1.0 - t2 * 0.6
                set_pixel(_segment_pixel_index(seg, offset), lr * level, lg * level, lb * level)
            else:
                set_pixel(_segment_pixel_index(seg, offset), 0, 0, 0)


def _dj_pattern_confetti(now, r, g, b):
    """Pattern 7: random sparks in the current color or its complement,
    decaying into the existing frame rather than a hard clear each tick --
    a trailing confetti/glitter look. The only pattern that reads directly
    from/writes directly to _pixels instead of going through set_pixel()
    for every LED every frame, since the decay needs last frame's values."""
    cr, cg, cb = _hue_shift(r, g, b, 180)
    for i in range(config.MARQUEE_TOTAL_LEDS):
        if random.random() < 0.06:
            pr, pg, pb = (cr, cg, cb) if random.random() < 0.5 else (r, g, b)
            set_pixel(i, pr, pg, pb)
        else:
            off = i * 3
            _pixels[off] = int(_pixels[off] * 0.85)
            _pixels[off + 1] = int(_pixels[off + 1] * 0.85)
            _pixels[off + 2] = int(_pixels[off + 2] * 0.85)


_DJ_PATTERNS = [
    _dj_pattern_breathe, _dj_pattern_wave, _dj_pattern_twinkle_base, _dj_pattern_call_response,
    _dj_pattern_complement_chase, _dj_pattern_rainbow_chase, _dj_pattern_bounce_comet, _dj_pattern_confetti,
]


def _apply_dj_dance(now):
    r, g, b = _resolve_marquee_color(config.DJ_COLOR_PALETTE[state.dj_color_index])
    pattern_fn = _DJ_PATTERNS[state.dj_theme_index % len(_DJ_PATTERNS)]
    pattern_fn(now, r, g, b)


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
    "input"/"fail" (2026-09-14, operator feedback that it was distracting
    during actual play). Confined to just "title" here regardless, since
    panels 3-6 are doing their own thing."""
    seg = config.MARQUEE_SEGMENTS["title"]
    count = seg["count"]
    if state.simon_phase in ("intro", "get_ready", "score_review"):
        spacing = config.SIMON_MARQUEE_CHASE_SPACING
        step = int(now / config.SIMON_MARQUEE_CHASE_STEP_SECONDS) % spacing
        for offset in range(count):
            if (offset - step) % spacing == 0:
                set_pixel(_segment_pixel_index(seg, offset), 255, 255, 255)
            else:
                set_pixel(_segment_pixel_index(seg, offset), 0, 0, 0)
    else:
        for offset in range(count):
            set_pixel(_segment_pixel_index(seg, offset), 0, 0, 0)

    for i, name in enumerate(_SIMON_PANEL_SEGMENTS):
        if state.simon_active_pad == i:
            r, g, b = config.SIMON_HW_COLOR_RGB[config.SIMON_HW_COLOR_ORDER[i]]
            set_segment(name, r, g, b)
        else:
            set_segment(name, 0, 0, 0)


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
    if time.time() < _flash_until:
        fill(*_flash_color)
    else:
        phase = state.show_phase
        if phase in ("setup", "countdown", "dark"):
            fill(0, 0, 0)
        elif phase == "intro":
            _apply_intro(now)
        elif phase == "outro":
            _apply_outro(now)
        elif state.westminster_active:
            _twinkle(255, 180, 0)
        elif state.mode == state.MODE_SIMON:
            _apply_simon(now)
        elif state.mode == state.MODE_DJ and not state.price_game_active:
            _apply_dj_dance(now)
        else:
            _apply_game_chase(now)
    render()


maybe_reresolve()
