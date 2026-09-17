"""drivers/accent_engine.py
Driver for the third ESP32 (stock WLED, 120-lamp "outlines" accent
strip), rebuilt 2026-09-15 against the replacement board -- see the
long comment above config.ACCENT_WLED_MAC for the full history of why
the original WeGoIOT board was abandoned.

This board enumerates on the exact same Silicon Labs CP2102 VID/PID
(and blank embedded serial number) as the matrix board (drivers/
led_bridge.py) and marquee board (drivers/wled_engine.py). Those two
tell each other apart via drivers/serial_ports.py's heartbeat
elimination, but that scheme only ever handled two indistinguishable
candidates -- with three, it briefly caused a real main-loop stall
(2026-09-14) bouncing between them. Instead of extending that
elimination scheme, this module positively identifies its own board:
it asks each unclaimed CP2102 candidate for its WLED info ({"v":true}
over serial) and checks the returned "mac" field against
config.ACCENT_WLED_MAC, burned into this specific chip at manufacture.
Any candidate that doesn't match is left untouched (closed again
immediately) for led_bridge.py/wled_engine.py to sort out as before.

The whole scan-probe-identify sequence runs on a background thread,
not the shared main loop -- probing means a blocking read waiting for
WLED's JSON reply, and doing that inline in main.py's frame loop would
be exactly the class of stall this design is meant to avoid.

No-op everywhere (with a one-line log at import) if pyserial isn't
available, so this stays safe to import from the Windows dev machine.
"""
import json
import threading
import time

import config
from drivers import color_utils, serial_ports
from state import state

try:
    import serial
    import serial.tools.list_ports
    _AVAILABLE = True
except ImportError:
    serial = None
    _AVAILABLE = False

_link = None
_lock = threading.Lock()
_effect_index = 0
_scan_thread = None
_stop_scanning = threading.Event()

# Serial write hand-off to _sender_loop's dedicated thread (2026-09-18) --
# see that function's docstring for why _send() can't write inline on
# whatever thread calls it. Same shape as drivers/wled_engine.py's own
# _serial_lock/_serial_send_ready/_serial_pending.
_send_lock = threading.Lock()
_send_ready = threading.Event()
_send_pending = None  # (serial.Serial instance, frame_bytes) tuple, or None once consumed

_PROBE_SETTLE_S = 0.3
_PROBE_REPLY_WAIT_S = 0.5
_RESCAN_INTERVAL_S = 5.0

if not _AVAILABLE:
    print("[ACCENT] pyserial not available on this host -- outline-strip control will report unavailable.")


def available():
    return _link is not None


def init():
    """Starts the background identify/connect thread -- call once from
    main.py at startup. Safe to call on a host without pyserial, or if
    already running (no-ops). Keeps retrying on its own interval if the
    board isn't found yet or gets unplugged later.

    Re-enabled 2026-09-15 (second time) now that drivers/wled_engine.py
    also positively verifies its own board by MAC (config.MARQUEE_WLED_MAC)
    instead of blindly grabbing the first unclaimed candidate. That gap
    was what broke this the first time it was enabled today: with this
    module correctly claiming its board by MAC and led_bridge.py
    claiming its board by heartbeat, wled_engine.py's old blind guess
    had nowhere else to land except the real matrix board, and its
    since-disabled self-correction never caught the mistake -- see
    wled_engine.py's git history/comments for the full incident. All
    three modules now positively verify their own board by a different
    method each (heartbeat / MAC / MAC), so none of them should ever be
    able to squat on either of the others' ports again."""
    global _scan_thread
    if not _AVAILABLE or _scan_thread is not None:
        return
    _stop_scanning.clear()
    _scan_thread = threading.Thread(target=_scan_loop, daemon=True)
    _scan_thread.start()


def cleanup():
    """Called from main.py's shutdown teardown -- stops the scan thread
    and releases the port."""
    global _link, _scan_thread
    _stop_scanning.set()
    with _lock:
        if _link is not None:
            serial_ports.release(_link.port)
            _link.close()
            _link = None
    _scan_thread = None


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


_manual_override = None  # None (auto-sync) or "top"/"all" while a manual movie-chase test is forced
_last_sent_look = None  # (fx, r, g, b, sx) tuple last actually sent, so sync_to_show_state() only sends on real change

# Btn9 feature-select confirmation flash (inputs/gamepad.py::
# handle_feature_select) and any future win/loss-style trigger -- mirrors
# drivers/wled_engine.py's own _flash_color/_flash_until pair, see flash()
# below for why this board doesn't need a per-frame repaint like that one.
_flash_color = None
_flash_until = 0.0


def _resolve_dj_color():
    """Same color state.accent_color_index resolves to everywhere else on
    the rig (drivers/lighting_engine.py::_current_color()'s own DMX-scoped
    counterpart), with the three dedicated-emitter looks (white/amber/uv
    lamp) substituted for a visible RGB approximation -- see
    config.ACCENT_DEDICATED_EMITTER_RGB's comment for why those can't just
    be used as-is."""
    color = config.DJ_COLOR_PALETTE[state.accent_color_index % len(config.DJ_COLOR_PALETTE)]
    if color.white or color.amber or color.uv:
        return config.ACCENT_DEDICATED_EMITTER_RGB.get(color.name, (255, 255, 255))
    return (color[0], color[1], color[2])


def _target_look():
    """Returns the (fx, r, g, b, sx) this board should currently be
    showing, mirroring drivers/wled_engine.py::update()'s own show-phase
    dispatch so the accent strip reads as part of the same show rather
    than an independently-run afterthought. Simplified relative to that
    dispatch (no show-phase/Simon/Westminster branches yet -- see this
    function's call site for why) since the immediate ask (2026-09-15) was
    per-song DJ color/theme matching plus the Price Game white-out
    specifically.

    When state.accent_sound_enabled is True, r/g/b come back None as a
    sentinel -- WLED's own AudioReactive effect (config.
    ACCENT_AUDIOREACTIVE_FX_ID) owns the visual once selected, so
    sync_to_show_state() sends only the fx pick once rather than fighting
    it with a col/sx override every frame. Real-world payoff depends on
    this board's analog-input hardware actually being wired up (2026-09-17
    session, GPIO36) -- until then this selects the effect but it has no
    live signal to react to."""
    if state.mode != state.MODE_DJ or state.price_game_active:
        # Same condition drivers/wled_engine.py::update() uses for its own
        # game-chase white pattern -- covers Price Game's white-lights
        # window and Quiz/other non-DJ modes with the same simple white
        # solid look, rather than inventing a second white-trigger rule.
        return (config.ACCENT_FX_SOLID, 255, 255, 255, state.accent_speed)  # Solid, white
    if state.accent_sound_enabled:
        return (config.ACCENT_AUDIOREACTIVE_FX_ID, None, None, None, None)
    # state.accent_theme_index is a direct WLED effect ID (index into
    # config.ACCENT_EFFECT_NAMES) since 2026-09-18 -- no longer routed
    # through a curated subset mapping, see that list's header comment.
    fx = (state.accent_theme_index if 0 <= state.accent_theme_index < len(config.ACCENT_EFFECT_NAMES)
          else config.ACCENT_FX_SOLID)
    r, g, b = _resolve_dj_color()
    gradient_mode = state.accent_gradient_mode
    if gradient_mode == "rainbow":
        fx = config.ACCENT_FX_RAINBOW
    elif gradient_mode in ("adjacent", "complementary"):
        degrees = 30 if gradient_mode == "adjacent" else 180
        r, g, b = color_utils.hue_shift(r, g, b, degrees)
    return (fx, r, g, b, state.accent_speed)


def sync_to_show_state():
    """Call once per frame from main.py (mirrors wled_engine.update()'s own
    per-frame call shape) -- cheap no-op unless the target look actually
    changed since the last call, so this doesn't spam the board with
    redundant JSON every frame the way a raw pixel push would need to.
    Does nothing while a manual movie-chase test (set_movie_chase()) is
    active, or while a flash() confirmation is still showing; resume_auto_
    sync() (or another set_movie_chase() call) clears the former, the
    latter clears on its own once _flash_until passes."""
    global _last_sent_look
    if time.time() < _flash_until:
        return
    if _manual_override is not None or not available():
        return
    look = _target_look()
    if look == _last_sent_look:
        return
    fx, r, g, b, sx = look
    if r is None:  # accent_sound_enabled sentinel -- select the effect once, nothing else
        _send({"seg": {"fx": fx}})
    else:
        _send({"seg": {"fx": fx, "col": [[r, g, b]], "sx": sx}})
    _last_sent_look = look


def flash(r, g, b, duration=None):
    """Brief solid-color override on the outline strip, taking priority
    over sync_to_show_state()'s normal per-frame polling -- mirrors
    drivers/wled_engine.py's flash() but for this board's JSON/effect-
    driven control instead of a raw pixel push. Unlike that one, this
    board doesn't need a per-frame repaint while flashing (WLED holds a
    Solid color on its own once set), so one send on trigger is enough;
    the expiry just marks when sync_to_show_state() should resume normal
    polling. Self-expiring; nothing needs to explicitly turn it back off."""
    global _flash_color, _flash_until, _last_sent_look
    if not available():
        return
    _flash_color = (r, g, b)
    _flash_until = time.time() + (duration if duration is not None else config.DJ_FEATURE_FLASH_SECONDS)
    _send({"seg": {"fx": config.ACCENT_FX_SOLID, "col": [[r, g, b]]}})
    _last_sent_look = None  # force a real resend once the flash expires, rather than trusting a stale comparison


def set_movie_chase(all_panels):
    """Manual bring-up/artist-testing control (2026-09-15) -- forces WLED's
    built-in Theater Chase effect (config.ACCENT_FX_THEATER), the same
    "movie marquee" bulb-chase look as drivers/wled_engine.py's Simon
    top-strip pattern, either across the whole strip or just
    config.ACCENT_TOP_SEGMENT_LED_COUNT pixels of it. Suspends
    sync_to_show_state() until resume_auto_sync() is called -- this is a
    deliberate override for evaluating the effect, not part of the normal
    per-song rotation (see config.ACCENT_TOP_SEGMENT_LED_COUNT's comment:
    the top/all split is a guess pending the strip's real segment layout,
    so this is also the way to actually go look at it and confirm).
    Returns False if the link isn't available."""
    global _manual_override, _last_sent_look
    if not available():
        return False
    _manual_override = "top" if all_panels is False else "all"
    if all_panels:
        _send({"seg": {"id": 0, "start": 0, "stop": config.ACCENT_TOTAL_LEDS,
                        "fx": config.ACCENT_FX_THEATER, "col": [[255, 255, 255]]}})
    else:
        top = config.ACCENT_TOP_SEGMENT_LED_COUNT
        _send({"seg": [
            {"id": 0, "start": 0, "stop": top,
             "fx": config.ACCENT_FX_THEATER, "col": [[255, 255, 255]]},
            {"id": 1, "start": top, "stop": config.ACCENT_TOTAL_LEDS,
             "fx": config.ACCENT_FX_SOLID, "on": False},
        ]})
    _last_sent_look = None  # force sync_to_show_state() to re-send once resumed, rather than trusting a stale comparison
    return True


def resume_auto_sync():
    """Clears set_movie_chase()'s manual override so sync_to_show_state()
    resumes following the current DJ color/theme (or Price Game white-out)
    on the very next frame."""
    global _manual_override
    _manual_override = None


def _send(payload):
    """Hands the JSON payload off to _sender_loop's dedicated thread rather
    than writing here directly -- see that function's docstring for why.
    Binds the frame to whichever specific link instance is current right
    now (read under _lock, same convention every other _link access in
    this module uses), so a write that's still stuck blocking when a
    reconnect later replaces _link can't be confused with the new
    connection -- same pattern drivers/wled_engine.py's own serial sender
    already uses."""
    global _send_pending
    with _lock:
        link = _link
    if link is None:
        return
    frame = (json.dumps(payload) + "\n").encode("utf-8")
    with _send_lock:
        _send_pending = (link, frame)
    _send_ready.set()


def _fail_send_link(link, error):
    """Shared teardown for a serial link that just failed a write, called
    from _sender_loop's own thread -- mirrors drivers/wled_engine.py's own
    _fail_serial_link(). Guards against clobbering a *different*, newer
    connection: _try_find_and_claim() runs on the scan thread and could in
    principle reconnect while a stale write from the old link is still
    unwinding here (e.g. it was blocked for a while before finally
    raising) -- only clear the shared _link if it's still pointing at the
    exact object that just failed."""
    global _link
    print(f"[ACCENT] Write failed, dropping link: {error}")
    device = link.port
    try:
        link.close()
    except Exception:
        pass
    serial_ports.release(device)
    with _lock:
        if _link is link:
            _link = None


def _sender_loop():
    """Runs forever on its own daemon thread, started once at import (only
    if pyserial is actually available -- see the guarded start call below).
    Moves the actual blocking serial write off whatever thread calls
    _send() -- critically, that includes FastAPI's request-handling
    threadpool (web/remote_server.py's /api/accent/* routes call _send()
    directly, as does the per-song sync from the main show loop) as well
    as the main loop's own per-frame sync_to_show_state() call.

    Added 2026-09-18 after a live report of the admin/player web panels
    going intermittently unresponsive during gameplay while the show
    itself kept running -- startup.log showed this board's serial
    connection reconnecting unusually often around the same time. Without
    a dedicated sender thread, a write that blocks in the kernel USB-
    serial driver (confirmed possible on this exact class of link --
    drivers/wled_engine.py's own history documents two full-minute
    main-loop freezes from precisely this gap before it got the same fix
    applied here) ties up whichever thread called _send() indefinitely;
    enough FastAPI worker threads stuck that way starves the whole web
    server even though uvicorn itself is still running. Fire-and-forget
    UDP-style tolerance isn't available here (this is a reliable serial
    link, not UDP), but the same "a dropped/delayed frame just gets
    superseded by the next one" reasoning still applies -- nothing here
    needs an ack."""
    global _send_pending
    while True:
        _send_ready.wait()
        with _send_lock:
            pending = _send_pending
            _send_pending = None
            _send_ready.clear()
        if pending is None:
            continue
        link, frame = pending
        try:
            link.write(frame)
        except (OSError, serial.SerialException) as e:
            _fail_send_link(link, e)


if _AVAILABLE:
    threading.Thread(target=_sender_loop, daemon=True, name="accent-serial-sender").start()


def _scan_loop():
    while not _stop_scanning.is_set():
        if _link is None:
            _try_find_and_claim()
        _stop_scanning.wait(_RESCAN_INTERVAL_S)


def _try_find_and_claim():
    global _link
    for port in serial.tools.list_ports.comports():
        if (port.vid, port.pid) not in config.ESP32_USB_SERIAL_VID_PIDS:
            continue
        if serial_ports.held_by(port.device) is not None:
            continue
        # Claim BEFORE opening -- see init()'s docstring for why this
        # ordering is the actual fix, not just a style choice.
        serial_ports.hold(port.device, "accent_engine")
        link = _open_and_probe(port.device)
        if link is None:
            serial_ports.release(port.device)
            continue
        with _lock:
            _link = link
        print(f"[ACCENT] Connected over serial on {port.device} (confirmed by MAC).")
        return


def _open_and_probe(device):
    """Opens `device` once, asks WLED for its info, and returns the
    still-open link if info.mac matches config.ACCENT_WLED_MAC --
    otherwise closes it and returns None. Never leaves a port open
    when it isn't actually this board; the caller releases the
    serial_ports hold in that case."""
    try:
        link = serial.Serial(device, baudrate=config.ACCENT_SERIAL_BAUD,
                              timeout=_PROBE_REPLY_WAIT_S, write_timeout=0.5,
                              dsrdtr=False, rtscts=False)
        link.dtr = False
        link.rts = False
        time.sleep(_PROBE_SETTLE_S)
        link.reset_input_buffer()
        link.write(b'{"v":true}\n')
        time.sleep(_PROBE_REPLY_WAIT_S)
        data = link.read(4096)
        mac = None
        text = data.decode("utf-8", errors="replace").strip()
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("{"):
                mac = json.loads(line).get("info", {}).get("mac")
                break
        if mac == config.ACCENT_WLED_MAC:
            link.timeout = 1  # back to a normal read timeout for ongoing use, was _PROBE_REPLY_WAIT_S just for the probe
            return link
        link.close()
        return None
    except (OSError, serial.SerialException) as e:
        print(f"[ACCENT] Probe of {device} failed: {e}")
        return None
    except Exception:
        return None
