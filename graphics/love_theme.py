"""LOVE -- idle theme for slow songs and ballads (2026-10-11).

Single-panel (32x16) animations for panels 3-6, dealt like any other idle
theme (graphics/animations.py IDLE_THEMES["Love"]). Slow songs pick this
theme automatically (drivers/light_prefs_engine.py::resolve_idle_theme).
1-bit panels, so soft light (candle glow, sparkle) is ordered-dithered,
same helpers as graphics/technical_dreams.py.
"""
import math

import numpy as np
import pygame

from graphics.technical_dreams import ON, dither, blit_mask, _grid, _beat

OFF = (0, 0, 0)


def _heart(h, w, cx, cy, s):
    """Filled heart (implicit curve) centred at cx, cy, about 2.4*s wide."""
    yy, xx = _grid(h, w)
    x = (xx - cx) / s
    y = -(yy - cy) / (s * 0.95) + 0.25
    return (x * x + y * y - 1) ** 3 - x * x * y ** 3 <= 0


def _outline(m):
    inner = m & np.roll(m, 1, 0) & np.roll(m, -1, 0) & np.roll(m, 1, 1) & np.roll(m, -1, 1)
    return m & ~inner


_SMALL = np.array([[c == "1" for c in r] for r in
                   ("0110110", "1111111", "1111111", "0111110", "0011100", "0001000")], bool)
_TINY = np.array([[c == "1" for c in r] for r in ("01010", "11111", "01110", "00100")], bool)


def _put(m, sprite, x, y):
    h, w = sprite.shape
    H, W = m.shape
    xa, ya, xb, yb = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if xb > xa and yb > ya:
        m[ya:yb, xa:xb] |= sprite[ya - y:yb - y, xa - x:xb - x]


def love_heartbeat(surface, rect, t, seed=0.0):
    """A heart beating lub-dub on the song's tempo."""
    x0, y0, w, h = rect
    ph = _beat(t)
    kick = 0.2 * math.exp(-ph * 12) + (0.12 * math.exp(-(ph - 0.22) * 12) if ph > 0.22 else 0)
    m = _heart(h, w, w / 2 - 0.5, h / 2 + 1, 5.4 * (1 + kick))
    if kick > 0.08:
        m |= _outline(_heart(h, w, w / 2 - 0.5, h / 2, 6.6)) & dither(np.full((h, w), kick * 3), x0, y0)
    blit_mask(surface, x0, y0, m)


def love_floating_hearts(surface, rect, t, seed=0.0):
    """Little hearts drifting up and swaying."""
    x0, y0, w, h = rect
    m = np.zeros((h, w), bool)
    for k in range(5):
        sp = 3.0 + (k * 37 % 5)
        y = int(h - ((t + seed) * sp + k * 7.3) % (h + 8))
        x = int((k * 6.7 + 2 + 2 * math.sin(t * 1.3 + k)) % (w - 5))
        _put(m, _TINY, x, y)
        if k == 2:
            _put(m, _SMALL, int((w / 2 - 3 + 6 * math.sin(t * 0.7)) % (w - 7)), int(h - ((t + seed) * 2.2) % (h + 8)))
    blit_mask(surface, x0, y0, m)


def love_cupid_arrow(surface, rect, t, seed=0.0):
    """An arrow flies in from the left and strikes a heart, which throbs."""
    x0, y0, w, h = rect
    p = ((t + seed) % 3.0) / 3.0
    hit = 0.55
    hx = w - 9
    m = _heart(h, w, hx, h / 2, 4.2 * (1 + (0.25 * math.exp(-(p - hit) * 18) if p > hit else 0)))
    tip = int(-4 + (hx - 2 + 4) * min(1.0, p / hit))
    yc = h // 2
    for x in range(tip - 12, tip + 1):
        if 0 <= x < w:
            m[yc, x] = True
    for d in (1, 2):  # arrowhead
        for x in (tip - d,):
            if 0 <= x < w:
                m[yc - d, x] = m[yc + d, x] = True
    for d in (1, 2):  # fletching: feathers swept back from the tail
        for x in (tip - 12 + d, tip - 10 + d):
            if 0 <= x - 1 < w:
                m[yc - 1, x - 1] = m[yc + 1, x - 1] = True
    if p > hit:
        m[yc, max(0, tip - 4):tip + 1] = True
    blit_mask(surface, x0, y0, m)


def love_two_hearts(surface, rect, t, seed=0.0):
    """Two little hearts glide together, become one big heart, and sparkle."""
    x0, y0, w, h = rect
    p = ((t + seed) % 4.5) / 4.5
    m = np.zeros((h, w), bool)
    if p < 0.5:
        q = p / 0.5
        q = q * q * (3 - 2 * q)
        _put(m, _SMALL, int(1 + (w / 2 - 7) * q), 5 + int(2 * math.sin(t * 3)))
        _put(m, _SMALL, int(w - 8 - (w / 2 - 8) * q), 5 + int(2 * math.cos(t * 3)))
    else:
        q = (p - 0.5) / 0.5
        m |= _heart(h, w, w / 2 - 0.5, h / 2, 3.5 + 1.8 * min(1, q * 3))
        if q > 0.3:
            rng = np.random.RandomState(int(t * 8) % 1000)
            for _ in range(5):
                a, r = rng.uniform(0, 2 * math.pi), 8 + 6 * q
                x, y = int(w / 2 + r * 1.4 * math.cos(a)), int(h / 2 + r * 0.6 * math.sin(a))
                if 0 <= x < w and 0 <= y < h:
                    m[y, x] = True
    blit_mask(surface, x0, y0, m)


_ROSE = np.array([[c == "1" for c in r] for r in
                  ("001111100", "011000110", "110111011", "101101101",
                   "110010011", "011111110", "001111100")], bool)


def love_rose(surface, rect, t, seed=0.0):
    """A rose swaying on its stem, now and then dropping a petal."""
    x0, y0, w, h = rect
    sway = 2.0 * math.sin((t + seed) * 1.1)
    m = np.zeros((h, w), bool)
    base_x = w // 2
    for y in range(7, h):  # stem bends with the sway
        f = (h - 1 - y) / (h - 8)
        m[y, int(round(base_x + sway * f * f))] = True
    m[11, base_x - 2:base_x] = m[10, base_x - 3] = True      # leaves
    m[12, base_x + 1:base_x + 3] = m[11, base_x + 3] = True
    _put(m, _ROSE, int(round(base_x - 4 + sway)), 0)
    p = ((t + seed) % 5.0) / 5.0
    if p > 0.4:  # a falling petal
        q = (p - 0.4) / 0.6
        px, py = int(base_x + 5 + sway + 6 * q + 2 * math.sin(q * 9)), int(4 + q * (h - 4))
        if 0 <= px < w and 0 <= py < h:
            m[py, px] = True
            if px + 1 < w:
                m[py, px + 1] = True
    blit_mask(surface, x0, y0, m)


def love_candles(surface, rect, t, seed=0.0):
    """Two candles with flickering, softly glowing flames."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    m = np.zeros((h, w), bool)
    glow = np.zeros((h, w), np.float32)
    for k, (cx, top) in enumerate(((10, 8), (22, 10))):
        m[top:h, cx - 2:cx + 2] = True
        m[top - 1, cx] = True  # wick
        fl = math.sin(t * 9 + k * 2 + seed) * 0.6 + math.sin(t * 13.7 + k) * 0.4
        fx, fy = cx + 0.5 * fl - 0.5, top - 4
        d = ((xx - fx) / 1.3) ** 2 + ((yy - fy) / (2.6 + 0.4 * fl)) ** 2
        m |= d <= 1.0
        glow = np.maximum(glow, np.clip(0.28 - np.hypot((xx - fx) / 1.8, yy - fy) / 18, 0, 1))
    blit_mask(surface, x0, y0, m | (dither(glow, x0, y0) & (yy < 10)))


def _heart_curve(n):
    u = np.linspace(0, 2 * math.pi, n)
    x = 16 * np.sin(u) ** 3
    y = -(13 * np.cos(u) - 5 * np.cos(2 * u) - 2 * np.cos(3 * u) - np.cos(4 * u))
    return x, y


def love_heart_draw(surface, rect, t, seed=0.0):
    """A heart drawn by an invisible pen, filled in, then faded away."""
    x0, y0, w, h = rect
    p = ((t + seed) % 5.0) / 5.0
    hx, hy = _heart_curve(60)
    pts = [(x0 + w / 2 + x * 0.42, y0 + h / 2 + 0.5 + y * 0.42) for x, y in zip(hx, hy)]
    n = int(len(pts) * min(1.0, p / 0.45))
    if n >= 2:
        pygame.draw.lines(surface, ON, False, pts[:n])
    if p > 0.45:
        level = min(1.0, (p - 0.45) / 0.25) if p < 0.8 else max(0.0, 1 - (p - 0.8) / 0.2)
        fill = _heart(h, w, w / 2 - 0.5, h / 2, 6.0) & dither(np.full((h, w), level, np.float32), x0, y0)
        ys, xs = np.nonzero(fill)
        for x, y in zip(xs, ys):
            surface.set_at((x0 + int(x), y0 + int(y)), ON)


def love_ecg(surface, rect, t, seed=0.0):
    """Heart monitor: a trace sweeping across, spiking on every beat."""
    x0, y0, w, h = rect
    period = 1.0  # one beat on the tempo-locked clock
    base = h - 5
    head = ((t + seed) / period * 20) % w
    m = np.zeros((h, w), bool)
    prev = None
    for x in range(w):
        age = (head - x) % w  # px since the pen passed
        if age > w - 5:
            continue  # the blank gap just ahead of the pen
        tt = (t + seed) - age / 20 * period
        ph = (tt / period) % 1.0
        if ph < 0.1:
            y = base - int(round(10 * math.sin(ph / 0.1 * math.pi)))
        elif ph < 0.15:
            y = base + 2
        elif 0.3 < ph < 0.45:
            y = base - int(2 * math.sin((ph - 0.3) / 0.15 * math.pi))
        else:
            y = base
        y = max(0, min(h - 1, y))
        if prev is not None and abs(prev - y) > 1:
            m[min(prev, y):max(prev, y) + 1, x] = True
        m[y, x] = True
        prev = y
    if _beat(t) < 0.25:
        _put(m, _TINY, w - 6, 1)
    blit_mask(surface, x0, y0, m)


def love_sparkle_heart(surface, rect, t, seed=0.0):
    """A heart outline full of twinkling lights."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    full = _heart(h, w, w / 2 - 0.5, h / 2, 6.2)
    tw = np.sin(xx * 12.9898 + yy * 78.233 + seed) * 43758.5453 % 1.0
    lit = np.sin(t * (2 + tw * 3) + tw * 20) > 0.55
    blit_mask(surface, x0, y0, _outline(full) | (full & lit))


def love_ring(surface, rect, t, seed=0.0):
    """A diamond ring turning slowly, its stone flashing."""
    x0, y0, w, h = rect
    turn = abs(math.cos((t + seed) * 0.9))
    rw = max(1, int(9 * turn))
    cx, cy = x0 + w // 2, y0 + 10
    pygame.draw.ellipse(surface, ON, (cx - rw, cy - 5, rw * 2 + 1, 10), 1)
    pygame.draw.polygon(surface, ON, [(cx - 3, cy - 6), (cx, cy - 10), (cx + 3, cy - 6), (cx, cy - 4)])
    if (t * 1.3 + seed) % 1.6 < 0.35:
        sx, sy = cx + 3, cy - 11
        for d in range(1, 4):
            for px, py in ((sx + d, sy), (sx - d, sy), (sx, sy + d), (sx, sy - d)):
                if x0 <= px < x0 + w and y0 <= py < y0 + h:
                    surface.set_at((px, py), ON)


def love_letter(surface, rect, t, seed=0.0):
    """A love letter: the envelope opens and a heart floats out."""
    x0, y0, w, h = rect
    p = ((t + seed) % 4.0) / 4.0
    ex, ey, ew, eh = x0 + 7, y0 + 7, 18, 9
    if p > 0.25:
        q = min(1.0, (p - 0.25) / 0.6)
        m = np.zeros((h, w), bool)
        _put(m, _SMALL, w // 2 - 3, int(8 - q * 9))
        blit_mask_or(surface, x0, y0, m)
    pygame.draw.rect(surface, OFF, (ex + 1, ey + 1, ew - 2, eh - 2))
    pygame.draw.rect(surface, ON, (ex, ey, ew, eh), 1)
    pygame.draw.line(surface, ON, (ex, ey + eh - 1), (ex + ew // 2, ey + 4))
    pygame.draw.line(surface, ON, (ex + ew - 1, ey + eh - 1), (ex + ew // 2, ey + 4))
    flap = 5 if p < 0.2 else -5  # flap closed (down) then open (up)
    pygame.draw.line(surface, ON, (ex, ey), (ex + ew // 2, ey + flap))
    pygame.draw.line(surface, ON, (ex + ew - 1, ey), (ex + ew // 2, ey + flap))


def blit_mask_or(surface, x0, y0, m):
    ys, xs = np.nonzero(m)
    for x, y in zip(xs, ys):
        surface.set_at((x0 + int(x), y0 + int(y)), ON)


def love_slow_dance(surface, rect, t, seed=0.0):
    """Two dancers in a slow dance, swaying together."""
    x0, y0, w, h = rect
    sway = math.sin((t + seed) * 1.6)
    cx = x0 + w / 2 + 2 * sway
    for side in (-1, 1):
        hx = cx + side * 4
        lean = -side * 0.8 + sway * 0.6
        head = (hx + lean, y0 + 3)
        pygame.draw.circle(surface, ON, (int(round(head[0])), int(head[1])), 2, 1)
        hip = (hx, y0 + 10)
        pygame.draw.line(surface, ON, (head[0], y0 + 5), hip)
        pygame.draw.line(surface, ON, hip, (hx - 1.5 + sway * side, y0 + 15))
        pygame.draw.line(surface, ON, hip, (hx + 1.5 + sway * side, y0 + 15))
        pygame.draw.line(surface, ON, (head[0], y0 + 6), (cx, y0 + 7))  # arms meet
    if (t + seed) % 3.0 < 1.2:
        m = np.zeros((h, w), bool)
        _put(m, _TINY, int(w / 2 - 2 + 2 * sway), int(1 - ((t + seed) % 3.0) * 2))
        blit_mask_or(surface, x0, y0, m)


def love_moon_stars(surface, rect, t, seed=0.0):
    """A crescent moon, twinkling stars, the odd shooting star."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    moon = (np.hypot(xx - 7, yy - 7) <= 5.5) & ~(np.hypot(xx - 9.5, yy - 5.5) <= 5)
    rng = np.random.RandomState(int(seed * 10) % 1000 + 3)
    m = moon.copy()
    for k in range(9):
        sx, sy = rng.randint(14, w), rng.randint(0, h)
        if math.sin(t * (1.5 + k * 0.4) + k * 2) > -0.3:
            m[sy, sx] = True
    p = ((t + seed) % 6.0) / 6.0
    if p < 0.15:
        q = p / 0.15
        sx, sy = int(w - q * 22), int(1 + q * 6)
        for d in range(4):
            if 0 <= sx + d < w and 0 <= sy - d // 2 < h:
                m[sy - d // 2, sx + d] = True
    blit_mask(surface, x0, y0, m)


def love_heart_plasma(surface, rect, t, seed=0.0):
    """Plasma swirling inside a heart."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    full = _heart(h, w, w / 2 - 0.5, h / 2, 6.2)
    v = np.sin(xx / 3 + t * 1.5) + np.sin(yy / 2.5 - t) + np.sin((xx + yy) / 4 + t * 0.7 + seed)
    blit_mask(surface, x0, y0, _outline(full) | (full & dither(0.5 + 0.5 * np.sin(v * 1.4), x0, y0)))


def love_heart_tunnel(surface, rect, t, seed=0.0):
    """Heart-shaped rings flowing out from the middle."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    dx, dy = (xx - (w / 2 - 0.5)) / 1.6, -(yy - (h / 2 - 1))
    th = np.arctan2(dy, dx)
    s, c = np.sin(th), np.cos(th)
    r_heart = 2 - 2 * s + s * np.sqrt(np.abs(c)) / (s + 1.4)
    rho = np.hypot(dx, dy) / np.maximum(r_heart, 0.3)
    blit_mask(surface, x0, y0, (np.floor(rho * 1.6 - (t + seed) * 1.5).astype(int) % 2) == 0)


ANIMATIONS = [
    love_heartbeat, love_floating_hearts, love_cupid_arrow, love_two_hearts,
    love_rose, love_candles, love_heart_draw, love_ecg, love_sparkle_heart,
    love_ring, love_letter, love_slow_dance, love_moon_stars, love_heart_plasma,
    love_heart_tunnel,
]
