# graphics/rig_view.py
"""Practice rig TV view (2026-11-11, config.PRACTICE_RIG).

Draws the whole game board full-screen on an HDMI TV for a Pi that has no
board attached, so a show can be rehearsed without the rig:

  - the six LED panels, lit exactly as drivers/led_bridge.py would send them
    (any non-zero red = on; the real panels are 1-bit, single colour),
  - the 102 marquee bulbs at their photo-measured places
    (config.MARQUEE_LAYOUT), from the same pixel buffer drivers/wled_engine.py
    sends to the marquee board,
  - the outline strip as a band around the board. The real strip runs WLED's
    own effects on its board, which this cannot reproduce: solid, blink and
    rainbow are drawn as such, every other effect as the song's colour moving
    over its background colour, and the effect's name is printed underneath,
  - the four arcade button lights (drivers/simon_hardware.led_states()).


DMX lights are not drawn.

Runs as its OWN PROCESS (python -m graphics.rig_view), started and fed by
graphics/rig_feed.py inside the show app. On a Pi 3 every screen update costs
10-30 ms however little changed, which stalled the 40 fps show loop when this
drew in-process; here it has a CPU core to itself and the show never waits
for the TV. It imports no drivers: everything it draws arrives as one small
UDP datagram on localhost (config.RIG_VIEW_PORT), about 20 a second. Only the
LEDs, bulbs and strip cells that changed are redrawn.
"""
import json
import math
import os
import socket
import subprocess
import sys
import time

import numpy as np
import pygame

import config
from config import MATRIX_WIDTH, MATRIX_HEIGHT, PANELS, TOP_COMBINED

_BG = (0, 0, 0)
_BULB_OFF = (30, 30, 34)
_STRIP_OFF = (14, 14, 16)
_GAMMA = [int(round(255 * (v / 255.0) ** 0.55)) for v in range(256)]  # dim LED values read brighter in real life

_ready = False
_cell = 0
_areas = []        # (source rect in matrix pixels, screen rect) per LED panel area
_cell_x = None     # matrix [x, y] -> screen x of that LED's cell (-1: not on a panel)
_cell_y = None
_dot_on = None     # one lit / unlit panel LED, _cell px square
_dot_off = None
_bulbs = []        # (x, y) per marquee LED index, screen pixels
_bulb_r = 0
_bulb_boxes = []   # (first LED, count, screen rect) per marquee segment
_strip = []        # one rect per outline cell, in order around the loop
_strip_edges = []  # the strip's four sides, for display.update()
_strip_phase = None  # numpy: each strip cell's place around the loop, 0..1
_gamma_np = None
_buttons = []      # (color name, (x, y))
_btn_r = 0
_label_pos = (0, 0)
_font = None

_strip_at = 0.0
_prev_lit = None
_prev_bulbs = None
_prev_strip = None
_prev_buttons = None
_prev_label = None
_first = True
_stat_n = 0
_stat_sum = 0.0
_stat_max = 0.0
_stat_at = 0.0
_stat_parts = [0.0, 0.0, 0.0, 0.0]   # seconds in: panels, bulbs, strip+buttons, screen update


def _segment_points():
    """name -> [(photo x, photo y), ...] in LED order."""
    out = {}
    for name, runs in config.MARQUEE_LAYOUT.items():
        pts = []
        for x0, y0, x1, y1, n in runs:
            for k in range(n):
                f = k / (n - 1) if n > 1 else 0.0
                pts.append((x0 + (x1 - x0) * f, y0 + (y1 - y0) * f))
        out[name] = pts
    return out


def _init(screen):
    global _ready, _cell, _bulb_r, _btn_r, _font, _label_pos
    global _cell_x, _cell_y, _dot_on, _dot_off, _strip_phase, _gamma_np
    W, H = screen.get_size()
    seg_pts = _segment_points()
    allp = [p for pts in seg_pts.values() for p in pts]
    minx, maxx = min(p[0] for p in allp), max(p[0] for p in allp)
    miny, maxy = min(p[1] for p in allp), max(p[1] for p in allp)
    pad = 85.0                      # photo units from the outermost bulbs to the outline strip
    label_h = max(18, H // 40)
    btn_col = int(W * 0.13)
    k = min((H - label_h - 12) / (maxy - miny + 2 * pad), (W - btn_col - 12) / (maxx - minx + 2 * pad))
    board_w, board_h = (maxx - minx + 2 * pad) * k, (maxy - miny + 2 * pad) * k
    ox = (W - btn_col - board_w) / 2 + pad * k
    oy = (H - label_h - board_h) / 2 + pad * k

    def to_screen(p):
        return (int(round(ox + (p[0] - minx) * k)), int(round(oy + (p[1] - miny) * k)))

    _bulb_r = max(4, int(round(17 * k)))
    _bulbs[:] = [(0, 0)] * config.MARQUEE_TOTAL_LEDS
    boxes = {}
    for name, pts in seg_pts.items():
        start = config.MARQUEE_SEGMENTS[name]["start"]
        sp = [to_screen(p) for p in pts]
        for i, p in enumerate(sp):
            if 0 <= start + i < len(_bulbs):
                _bulbs[start + i] = p
        xs, ys = [p[0] for p in sp], [p[1] for p in sp]
        boxes[name] = (min(xs), min(ys), max(xs), max(ys))

    # LED panels: each sits centred inside its own bulb loop, all at one cell size.
    sources = {"title": TOP_COMBINED, "panel3": PANELS[3], "panel4": PANELS[4],
               "panel5": PANELS[5], "panel6": PANELS[6]}
    inset = _bulb_r + 5
    _cell = max(2, int(min(min((b[2] - b[0] - 2 * inset) / sources[n][2], (b[3] - b[1] - 2 * inset) / sources[n][3])
                           for n, b in boxes.items())))
    _dot_on = pygame.Surface((_cell, _cell))
    _dot_off = pygame.Surface((_cell, _cell))
    for dot, rgb in ((_dot_on, (255, 0, 0)), (_dot_off, (config.RIG_VIEW_OFF_PIXEL, 0, 0))):
        dot.fill(_BG)
        if _cell >= 5:
            pygame.draw.circle(dot, rgb, (_cell // 2, _cell // 2), max(1, (_cell - 1) // 2))
        else:
            dot.fill(rgb, (0, 0, max(1, _cell - 1), max(1, _cell - 1)))
    _cell_x = np.full((MATRIX_WIDTH, MATRIX_HEIGHT), -1, dtype=np.int32)
    _cell_y = np.full((MATRIX_WIDTH, MATRIX_HEIGHT), -1, dtype=np.int32)
    _areas.clear()
    _bulb_boxes.clear()
    for name, (x0, y0, x1, y1) in boxes.items():
        sx, sy, sw, sh = sources[name]
        pw, ph = sw * _cell, sh * _cell
        px0, py0 = (x0 + x1 - pw) // 2, (y0 + y1 - ph) // 2
        _cell_x[sx:sx + sw, sy:sy + sh] = (px0 + np.arange(sw) * _cell)[:, None]
        _cell_y[sx:sx + sw, sy:sy + sh] = (py0 + np.arange(sh) * _cell)[None, :]
        _areas.append(((sx, sy, sw, sh), pygame.Rect(px0, py0, pw, ph)))
        seg = config.MARQUEE_SEGMENTS[name]
        m = _bulb_r + 4
        _bulb_boxes.append((seg["start"], seg["count"],
                            pygame.Rect(x0 - m, y0 - m, x1 - x0 + 2 * m, y1 - y0 + 2 * m)))

    # Outline strip: cells around a rectangle just outside the outermost bulbs.
    t = max(4, int(round(13 * k)))                       # band thickness
    rx0, ry0 = int(ox - pad * k * 0.72), int(oy - pad * k * 0.72)
    rx1 = int(ox + (maxx - minx) * k + pad * k * 0.72)
    ry1 = int(oy + (maxy - miny) * k + pad * k * 0.72)
    step = max(10, int(round(30 * k)))
    cells = []
    for x in range(rx0, rx1, step):
        cells.append(pygame.Rect(x, ry0 - t // 2, step - 2, t))                 # top, left -> right
    for y in range(ry0, ry1, step):
        cells.append(pygame.Rect(rx1 - t // 2, y, t, step - 2))                 # right, down
    for x in range(rx1 - step, rx0 - step, -step):
        cells.append(pygame.Rect(x, ry1 - t // 2, step - 2, t))                 # bottom, right -> left
    for y in range(ry1 - step, ry0 - step, -step):
        cells.append(pygame.Rect(rx0 - t // 2, y, t, step - 2))                 # left, up
    _strip[:] = cells
    _strip_phase = np.arange(len(cells), dtype=np.float32) / len(cells)
    _gamma_np = np.array(_GAMMA, dtype=np.uint8)
    e = t + step
    _strip_edges[:] = [pygame.Rect(rx0 - e, ry0 - e, rx1 - rx0 + 2 * e, 2 * e),
                       pygame.Rect(rx0 - e, ry1 - e, rx1 - rx0 + 2 * e, 2 * e),
                       pygame.Rect(rx0 - e, ry0 - e, 2 * e, ry1 - ry0 + 2 * e),
                       pygame.Rect(rx1 - e, ry0 - e, 2 * e, ry1 - ry0 + 2 * e)]

    # Arcade button lights: a column to the right of the board.
    _btn_r = max(10, int(min(btn_col * 0.30, H * 0.075)))
    bx = int(W - btn_col / 2)
    names = list(config.SIMON_HW_COLOR_ORDER)
    gap = _btn_r * 3
    top = H // 2 - gap * (len(names) - 1) // 2
    _buttons[:] = [(c, (bx, top + i * gap)) for i, c in enumerate(names)]

    pygame.font.init()
    _font = pygame.font.Font(None, label_h + 4)
    _label_pos = (rx0, H - label_h - 4)
    _ready = True
    print(f"[RIG VIEW] Practice board on a {W}x{H} screen: {_cell}px per panel LED, "
          f"{len(_bulbs)} marquee bulbs, {len(_strip)} outline cells.")


def _scale(rgb, f):
    return (int(rgb[0] * f), int(rgb[1] * f), int(rgb[2] * f))


def _strip_colors(view, now):
    """One colour for the whole strip, or a list with one per cell (gamma applied)."""
    kind = view["kind"]
    if kind == "off":
        return _STRIP_OFF
    bri = view["bri"] / 255.0
    c1, c2 = _scale(view["c1"], bri), _scale(view["c2"], bri)
    rate = 0.25 + view["speed"] / 255.0 * 1.5          # cycles per second
    if kind == "solid":
        c = c1
    elif kind == "blink":
        c = c1 if (now * rate * 2) % 1.0 < 0.5 else c2
    elif kind == "audio":
        c = _scale(c1, max(0.15, 0.55 + 0.45 * math.sin(now * 9.0) * math.sin(now * 2.3)))
    elif kind == "rainbow":
        h = (_strip_phase * 2 - now * rate * 0.3) % 1.0 * 6.0
        rgb = np.stack([np.clip(np.abs(h - 3.0) - 1.0, 0, 1), np.clip(2.0 - np.abs(h - 2.0), 0, 1),
                        np.clip(2.0 - np.abs(h - 4.0), 0, 1)], axis=1) * (255.0 * bri)
        return [tuple(v) for v in _gamma_np[rgb.astype(np.uint8)].tolist()]
    else:                                               # "motion": bands of c1 travelling over c2
        f = 0.5 + 0.5 * np.sin((_strip_phase * 8 - now * rate * 0.5) * (2 * math.pi))
        f = (f * f)[:, None]
        a, b = np.array(c2, dtype=np.float32), np.array(c1, dtype=np.float32)
        return [tuple(v) for v in _gamma_np[(a + (b - a) * f).astype(np.uint8)].tolist()]
    return (_GAMMA[c[0]], _GAMMA[c[1]], _GAMMA[c[2]]) if any(c) else _STRIP_OFF


def draw(screen, lit, px, view, leds):
    """One frame. lit: bool array [x, y] of the LED matrix; px: the marquee's
    RGB bytes; view: accent_engine.view_state(); leds: color -> lit."""
    global _prev_lit, _prev_bulbs, _prev_strip, _prev_buttons, _prev_label, _first
    global _stat_n, _stat_sum, _stat_max, _stat_at, _strip_at
    if not _ready:
        _init(screen)
    mono = time.monotonic()
    now = time.time()
    dirty = []
    if _first:
        screen.fill(_BG)

    # 1. LED panels: only the LEDs that changed since the last draw
    diff = (_cell_x >= 0) if _prev_lit is None else (lit != _prev_lit) & (_cell_x >= 0)
    if diff.any():
        xs, ys = np.nonzero(diff)
        on, off = _dot_on, _dot_off
        screen.blits([(on if v else off, (x, y)) for v, x, y in
                      zip(lit[xs, ys].tolist(), _cell_x[xs, ys].tolist(), _cell_y[xs, ys].tolist())], False)
        for (sx, sy, sw, sh), rect in _areas:
            if diff[sx:sx + sw, sy:sy + sh].any():
                dirty.append(rect)
    _prev_lit = lit
    t1 = time.monotonic()

    # 2. Marquee bulbs: only the ones whose colour changed
    if px != _prev_bulbs:
        old = _prev_bulbs
        _prev_bulbs = px
        r = _bulb_r
        core = max(1, int(r * 0.45))
        circle = pygame.draw.circle
        for start, count, box in _bulb_boxes:
            o0, o1 = start * 3, (start + count) * 3
            if old is not None and px[o0:o1] == old[o0:o1]:
                continue
            dirty.append(box)
            for i in range(start, start + count):
                o = i * 3
                if old is not None and px[o:o + 3] == old[o:o + 3]:
                    continue
                cr, cg, cb = px[o], px[o + 1], px[o + 2]
                xy = _bulbs[i]
                if cr or cg or cb:
                    c = (_GAMMA[cr], _GAMMA[cg], _GAMMA[cb])
                    circle(screen, (c[0] * 3 // 8, c[1] * 3 // 8, c[2] * 3 // 8), xy, r + 3)
                    circle(screen, c, xy, r)
                    circle(screen, ((c[0] + 255) // 2, (c[1] + 255) // 2, (c[2] + 255) // 2), xy, core)
                else:
                    circle(screen, _BG, xy, r + 3)
                    circle(screen, _BULB_OFF, xy, r)
    t2 = time.monotonic()

    # 3. Outline strip (moving looks are redrawn ~12 times a second)
    moving = view["kind"] in ("motion", "rainbow")
    if not moving or mono - _strip_at >= 0.08 or _prev_strip is None:
        _strip_at = mono
        colors = _strip_colors(view, now)
        if colors != _prev_strip:
            _prev_strip = colors
            fill = screen.fill
            if isinstance(colors, tuple):
                for rect in _strip:
                    fill(colors, rect)
            else:
                for rect, c in zip(_strip, colors):
                    fill(c, rect)
            dirty.extend(_strip_edges)
    label = "OUTLINE: " + view["label"]
    if view["kind"] in ("motion", "audio"):
        label += "  (approximate)"
    if label != _prev_label:
        _prev_label = label
        text = _font.render(label, True, (90, 90, 96), _BG)
        clear = pygame.Rect(_label_pos[0], _label_pos[1], screen.get_width() // 2, text.get_height())
        screen.fill(_BG, clear)
        screen.blit(text, _label_pos)
        dirty.append(clear)

    # 4. Button lights
    bkey = tuple(bool(leds.get(c)) for c, _p in _buttons)
    if bkey != _prev_buttons:
        _prev_buttons = bkey
        r = _btn_r
        for (color, (x, y)), lit_now in zip(_buttons, bkey):
            rgb = config.SIMON_HW_COLOR_RGB[color]
            pygame.draw.circle(screen, _BG, (x, y), r + 6)
            if lit_now:
                pygame.draw.circle(screen, _scale(rgb, 0.4), (x, y), r + 6)
                pygame.draw.circle(screen, rgb, (x, y), r)
                pygame.draw.circle(screen, tuple((v + 255) // 2 for v in rgb), (x, y), r // 2)
            else:
                pygame.draw.circle(screen, _scale(rgb, 0.16), (x, y), r)
                pygame.draw.circle(screen, _scale(rgb, 0.30), (x, y), r, 2)
            dirty.append(pygame.Rect(x - r - 6, y - r - 6, 2 * r + 13, 2 * r + 13))

    t3 = time.monotonic()
    if _first:
        _first = False
        pygame.display.flip()
    elif dirty:
        pygame.display.update(dirty)

    # Cost of this view, logged every 10 minutes.
    t4 = time.monotonic()
    took = t4 - mono
    for i, d in enumerate((t1 - mono, t2 - t1, t3 - t2, t4 - t3)):
        _stat_parts[i] += d
    _stat_n += 1
    _stat_sum += took
    _stat_max = max(_stat_max, took)
    if not _stat_at:
        _stat_at = mono
    elif mono - _stat_at >= 600.0:
        p = [v / _stat_n * 1000 for v in _stat_parts]
        print(f"[RIG VIEW] {_stat_n / (mono - _stat_at):.0f} fps, draw avg {_stat_sum / _stat_n * 1000:.1f}ms, "
              f"worst {_stat_max * 1000:.0f}ms (panels {p[0]:.1f}, bulbs {p[1]:.1f}, strip {p[2]:.1f}, "
              f"screen {p[3]:.1f}).")
        _stat_at, _stat_n, _stat_sum, _stat_max = mono, 0, 0.0, 0.0
        _stat_parts[:] = [0.0, 0.0, 0.0, 0.0]


# ------------------------------------------------------------------ process
MAGIC = b"RIG1"
_LIT_BYTES = (MATRIX_WIDTH * MATRIX_HEIGHT + 7) // 8
_PX_BYTES = config.MARQUEE_TOTAL_LEDS * 3


def pack(lit, px, view, leds):
    """One datagram (graphics/rig_feed.py sends it): MAGIC, the LED matrix one
    bit per LED, the marquee's RGB bytes, then JSON for the strip and buttons."""
    extra = json.dumps({"view": view, "leds": leds}, separators=(",", ":")).encode()
    return MAGIC + np.packbits(lit).tobytes() + bytes(px) + extra


def _unpack(data):
    if len(data) < 4 + _LIT_BYTES + _PX_BYTES or data[:4] != MAGIC:
        return None
    bits = np.frombuffer(data, dtype=np.uint8, count=_LIT_BYTES, offset=4)
    lit = np.unpackbits(bits)[:MATRIX_WIDTH * MATRIX_HEIGHT].reshape(MATRIX_WIDTH, MATRIX_HEIGHT).astype(bool)
    o = 4 + _LIT_BYTES
    extra = json.loads(data[o + _PX_BYTES:].decode())
    view = extra["view"]
    view["c1"], view["c2"] = tuple(view["c1"]), tuple(view["c2"])
    return lit, bytes(data[o:o + _PX_BYTES]), view, extra["leds"]


def _set_tv_mode(mode):
    """Puts the TV in `mode` ("1280x720") through wlr-randr before the window
    opens: a Pi 3 cannot push 1080p frames fast enough. Best effort."""
    try:
        out = subprocess.run(["wlr-randr"], capture_output=True, text=True, timeout=10).stdout
        name = next((ln.split()[0] for ln in out.splitlines() if ln and not ln[0].isspace()), None)
        if name:
            subprocess.run(["wlr-randr", "--output", name, "--mode", mode], timeout=10)
            print(f"[RIG VIEW] TV output {name} set to {mode}.")
    except Exception as e:
        print(f"[RIG VIEW] Could not set the TV to {mode} ({e}) -- staying at its current mode.")


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", config.RIG_VIEW_PORT))
    except OSError:
        print("[RIG VIEW] Another practice-board window is already running -- this one exits.")
        return
    sock.settimeout(0.25)
    if config.RIG_VIEW_TV_MODE and sys.platform.startswith("linux"):
        _set_tv_mode(config.RIG_VIEW_TV_MODE)
        time.sleep(1.0)
    pygame.display.init()
    screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    pygame.display.set_caption("Practice board")
    pygame.mouse.set_visible(False)
    _init(screen)
    parent = os.getppid()
    frame = (np.zeros((MATRIX_WIDTH, MATRIX_HEIGHT), dtype=bool), bytes(_PX_BYTES),
             {"kind": "off", "c1": (0, 0, 0), "c2": (0, 0, 0), "bri": 0, "speed": 128, "label": "waiting for the show app"},
             {})
    draw(screen, *frame)
    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return
        data = None
        try:
            data = sock.recv(4096)
            sock.setblocking(False)          # keep only the newest waiting datagram
            try:
                while True:
                    data = sock.recv(4096)
            except (BlockingIOError, OSError):
                pass
            sock.settimeout(0.25)
        except socket.timeout:
            if os.getppid() != parent:       # the show app is gone
                return
        if data:
            try:
                got = _unpack(data)
            except Exception:
                got = None
            if got is not None:
                frame = got
        draw(screen, *frame)


if __name__ == "__main__":
    main()
