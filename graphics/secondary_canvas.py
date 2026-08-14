# graphics/secondary_canvas.py
"""Secondary HDMI fullscreen fallback canvas (Feature Update): an
independent second pygame window, dedicated to a scaled, high-visibility
render of the same LED matrix raster graphics/matrix_canvas.py already
builds into `matrix_surface` every frame -- for a show where the physical
ESP32 matrix panels aren't available and an external monitor/projector has
to stand in.

pygame's classic pygame.display.set_mode() (what matrix_canvas.py uses for
the primary dev-overlay simulator) only ever owns a single OS window.
pygame-ce's SDL2-backed pygame._sdl2.video.Window class supports true
multi-window sessions on top of the same already-initialized video
subsystem, so this module opens a second Window rather than trying to
replace or share the primary one.

Module owns its own init()/render()/handle_window_close() lifecycle, same
"module owns its own window/popup behavior" split web/qr_popup.py and
graphics/overlay_panel.py already use -- main.py calls init() once at
startup, graphics/matrix_canvas.py::render_led_grid() calls render() once
per frame right alongside the primary canvas's own draw, and
inputs/gamepad.py forwards WINDOWCLOSE events here so closing this window
(only reachable in the single-monitor windowed fallback -- the auto-placed
fullscreen window is borderless and has no close button) can never be
mistaken for the primary window's QUIT and take down the whole app.
"""
import pygame
from pygame._sdl2.video import Window

import config
from config import MATRIX_WIDTH, MATRIX_HEIGHT, PANELS, RED_OFF, BLACK

_window = None
_visible = True


def _get_monitor_rects():
    """[(left, top, width, height), ...] in absolute virtual-desktop pixel
    coordinates, primary monitor first -- pygame.display itself has no API
    for per-monitor *position* (only get_desktop_sizes(), which is just a
    list of sizes), so this reaches for win32api the same way
    matrix_canvas.py::_pin_window_always_on_top() already does for window
    Z-order. Falls back to a single (0, 0, w, h) entry sized off
    pygame.display.get_desktop_sizes() if pywin32 isn't available/fails --
    correct on a single-monitor box, and on a multi-monitor box just means
    auto-placement degrades to the single-monitor windowed fallback below."""
    try:
        import win32api
        monitors = win32api.EnumDisplayMonitors()
        entries = []
        for hmon, _hdc, _rect in monitors:
            info = win32api.GetMonitorInfo(hmon)
            is_primary = bool(info.get("Flags", 0) & 1)  # MONITORINFOF_PRIMARY
            left, top, right, bottom = info["Monitor"]
            entries.append((not is_primary, left, top, right - left, bottom - top))
        entries.sort()
        return [(left, top, w, h) for _sort_key, left, top, w, h in entries]
    except Exception as e:
        print(f"[SECONDARY CANVAS] Could not enumerate monitors via win32api ({e}) -- "
              f"falling back to pygame.display.get_desktop_sizes().")
        return [(0, 0, w, h) for w, h in pygame.display.get_desktop_sizes()]


def init():
    """Detects connected monitors and opens the fallback window per
    config.SECONDARY_CANVAS_*. Must run after pygame.display.set_mode() has
    already created the primary window (graphics/matrix_canvas.py does this
    at import time) -- the SDL video subsystem has to already be live for a
    second Window() to attach to it."""
    global _window

    if not config.SECONDARY_CANVAS_ENABLED:
        print("[SECONDARY CANVAS] Disabled via config.SECONDARY_CANVAS_ENABLED.")
        return

    monitors = _get_monitor_rects()

    if len(monitors) < 2:
        if not config.SECONDARY_CANVAS_SHOW_ON_SINGLE_MONITOR:
            print(f"[SECONDARY CANVAS] Only {len(monitors)} monitor detected -- fallback canvas stays "
                  f"off (flip config.SECONDARY_CANVAS_SHOW_ON_SINGLE_MONITOR True to force a windowed "
                  f"copy on this single monitor).")
            return
        w, h = config.SECONDARY_CANVAS_WINDOWED_SIZE
        _window = Window(config.SECONDARY_CANVAS_WINDOW_TITLE, size=(w, h),
                          position=(80, 80), resizable=True, borderless=False)
        print(f"[SECONDARY CANVAS] Only one monitor detected -- opened a movable/resizable "
              f"{w}x{h} windowed fallback on it instead of auto-fullscreen.")
    else:
        # monitors[0] is the primary (matches matrix_canvas.py's simulator
        # window, pinned at (0,0) on the primary via SDL_VIDEO_WINDOW_POS) --
        # monitors[1] is "Index 1", the first non-primary display, per spec.
        left, top, w, h = monitors[1]
        _window = Window(config.SECONDARY_CANVAS_WINDOW_TITLE, size=(w, h),
                          position=(left, top), borderless=True)
        _window.always_on_top = True
        print(f"[SECONDARY CANVAS] Secondary monitor detected at ({left},{top}) {w}x{h} -- "
              f"opened borderless-fullscreen fallback canvas.")

    _window.get_surface()  # realizes the window's drawable surface up front


def is_secondary_window_event(event):
    """True if a pygame event (already known to carry a `.window` attribute,
    e.g. WINDOWCLOSE) belongs to this module's window rather than the
    primary simulator window. See inputs/gamepad.py's WINDOWCLOSE handling."""
    return _window is not None and getattr(event, "window", None) is not None \
        and event.window.id == _window.id


def handle_window_close():
    """Called from inputs/gamepad.py when this window's WINDOWCLOSE fires
    (only reachable via the single-monitor windowed fallback's titlebar --
    the auto-placed fullscreen window is borderless/has no close button).
    Hides rather than destroys the window: render() below just skips
    drawing while hidden, so an operator can't accidentally lose the
    fallback canvas for the rest of the show by fat-fingering its close
    button mid-set."""
    global _visible
    if _window is None:
        return
    _window.hide()
    _visible = False
    print("[SECONDARY CANVAS] Fallback window closed by operator -- hidden for the rest of this session.")


def _compute_layout(win_w, win_h):
    """Largest integer per-LED pixel scale that still fits the raw
    MATRIX_WIDTH x MATRIX_HEIGHT raster inside the window, centered
    (letterboxed/pillarboxed) so the aspect ratio is preserved and every LED
    cell stays a crisp, uniform-size square rather than a smeared stretch."""
    scale = max(1, min(win_w // MATRIX_WIDTH, win_h // MATRIX_HEIGHT))
    render_w = MATRIX_WIDTH * scale
    render_h = MATRIX_HEIGHT * scale
    off_x = (win_w - render_w) // 2
    off_y = (win_h - render_h) // 2
    return scale, off_x, off_y


def render(matrix_surface):
    """Mirrors the current frame's `matrix_surface` (the same off-screen
    raster graphics/matrix_canvas.py::update_matrix_canvas() builds for
    every mode -- DJ mode, Quiz mode, Price Game, Space Invaders, and the
    Westminster Bat Clock sequence all draw into it before either canvas
    ever renders, so nothing extra is needed here to cover those) onto the
    fallback window, scaled to fill it. Call once per frame, after
    update_matrix_canvas() and alongside the primary canvas's own draw --
    see graphics/matrix_canvas.py::render_led_grid()."""
    if _window is None or not _visible:
        return

    surface = _window.get_surface()
    win_w, win_h = _window.size
    scale, off_x, off_y = _compute_layout(win_w, win_h)
    gap = config.SECONDARY_CANVAS_PIXEL_GAP_PX if config.SECONDARY_CANVAS_SHOW_PIXEL_GRID else 0
    gap = min(gap, scale - 1)  # never let the gap eat the whole cell at a small scale

    surface.fill(BLACK)
    for y in range(MATRIX_HEIGHT):
        for x in range(MATRIX_WIDTH):
            color = matrix_surface.get_at((x, y))
            if color == (0, 0, 0, 255):
                color = RED_OFF
            rect = (
                off_x + x * scale + gap,
                off_y + y * scale + gap,
                scale - gap,
                scale - gap,
            )
            pygame.draw.rect(surface, color, rect)

    if config.SECONDARY_CANVAS_SHOW_PANEL_SEAMS:
        seam_color = (80, 0, 0)
        seam_thickness = max(1, scale // 8)
        for px, py, pw, ph in PANELS.values():
            rect = (off_x + px * scale, off_y + py * scale, pw * scale, ph * scale)
            pygame.draw.rect(surface, seam_color, rect, seam_thickness)

    _window.flip()
