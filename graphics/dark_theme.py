"""DARK -- idle theme for moody, heavy, brooding songs (2026-10-13).

Single-panel (32x16) animations for panels 3-6, dealt like any other idle
theme (graphics/animations.py IDLE_THEMES["Dark"]). Songs the AI tags
energy "dark" pick this theme automatically
(drivers/light_prefs_engine.py::resolve_idle_theme). Night, weather and
unease -- no pumpkins, that's the Halloween theme.
"""
import math

import numpy as np

import config
from graphics.love_theme import _outline
from graphics.technical_dreams import (
    blit_mask, dither, _grid, _beat, _stamp, _st, sprite, draw_line, disc, noise_rng, hash01,
)


def _set(m, x, y):
    if 0 <= x < m.shape[1] and 0 <= y < m.shape[0]:
        m[y, x] = True


def _flat(h, w, level):
    return np.full((h, w), level, np.float32)


def dk_storm(surface, rect, t, seed=0.0):
    """Driving rain and the odd fork of lightning."""
    x0, y0, w, h = rect
    tt = t + seed
    m = np.zeros((h, w), bool)
    for k in range(16):
        yv = (tt * (22 + k * 7 % 9) + k * 5.7) % (h + 6) - 3
        bx = (k * 6.1 + 3) % (w + 8)
        for j in range(3):
            _set(m, int(bx - (yv - j) * 0.5) % w, int(yv) - j)
    ci, c = int(tt / 5.3), tt % 5.3
    if c < 0.3 and not 0.1 < c < 0.15:
        x = 6 + int(hash01(ci, seed) * (w - 12))
        for y in range(0, h, 3):
            nx = x + int(hash01(ci, y + 1) * 7) - 3
            draw_line(m, x, y, nx, y + 3)
            x = nx
        if c < 0.06:
            m |= dither(_flat(h, w, 0.2), x0, y0)
    blit_mask(surface, x0, y0, m)


def dk_eye(surface, rect, t, seed=0.0):
    """A watching eye: it looks from side to side, and blinks."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    c = tt % 4.7
    lid = abs(c - 0.15) / 0.15 if c < 0.3 else 1.0
    arch = np.sin(np.pi * np.clip((xx - 2) / 27.0, 0, 1))
    inside = (yy >= 8 - 6.5 * arch * lid) & (yy <= 8 + 5.5 * arch * lid) & (xx >= 2) & (xx <= 29)
    d = np.hypot(xx - (15.5 + 6 * max(-1.0, min(1.0, 1.7 * math.sin(tt * 0.7)))), yy - 8)
    blit_mask(surface, x0, y0, _outline(inside) | (inside & (((d <= 4.2) & (d > 3.2)) | (d <= 1.6))))


_STARS = ((2, 2), (6, 9), (9, 4), (12, 12), (14, 1), (29, 13), (30, 3), (4, 14))


def dk_moon(surface, rect, t, seed=0.0):
    """A full moon with clouds dragging across it."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    m = disc(h, w, 21, 6.5, 5.2) & ~disc(h, w, 19.5, 5, 1.0)
    m[8, 23] = m[4, 22] = False
    for k, (sx, sy) in enumerate(_STARS):
        if math.sin(tt * (1.1 + 0.3 * k) + k * 2) > -0.4:
            m[sy, sx] = True
    c = np.sin(xx * 0.33 - tt * 0.9 + 1.5 * np.sin(yy * 0.45 + 1)) + 0.7 * np.sin(xx * 0.15 - tt * 0.5 + yy * 0.35 + 2)
    cloud = c > 0.75
    blit_mask(surface, x0, y0, (m & ~cloud) | (cloud & dither(np.clip(0.2 + 0.3 * (c - 0.75), 0, 0.6), x0, y0)))


_CROW = sprite("..##.", ".####", "####.", ".##..", "..#..")
_CROW_UP = sprite("#.....#", ".#...#.", "..###..", "...#...")
_CROW_DOWN = sprite("..###..", ".#.#.#.", "#.....#")
_PERCHES = (4, 13, 23)


def dk_crows(surface, rect, t, seed=0.0):
    """Crows on a wire; every so often one takes off and comes back."""
    x0, y0, w, h = rect
    tt = t + seed
    ci, c = int(tt / 8.0), tt % 8.0
    wire = [9 + int(round(2 * math.sin(math.pi * x / (w - 1)))) for x in range(w)]
    m = np.zeros((h, w), bool)
    for x in range(w):
        m[wire[x], x] = True
    flap = _CROW_UP if int(tt * 7) % 2 else _CROW_DOWN
    for i, px in enumerate(_PERCHES):
        top = wire[px + 2] - 5
        side = 1 if i < 2 else -1
        if i == ci % 3 and c > 5.0:         # away
            q = (c - 5.0) / 3.0
            _stamp(m, flap, int(px + side * q * 34), int(top - 2 - q * 12 + 2 * math.sin(q * 14)))
        elif i == ci % 3 and c < 1.0:       # and back in to land
            _stamp(m, flap, int(px - side * (1 - c) * 14), int(top - 1 - (1 - c) * 8))
        else:
            _stamp(m, _CROW if i % 2 == 0 else _CROW[:, ::-1], px, top)
    blit_mask(surface, x0, y0, m)


def dk_smoke(surface, rect, t, seed=0.0):
    """Smoke curling up from a burning stick."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    rise = h - 2 - yy
    mid = 15.5 + (0.5 + rise * 0.28) * np.sin(yy * 0.55 + tt * 2.0) + rise * 0.12 * np.sin(yy * 0.21 + tt * 0.7 + 2)
    v = np.exp(-((xx - mid) / (0.8 + rise * 0.22)) ** 2) * np.clip(1.0 - rise * 0.045, 0.25, 1)
    m = dither(v * 0.9, x0, y0) & (yy < h - 2)
    m[h - 2, 15:17] = True
    draw_line(m, 15, h - 1, 9, h - 1)
    blit_mask(surface, x0, y0, m)


def dk_static(surface, rect, t, seed=0.0):
    """Dead-channel TV snow, collapsing to a line now and then."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    m = noise_rng.rand(h, w) < 0.28
    m |= (np.abs(yy - ((tt * 6) % (h + 8) - 4)) < 1.5) & (noise_rng.rand(h, w) < 0.5)
    q = tt % 7.0 - 6.0
    if q > 0:                               # the set switching off, then back on
        if q < 0.2:
            m &= np.abs(yy - 7.5) <= 8 * (1 - q / 0.2)
        elif q < 0.6:
            half = 15 * (1 - (q - 0.2) / 0.4)
            m = (np.abs(yy - 7.5) < 1) & (np.abs(xx - 15.5) <= half + 0.6)
        elif q < 0.85:
            m[:] = False
    blit_mask(surface, x0, y0, m)


_FIGURE = sprite(".##.", "####", ".##.", "####", "####", "####", ".##.", ".##.")


def dk_streetlamp(surface, rect, t, seed=0.0):
    """A lone figure beside a flickering street lamp, in the rain."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    c = tt % 6.3
    m = np.zeros((h, w), bool)
    if not (c < 0.12 or 0.25 < c < 0.33):
        m |= (yy >= 5) & (np.abs(xx - 11) <= 1 + (yy - 5) * 0.75) & dither(_flat(h, w, 0.36), x0, y0)
    m[h - 1, :] = True
    m[3:h - 1, 6] = True
    m[3, 6:12] = True
    m[4, 10:13] = True
    for k in range(5):
        y = int((tt * (17 + k * 3) + k * 6.3) % (h + 3)) - 2
        for j in (0, 1):
            _set(m, 17 + (k * 7) % 15, y - j)
    _stamp(m, _FIGURE, 22, 7)
    blit_mask(surface, x0, y0, m)


def dk_pendulum(surface, rect, t, seed=0.0):
    """A pendulum swinging in time with the music, trailing a fading echo."""
    x0, y0, w, h = rect
    m = np.zeros((h, w), bool)
    m[0, 12:20] = True
    for back, level in ((0.15, 0.25), (0.08, 0.5), (0.0, 1.0)):
        a = 1.12 * math.sin(math.pi * (t - back) / (2 * config.TEMPO_PERIOD_DEFAULT_SECONDS))
        bx, by = 15.5 + 11.3 * math.sin(a), 11.3 * math.cos(a)
        bob = disc(h, w, bx, by, 2.3)
        if level < 1:
            bob &= dither(_flat(h, w, level), x0, y0)
        else:
            draw_line(m, 15.5, 0, bx, by)
        m |= bob
    blit_mask(surface, x0, y0, m)


def dk_embers(surface, rect, t, seed=0.0):
    """Embers drifting up off a bed of coals."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    glow = np.clip((yy - (h - 4.5)) / 4, 0, 1) * (0.6 + 0.3 * np.sin(xx * 0.9 + tt * 3) * np.sin(xx * 0.37 - tt * 1.7))
    m = dither(glow, x0, y0)
    for k in range(12):
        p = (tt * (0.12 + 0.05 * (k % 4)) + k * 0.37) % 1.0
        if p < 0.5 + 0.5 * ((k * 0.618) % 1.0) and math.sin(tt * 9 + k * 2.1) > -0.5:
            _set(m, int((k * 7.9 + 2) % w + 3 * math.sin(p * 7 + k)), int(h - 3 - p * (h + 2)))
    blit_mask(surface, x0, y0, m)


def dk_eclipse(surface, rect, t, seed=0.0):
    """A total eclipse: black sun, flaring corona, a bead of light crawling round the rim."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    cx, cy = w / 2 - 0.5, h / 2 - 0.5
    d = np.hypot(xx - cx, yy - cy)
    th = np.arctan2(yy - cy, xx - cx)
    rays = 0.55 + 0.45 * np.sin(th * 7 + tt * 0.8) * np.sin(th * 3 - tt * 0.5)
    corona = np.clip(0.8 / np.maximum(d - 5.0, 0.4) * rays * (0.75 + 0.25 * math.exp(-_beat(t) * 4)), 0, 1)
    m = (d > 5.2) & (dither(corona, x0, y0) | (d <= 6.2))
    m |= disc(h, w, cx + 5.8 * math.cos(tt * 0.5), cy + 5.8 * math.sin(tt * 0.5), 1.3)
    blit_mask(surface, x0, y0, m)


def dk_ripples(surface, rect, t, seed=0.0):
    """Slow drops landing in dark water, rings spreading out."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    m = np.zeros((h, w), bool)
    for k in range(3):
        u = (t + seed) / 3.4 + k / 3.0
        ci, q = int(u), u % 1.0
        cx, cy = 5 + hash01(ci, k + seed) * (w - 10), 5 + hash01(k + seed, ci) * (h - 9)
        if q < 0.12:                        # the drop on its way down
            y = int(cy * q / 0.12)
            _set(m, int(cx), y)
            _set(m, int(cx), y - 1)
            continue
        age = (q - 0.12) / 0.88
        rho = np.hypot((xx - cx) / 2.0, yy - cy)
        ring = np.abs(rho - age * 9) < 0.5
        if age > 0.3:
            ring |= np.abs(rho - (age * 9 - 2.5)) < 0.5
        m |= ring & dither(_flat(h, w, min(1.0, (1 - age) * 1.4)), x0, y0)
    blit_mask(surface, x0, y0, m)


def _skyline(w, h, seed):
    key = ("skyline", w, h, int(seed * 10))
    tops = _st.get(key)
    if tops is None:
        rng = np.random.RandomState(int(seed * 10) % 100000 + 5)
        tops = np.zeros(w, int)
        x = 0
        while x < w:
            bw = rng.randint(3, 7)
            tops[x:x + bw] = h - rng.randint(4, 12)
            x += bw
        _st[key] = tops
    return tops


def dk_skyline(surface, rect, t, seed=0.0):
    """A city at night: windows blinking on and off, a searchlight raking the sky."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    tops = _skyline(w, h, seed)
    roof = tops[None, :]
    m = yy == roof
    for x in range(1, w):                   # walls where the roofline steps
        lo, hi = sorted((tops[x - 1], tops[x]))
        m[lo:hi + 1, x if tops[x] < tops[x - 1] else x - 1] = True
    tw = (np.sin(xx * 12.9898 + yy * 78.233) * 43758.5453) % 1.0
    lit = (tw * 7 + np.floor(tt / 3 + tw * 5) * 0.37) % 1.0 < 0.4
    m |= (yy > roof + 1) & (xx % 2 == 1) & (yy % 2 == 1) & lit
    bx = int(np.argmin(tops))
    by = int(tops[bx])
    beam = np.zeros((h, w), bool)
    a = -math.pi / 2 + 1.0 * math.sin(tt * 0.6)
    draw_line(beam, bx, by - 1, bx + 40 * math.cos(a), by - 1 + 40 * math.sin(a))
    m |= beam & (yy < roof)
    blit_mask(surface, x0, y0, m)


def dk_glitch(surface, rect, t, seed=0.0):
    """A signal breaking up: slices tearing sideways, blocks flipping, worst on the beat."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    k = int((t + seed) * 8)
    s = _st.get(("glitch", x0, y0))
    if s is not None and s[0] == k:         # each break-up holds for an eighth of a second
        blit_mask(surface, x0, y0, s[1])
        return
    d = np.hypot(xx - (w / 2 - 0.5), yy - (h / 2 - 0.5))
    m = ((d <= 6.8) & (d > 5.2)) | (d <= 2.2)
    rng = noise_rng
    for _ in range(int(1 + 4 * math.exp(-_beat(t) * 3)) + 2 * (rng.rand() < 0.3)):
        y, n = rng.randint(0, h - 1), rng.randint(1, 4)
        m[y:y + n] = np.roll(m[y:y + n], rng.randint(-6, 7), axis=1)
    if rng.rand() < 0.35:
        x, y = rng.randint(0, w - 8), rng.randint(0, h - 3)
        m[y:y + 3, x:x + 8] ^= True
    m[k % h] |= rng.rand(w) < 0.25
    _st[("glitch", x0, y0)] = (k, m)
    blit_mask(surface, x0, y0, m)


def dk_spiral(surface, rect, t, seed=0.0):
    """A slow hypnotic spiral."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    dx, dy = xx - (w / 2 - 0.5), yy - (h / 2 - 0.5)
    blit_mask(surface, x0, y0, np.sin(2 * np.arctan2(dy, dx) + np.hypot(dx, dy) * 0.9 - (t + seed) * 1.6) > 0.15)


def dk_thorns(surface, rect, t, seed=0.0):
    """Thorned vines creeping across from both sides, then withering away."""
    x0, y0, w, h = rect
    tt = t + seed
    ci, c = int(tt / 9.0), tt % 9.0
    m = np.zeros((h, w), bool)
    for k, (base, freq) in enumerate(((5, 0.42), (11, 0.36))):
        prev = None
        for i in range(int(min(1.0, c / 5.5) * w)):
            x = i if k == 0 else w - 1 - i
            y = int(round(base + 2.5 * math.sin(x * freq + k * 2 + ci)))
            m[min(y, y if prev is None else prev):max(y, y if prev is None else prev) + 1, x] = True
            prev = y
            side = 1 if (x // 4) % 2 else -1
            if x % 4 == 1:                  # a thorn
                _set(m, x, y + side)
                _set(m, x + 1, y + 2 * side)
            if x % 9 == 5:                  # a leaf on the other side
                _set(m, x, y - side)
                _set(m, x - 1, y - 2 * side)
                _set(m, x, y - 2 * side)
    if c > 7.5:
        m &= dither(_flat(h, w, 1 - (c - 7.5) / 1.5), x0, y0)
    blit_mask(surface, x0, y0, m)


ANIMATIONS = [
    dk_storm, dk_eye, dk_moon, dk_crows, dk_smoke, dk_static, dk_streetlamp,
    dk_pendulum, dk_embers, dk_eclipse, dk_ripples, dk_skyline, dk_glitch,
    dk_spiral, dk_thorns,
]
