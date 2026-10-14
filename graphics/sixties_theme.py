"""THE 60s -- idle theme for 1960s songs (2026-10-13).

Single-panel (32x16) animations for panels 3-6, dealt like any other idle
theme (graphics/animations.py IDLE_THEMES["60s"]). Songs released in the
1960s pick this theme automatically
(drivers/light_prefs_engine.py::resolve_idle_theme); the song's energy tag
then narrows the deal -- @for_energy("slow") ones only on slow songs,
@for_energy("fast") only on fast ones, unmarked on either.
"""
import math

import numpy as np

import config
from graphics.love_theme import _outline
from graphics.technical_dreams import (
    blit_mask, _grid, _beat, _stamp, text_mask,
    sprite, draw_line, draw_box, disc, ellipse, plot, noise_rng, for_energy,
)


def _beat_index(t):
    return int(t / config.TEMPO_PERIOD_DEFAULT_SECONDS)


@for_energy("slow")
def s60_peace(surface, rect, t, seed=0.0):
    """A peace sign turning slowly, like a medallion on a chain."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    u = (xx - 15.5) / max(abs(math.cos(tt * 0.9)), 0.12) / 1.5
    v = yy - 7.5
    d = np.hypot(u, v)
    m = (d <= 7.7) & (d > 6.1)
    inner = d <= 6.1
    m |= (np.abs(xx - 15.5) < 1) & (np.abs(v) <= 7.7)
    m |= inner & (v > 0) & (np.abs(np.abs(u) - v) < 0.9)
    blit_mask(surface, x0, y0, m)


def s60_flowers(surface, rect, t, seed=0.0):
    """Flower power: two big daisies nodding on their stems, petals turning."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    m = np.zeros((h, w), bool)
    for i, (bx, hy, r) in enumerate(((8, 5.5, 5.4), (23, 5.5, 5.4))):
        sway = 2.2 * math.sin(tt * (0.9 + 0.2 * i) + i * 1.7)
        ys = np.arange(int(hy), h)
        f = (15 - ys) / (15.0 - hy)
        plot(m, bx + sway * f * f, ys)
        m[13, bx + (1 if i % 2 else -3):bx + (4 if i % 2 else 0)] = True       # leaf
        hx = bx + sway
        d = np.hypot(xx - hx, yy - hy)
        th = np.arctan2(yy - hy, xx - hx)
        spin = tt * 0.6 * (1 if i % 2 else -1)
        m &= ~(d <= r)
        m |= (d <= r) & (d > 2.2) & (np.cos(6 * (th - spin)) > -0.2)
        m |= d <= 1.2
    blit_mask(surface, x0, y0, m)


def s60_liquid(surface, rect, t, seed=0.0):
    """A liquid light show: oil and dye oozing across the projector."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    f = np.zeros((h, w), np.float32)
    for k in range(4):
        cx = 15.5 + 13 * math.sin(tt * (0.31 + 0.07 * k) + k * 1.9)
        cy = 7.5 + 6 * math.cos(tt * (0.43 + 0.05 * k) + k * 2.6)
        f += 9.0 / ((xx - cx) ** 2 * 0.45 + (yy - cy) ** 2 + 4.0)
    blit_mask(surface, x0, y0, ((f * 1.2) % 1.0) < 0.5)


@for_energy("fast")
def s60_op_art(surface, rect, t, seed=0.0):
    """Op art: wobbling rings that seem to breathe off the wall."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    dx, dy = (xx - 15.5) / 1.7, yy - 7.5
    d = np.hypot(dx, dy)
    th = np.arctan2(dy, dx)
    blit_mask(surface, x0, y0, np.sin(d * 1.9 - tt * 5 + 1.6 * np.sin(th * 3 + tt * 1.3)) > 0)


_BUS = sprite(
    ".####################.",
    "#....#....#....#.....#",
    "#....#....#....#.....#",
    "######################",
    "######################",
    "######################",
    "##...############...##",
)
_DAISY = sprite(".#.", "#.#", ".#.")


@for_energy("fast")
def s60_bus(surface, rect, t, seed=0.0):
    """A camper van bouncing over the hills, daisies in its wake."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    xs = np.arange(w)
    ground = 13.6 + 1.4 * np.sin((xs + tt * 14) * 0.25)
    m = np.zeros((h, w), bool)
    plot(m, xs, ground)
    by = int(round(min(ground[11], ground[26]))) - 10
    _stamp(m, _BUS, 8, by)
    for cx in (11.5, 26.5):
        d = np.hypot(xx - cx, yy - (by + 7.5))
        th = np.arctan2(yy - (by + 7.5), xx - cx)
        m |= (d <= 2.3) & ((d > 1.3) | (np.cos(th - tt * 8) > 0.5))
    for i in range(3):
        ph = (tt * 0.5 + i / 3.0) % 1.0
        _stamp(m, _DAISY, int(6 - ph * 9), int(by + 5 - ph * 7 + 1.5 * math.sin(tt * 3 + i * 2)))
    blit_mask(surface, x0, y0, m)


_LM = sprite(
    "...####...",
    "..######..",
    "..##.###..",
    "..######..",
    ".########.",
    ".########.",
    "..#.##.#..",
    ".#..##..#.",
    "##......##",
)


@for_energy("slow")
def s60_lander(surface, rect, t, seed=0.0):
    """The Moon landing: down it comes, out goes the flag, and away again."""
    x0, y0, w, h = rect
    c = (t + seed) % 14.0
    m = np.zeros((h, w), bool)
    for sx, sy in ((2, 2), (8, 5), (21, 1), (30, 7), (14, 3), (25, 6)):
        m[sy, sx] = True
    m |= disc(h, w, 27, 3, 2.6) & ~disc(h, w, 28.3, 2.4, 2.3)      # the Earth, a crescent
    m[14] = True
    m[14, 3:7] = m[14, 24:29] = False                              # craters
    m[15, 3:7] = m[15, 24:29] = True
    burn = False
    if c < 6:
        f = c / 6.0
        y = -10 + (1 - (1 - f) ** 2) * 15
        x = 11 + round(2 * math.sin(c * 1.6) * (1 - f))
        burn = True
    elif c < 10:
        x, y = 11, 5
    else:
        f = (c - 10) / 4.0
        x, y, burn = 11, 5 - f * f * 17, True
    y = int(round(y))
    _stamp(m, _LM, x, y)
    if burn:
        n = 1 + noise_rng.randint(0, 3)
        for r in range(y + 8, min(14, y + 8 + n)):
            if r >= 0:
                m[r, x + 4:x + 6] = True
    if c > 7:
        m[9:14, 22] = True
        wave = 1 if math.sin(c * 5) > 0 else 0
        m[9, 23:26] = True
        m[10, 23 + wave:26 + wave] = True
    blit_mask(surface, x0, y0, m)


@for_energy("fast")
def s60_twist(surface, rect, t, seed=0.0):
    """Two dancers doing the Twist: shoulders one way, hips the other."""
    x0, y0, w, h = rect
    a = math.pi * t / config.TEMPO_PERIOD_DEFAULT_SECONDS
    m = np.zeros((h, w), bool)
    for i, cx in enumerate((9, 22)):
        s, c = math.sin(a + i * math.pi), math.cos(a + i * math.pi)
        sx, hx, kx = cx + 1.6 * s, cx - 1.6 * s, cx + 2.4 * s
        m |= disc(h, w, sx, 2, 1.7)
        draw_line(m, sx - 3, 5, sx + 3, 5)
        draw_line(m, sx, 5, hx, 9)
        draw_line(m, sx - 3, 5, sx - 5 + 2 * c, 8)
        draw_line(m, sx + 3, 5, sx + 5 + 2 * c, 8)
        for side in (-1, 1):
            draw_line(m, hx + side, 9, kx + side * 2, 12)
            draw_line(m, kx + side * 2, 12, cx + side * 2, 15)
    blit_mask(surface, x0, y0, m)


_MOP = sprite(
    "..###..",
    ".#####.",
    "#######",
    "#######",
    "#.#.#.#",
    "#.....#",
    ".#...#.",
    "..###..",
)
_SUIT = sprite(".#####.", "###.###", "###.###", "###.###", "###.###", "###.###", "###.###", "###.###")


@for_energy("fast")
def s60_mop_tops(surface, rect, t, seed=0.0):
    """Four mop-topped lads shaking their heads -- yeah, yeah, yeah."""
    x0, y0, w, h = rect
    k, ph = _beat_index(t), _beat(t)
    m = np.zeros((h, w), bool)
    for i in range(4):
        x = 1 + i * 8
        _stamp(m, _SUIT, x, 9)
        shake = (1 if (k + i) % 2 else -1) if ph < 0.5 else 0
        if k % 8 == 7:
            shake = 1 if int(ph * 6) % 2 else -1    # the big "wooo" head shake
        _stamp(m, _MOP, x + shake, 1 if ph < 0.2 else 0)
    blit_mask(surface, x0, y0, m)


_SURFER = sprite("..##.", "..##.", "#####", "..#..", "..#..", ".#.#.", "#...#")


def s60_surf(surface, rect, t, seed=0.0):
    """A surfer riding the swell."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    xs = np.arange(w)
    hgt = 9.5 + 2.6 * np.sin(xs * 0.26 - tt * 2.2) + 1.1 * np.sin(xs * 0.6 + tt * 1.3)
    top = np.floor(hgt)[None, :]
    m = (yy == top) | ((yy > top) & ((xx + 2 * yy + int(tt * 7)) % 5 == 0))
    by = int(round(hgt[15] - 1.5))
    slope = (hgt[19] - hgt[11]) / 8.0
    m[max(0, by - 7):max(0, by + 1), 10:21] = False
    draw_line(m, 10, by - slope * 4, 20, by + slope * 4)           # board
    _stamp(m, _SURFER, 13 - int(round(slope * 2)), by - 7)
    blit_mask(surface, x0, y0, m)


_SUB = sprite(
    "........#.........",
    "........##........",
    "......#####.......",
    "......#####.......",
    "..##############..",
    ".###.###.###.####.",
    ".################.",
    "..##############..",
)


@for_energy("slow")
def s60_submarine(surface, rect, t, seed=0.0):
    """A little submarine puttering along under the sea, weeds swaying below."""
    x0, y0, w, h = rect
    tt = t + seed
    m = np.zeros((h, w), bool)
    for i, bx in enumerate((3, 9, 21, 28)):          # seaweed
        ys = np.arange(13 - i % 2, 16)
        plot(m, bx + (15 - ys) * 0.5 * np.sin(tt * 1.4 + i * 1.3 + ys * 0.5), ys)
    sx = 8 + int(round(2.5 * math.sin(tt * 0.5)))
    sy = 2 + int(round(1.4 * math.sin(tt * 0.8)))
    _stamp(m, _SUB, sx, sy)
    if int(tt * 8) % 2:                              # propeller
        m[sy + 4:sy + 8, sx - 1] = True
    else:
        m[sy + 5:sy + 7, sx - 2:sx] = True
    for i in range(4):                               # bubbles
        ph = (tt * 0.5 + i / 4.0) % 1.0
        plot(m, [sx - 3 - ph * 6 + 1.2 * math.sin(tt * 3 + i * 2)], [sy + 5 - ph * 10])
    blit_mask(surface, x0, y0, m)


_WORDS = ("LOVE", "PEACE", "FAB")


def s60_groovy(surface, rect, t, seed=0.0):
    """LOVE... PEACE... FAB -- lettering that ripples like a poster."""
    x0, y0, w, h = rect
    tt = t + seed
    word = _WORDS[int(tt / 6.0) % len(_WORDS)]
    tm = text_mask(word, tight=len(word) > 5)
    left = (w - tm.shape[1]) // 2
    off = np.rint(4.5 + 2 * np.sin(np.arange(w) * 0.2 - tt * 2.5)).astype(int)
    m = np.zeros((h, w), bool)
    for x in range(w):
        m[off[x] - 2, x] = True
        m[off[x] + 8, x] = True
        if left <= x < left + tm.shape[1]:
            m[off[x]:off[x] + 7, x] = tm[:, x - left]
    blit_mask(surface, x0, y0, m)


@for_energy("fast")
def s60_kaleido(surface, rect, t, seed=0.0):
    """A kaleidoscope, forever folding into new patterns."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    ax, ay = np.abs(xx - 15.5) / 2.0, np.abs(yy - 7.5)
    u, v = np.maximum(ax, ay), np.minimum(ax, ay)
    f = np.sin(u * 1.3 - tt * 2.6) + np.sin(v * 1.9 + tt * 1.7) + np.sin((u + v) * 0.8 + tt * 0.9)
    blit_mask(surface, x0, y0, f > 0.35)


@for_energy("fast")
def s60_drums(surface, rect, t, seed=0.0):
    """A drum kit playing itself: kick on the beat, hi-hat chattering, cymbal rocking."""
    x0, y0, w, h = rect
    tt = t + seed
    k, ph = _beat_index(t), _beat(t)
    kick = math.exp(-ph * 6)
    m = _outline(disc(h, w, 15.5, 10, 5.5)) | disc(h, w, 15.5, 10, 0.8 + 2.8 * kick)
    draw_box(m, 9, 1, 6, 4)
    draw_box(m, 17, 1, 6, 4)
    draw_box(m, 23, 8, 7, 4)                         # snare
    m[12:16, 24] = m[12:16, 28] = True
    m[6:16, 4] = True                                # hi-hat
    open_ = 1 if 0.5 <= ph < 0.75 else 0
    m[6, 1:8] = True
    m[5 - open_, 1:8] = True
    tilt = 1.8 * math.sin(tt * 10) * math.exp(-((k % 4) + ph) * 0.9)
    draw_line(m, 25, 2 + tilt, 31, 2 - tilt)         # crash
    m[3:8, 28] = True
    hit = (k % 2 == 1) and ph < 0.25
    draw_line(m, 21, 4, 25, 7 if hit else 5)
    blit_mask(surface, x0, y0, m)


@for_energy("slow")
def s60_dove(surface, rect, t, seed=0.0):
    """A dove on the wing, olive branch in its beak."""
    x0, y0, w, h = rect
    tt = t + seed
    bx = 15.5 + 5 * math.sin(tt * 0.4)
    by = 8 + 2 * math.sin(tt * 0.9)
    m = np.zeros((h, w), bool)
    for i in range(2):                               # clouds sliding by
        cx = int((i * 19 - tt * (3 + i)) % (w + 10)) - 5
        m[1 + i * 13, max(0, cx):max(0, cx + 5)] = True
    m |= ellipse(h, w, bx, by, 3.8, 2.2) | disc(h, w, bx + 4.2, by - 1.6, 1.6)
    draw_line(m, bx + 6, by - 1.6, bx + 7, by - 1.6)
    draw_line(m, bx + 8, by - 3, bx + 9, by - 1)     # olive branch
    for ty in (-2, -1, 0, 1):                        # fan tail
        draw_line(m, bx - 3, by, bx - 7, by + ty)
    flap = math.sin(tt * 5)
    for back, reach in ((1.5, 5.0), (0.0, 7.0)):     # far wing, then the near one
        tx, ty = bx - 3.5 + back + 2 * abs(flap), by - 1 - reach * flap
        for sx in np.linspace(-2.5, 2.5, 6):
            draw_line(m, bx + sx + back, by - 1, tx, ty)
    blit_mask(surface, x0, y0, m)


ANIMATIONS = [
    s60_peace, s60_flowers, s60_liquid, s60_op_art, s60_bus, s60_lander,
    s60_twist, s60_mop_tops, s60_surf, s60_submarine, s60_groovy, s60_kaleido,
    s60_drums, s60_dove,
]
