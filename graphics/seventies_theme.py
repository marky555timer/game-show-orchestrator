"""THE 70s -- idle theme for 1970s songs (2026-10-13).

Single-panel (32x16) animations for panels 3-6, dealt like any other idle
theme (graphics/animations.py IDLE_THEMES["70s"]). Songs released in the
1970s pick this theme automatically
(drivers/light_prefs_engine.py::resolve_idle_theme); the song's energy tag
then narrows the deal -- @for_energy("slow") ones only on slow songs,
@for_energy("fast") only on fast ones, unmarked on either.
"""
import math

import numpy as np

import config
from graphics.love_theme import _outline
from graphics.technical_dreams import (
    blit_mask, dither, _grid, _beat, _stamp, _st, _levels,
    sprite, draw_line, draw_box, disc, ellipse, plot, hash01, for_energy,
)


def _beat_index(t):
    return int(t / config.TEMPO_PERIOD_DEFAULT_SECONDS)


_DANCER = sprite(
    "........#",
    ".......#.",
    "..##..#..",
    "..##.#...",
    ".####....",
    "#.##.....",
    "#.##.....",
    "..###....",
    ".##.#....",
    ".#...#...",
    "##...##..",
)


@for_energy("fast")
def s70_dance_floor(surface, rect, t, seed=0.0):
    """Saturday night: one finger in the air over a floor of flashing tiles."""
    x0, y0, w, h = rect
    k = _beat_index(t)
    m = np.zeros((h, w), bool)
    pose = _DANCER if k % 2 else _DANCER[:, ::-1]
    _stamp(m, pose, 12 + (1 if k % 2 else -1), 1 if _beat(t) < 0.3 else 0)
    for r in range(2):
        for c in range(8):
            if hash01(k + seed, c + r * 8) > 0.5:
                m[12 + r * 2:14 + r * 2, c * 4:c * 4 + 3] = True
    blit_mask(surface, x0, y0, m)


_BOOT = sprite(
    "..####........",
    "..####........",
    "..#####.......",
    "..######......",
    "..##########..",
    "..############",
    "..############",
    "##############",
)
_SPARK = sprite(".#.", "###", ".#.")


@for_energy("fast")
def s70_skate(surface, rect, t, seed=0.0):
    """A roller skate cruising the rink, wheels spinning, stars flying off behind."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    m = np.zeros((h, w), bool)
    for i in range(3):
        ph = (tt * 0.9 + i / 3.0) % 1.0
        if ph < 0.7:
            _stamp(m, _SPARK, int(8 - ph * 14), int(9 - i * 4 + 2 * math.sin(tt * 2 + i)))
    x = 11 + int(round(2.5 * math.sin(tt * 1.3)))
    y = 2 + (1 if _beat(t) < 0.25 else 0)
    _stamp(m, _BOOT, x, y)
    for cx in (x + 3.5, x + 10.5):
        d = np.hypot(xx - cx, yy - (y + 10.5))
        th = np.arctan2(yy - (y + 10.5), xx - cx)
        m |= (d <= 2.5) & ((d > 1.4) | (np.cos(th - tt * 11) > 0.4))
    m[15] |= ((xx[0] + tt * 22) % 6) < 2
    blit_mask(surface, x0, y0, m)


@for_energy("slow")
def s70_reels(surface, rect, t, seed=0.0):
    """A reel-to-reel deck, the big spools turning at their own speeds."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    m = np.zeros((h, w), bool)
    for cx, speed in ((7.5, 1.5), (23.5, 2.4)):
        d = np.hypot(xx - cx, yy - 7)
        th = np.arctan2(yy - 7, xx - cx)
        m |= ((d <= 6.9) & (d > 5.9)) | (d <= 1.3)
        m |= (d <= 5.9) & (d > 1.3) & (np.cos(3 * (th - tt * speed)) > 0.25)
    m[15, 7:25] = True                               # tape running past the heads
    m[13:15, 14:18] = True
    blit_mask(surface, x0, y0, m)


def s70_vu(surface, rect, t, seed=0.0):
    """Two VU meters, needles kicking with the music."""
    x0, y0, w, h = rect
    m = np.zeros((h, w), bool)
    for i, lv in enumerate(_levels(t, 2, seed)):
        left = i * 16
        draw_box(m, left, 0, 16, 16)
        px, py = left + 7.5, 13.5
        for j in range(7):
            a = math.radians(-48 + 16 * j)
            plot(m, [px + 10 * math.sin(a)], [py - 10 * math.cos(a)])
            if j >= 5:                               # the red end of the scale
                plot(m, [px + 9 * math.sin(a)], [py - 9 * math.cos(a)])
        a = math.radians(-48 + 96 * lv)
        draw_line(m, px, py, px + 8.5 * math.sin(a), py - 8.5 * math.cos(a))
        m[13:15, left + 7:left + 9] = True
    blit_mask(surface, x0, y0, m)


@for_energy("slow")
def s70_cradle(surface, rect, t, seed=0.0):
    """A desk-top clacker: one steel ball swings in, the far one swings out."""
    x0, y0, w, h = rect
    p = (t / (2 * config.TEMPO_PERIOD_DEFAULT_SECONDS)) % 1.0
    m = np.zeros((h, w), bool)
    m[0, 1:31] = True
    m[:, 1] = m[:, 30] = True
    m[15, 0:32] = True
    for i in range(5):
        a = 0.0
        if i == 0 and p < 0.5:
            a = -0.85 * math.sin(2 * math.pi * p)
        elif i == 4 and p >= 0.5:
            a = 0.85 * math.sin(2 * math.pi * (p - 0.5))
        ax = 9.5 + i * 3
        bx, by = ax + 9.5 * math.sin(a), 1 + 9.5 * math.cos(a)
        draw_line(m, ax, 1, bx, by)
        m |= disc(h, w, bx, by + 1, 1.6)
    blit_mask(surface, x0, y0, m)


@for_energy("fast")
def s70_chopper(surface, rect, t, seed=0.0):
    """A long-forked chopper thumping down the highway."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    b = 1 if math.sin(tt * 7) > 0.6 else 0
    m = np.zeros((h, w), bool)
    for cx, cy, r in ((7, 10.5, 3.6), (26.5, 11, 3.1)):
        d = np.hypot(xx - cx, yy - cy)
        th = np.arctan2(yy - cy, xx - cx)
        m |= (d <= r) & ((d > r - 1.1) | (np.abs(np.cos(th - tt * 9)) > 0.93))
    draw_line(m, 7, 10, 13, 7 - b)                   # frame
    m[8 - b:12 - b, 12:17] = True                    # engine
    m[6 - b, 12:19] = True                           # tank
    draw_line(m, 26, 11, 20, 3 - b)                  # fork
    draw_line(m, 20, 3 - b, 17, 2 - b)               # bars
    m |= disc(h, w, 9, 1.3, 1.5)                     # rider, leaning well back
    draw_line(m, 9, 3, 11, 5 - b)
    draw_line(m, 10, 3, 12, 5 - b)
    draw_line(m, 10, 3, 17, 2 - b)
    for i in range(3):                               # exhaust
        ph = (tt * 1.6 + i / 3.0) % 1.0
        plot(m, [4 - ph * 6, 3 - ph * 6], [13 - ph * 4, 13 - ph * 4])
    m[15] = ((xx[0] + tt * 30) % 8) < 4
    blit_mask(surface, x0, y0, m)


def s70_strut(surface, rect, t, seed=0.0):
    """Keep on truckin': a bell-bottomed strut, big shoes leading the way."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    a = math.pi * t / config.TEMPO_PERIOD_DEFAULT_SECONDS    # a stride every two beats
    hipx, hipy = 14.0, 7.0 + 0.6 * abs(math.cos(a))
    m = np.zeros((h, w), bool)
    for s in (1, -1):
        th = 0.75 * s * math.sin(a)
        kx, ky = hipx + 3.6 * math.sin(th), hipy + 3.6 * math.cos(th)
        th2 = th - 0.7 * max(0.0, -s * math.cos(a))          # the trailing leg bends
        fx, fy = kx + 3.6 * math.sin(th2), min(14.0, ky + 3.6 * math.cos(th2))
        draw_line(m, hipx, hipy, kx, ky)
        draw_line(m, hipx + 1, hipy, kx + 1, ky)
        draw_line(m, kx, ky, fx - 1, fy)
        draw_line(m, kx + 1, ky, fx + 2, fy)                  # the flare
        m[int(round(fy)) + 1, max(0, int(fx) - 1):int(fx) + 5] = True   # shoe
        hx = 12.5 - 4.2 * math.sin(th * 1.1)
        draw_line(m, 12.5, 3.5, hx, 3.5 + 4.2 * math.cos(th * 1.1))
    draw_line(m, hipx, hipy, 12.5, 3)
    draw_line(m, hipx + 1, hipy, 13.5, 3)
    m |= disc(h, w, 12, 1.2, 1.5)
    m[15] |= ((xx[0] + tt * 12) % 7) < 3
    blit_mask(surface, x0, y0, m)


@for_energy("slow")
def s70_rainbow(surface, rect, t, seed=0.0):
    """A rainbow between two clouds, colour streaming along each band."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    dx, dy = xx - 15.5, yy - 14.5
    d = np.hypot(dx, dy)
    th = np.arctan2(-dy, dx)
    m = np.zeros((h, w), bool)
    for i, r in enumerate((13.5, 10.5, 7.5)):
        flow = ((th * r + (1 if i % 2 else -1) * tt * 5) % 9) < 7.5
        m |= (np.abs(d - r) < 0.8) & flow
    for i, cx in enumerate((4, 27)):
        bob = 0.6 * math.sin(tt * 1.2 + i * 2)
        cloud = (disc(h, w, cx, 12.5 + bob, 2.8) | disc(h, w, cx + 3, 13.5 + bob, 2.2)
                 | disc(h, w, cx - 3, 13.5 + bob, 2.2))
        m &= ~cloud
        m |= _outline(cloud)
    blit_mask(surface, x0, y0, m)


_COW = sprite(".####", "#####", ".#.#.")


def s70_saucer(surface, rect, t, seed=0.0):
    """A flying saucer hovering about, lights chasing, beaming something up."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    sx = 15.5 + 5 * math.sin(tt * 0.7) + 2 * math.sin(tt * 1.9)
    sy = 3.5 + 1.2 * math.sin(tt * 1.3)
    beam = (yy > sy + 2) & (np.abs(xx - sx) <= 1.5 + (yy - sy - 2) * 0.45)
    m = beam & dither(np.full((h, w), 0.3 + 0.3 * math.exp(-_beat(t) * 4), np.float32), x0, y0)
    cy = int(15 - (tt * 2.5) % 11)
    m[max(0, cy - 1):cy + 4, max(0, int(sx) - 3):int(sx) + 4] = False
    _stamp(m, _COW, int(sx) - 2, cy)
    m |= _outline(disc(h, w, sx, sy - 0.3, 3.2) & (yy <= sy))
    hull = ellipse(h, w, sx, sy + 1.2, 8.5, 1.7)
    m |= hull
    for j in range(6):
        if (j + int(tt * 6)) % 3 == 0:
            lx, ly = int(round(sx - 6.2 + j * 2.5)), int(round(sy + 1.2))
            if 0 <= lx < w and 0 <= ly < h:
                m[ly, lx] = False
    blit_mask(surface, x0, y0, m)


_BUMPERS = ((7.0, 5.0), (16.0, 3.5), (25.0, 6.0))


def _pinball_step(s, dt):
    s["vy"] += 55 * dt
    s["x"] += s["vx"] * dt
    s["y"] += s["vy"] * dt
    if s["x"] < 1:
        s["x"], s["vx"] = 1, abs(s["vx"])
    if s["x"] > 30:
        s["x"], s["vx"] = 30, -abs(s["vx"])
    if s["y"] < 0.5:
        s["y"], s["vy"] = 0.5, abs(s["vy"]) * 0.8
    for i, (bx, by) in enumerate(_BUMPERS):
        dx, dy = s["x"] - bx, s["y"] - by
        d = math.hypot(dx, dy)
        if 0 < d < 3.4:
            speed = max(20.0, math.hypot(s["vx"], s["vy"]))
            s["vx"], s["vy"] = dx / d * speed, dy / d * speed
            s["x"], s["y"] = bx + dx / d * 3.4, by + dy / d * 3.4
            s["flash"][i] = 0.18
    if s["y"] > 12.5 and s["vy"] > 0:                # the flippers send it back up
        s["y"] = 12.5
        s["vy"] = -(27 + 9 * s["rng"].rand())
        s["vx"] = s["rng"].uniform(-16, 16)
        s["flip"] = 0.18
        s["side"] = 0 if s["x"] < 16 else 1


@for_energy("fast")
def s70_pinball(surface, rect, t, seed=0.0):
    """A pinball rattling between the bumpers, flippers batting it back."""
    x0, y0, w, h = rect
    s = _st.get(("pinball", x0, y0))
    if s is None:
        s = _st[("pinball", x0, y0)] = {"t": t, "x": 12.0, "y": 8.0, "vx": 11.0, "vy": 0.0, "flip": 0.0,
                                        "side": 0, "flash": [0.0, 0.0, 0.0],
                                        "rng": np.random.RandomState(int(seed * 100) % 100000)}
    dt = max(0.0, min(0.2, t - s["t"]))
    s["t"] = t
    for _ in range(4):
        _pinball_step(s, dt / 4)
    s["flip"] = max(0.0, s["flip"] - dt)
    m = np.zeros((h, w), bool)
    for i, (bx, by) in enumerate(_BUMPERS):
        s["flash"][i] = max(0.0, s["flash"][i] - dt)
        ring = disc(h, w, bx, by, 2.4)
        m |= ring if s["flash"][i] > 0 else _outline(ring)
    up = s["flip"] > 0
    draw_line(m, 7, 13, 14, 12 if up and s["side"] == 0 else 15)
    draw_line(m, 24, 13, 17, 12 if up and s["side"] == 1 else 15)
    bx, by = int(s["x"]), int(s["y"])
    m[max(0, by):by + 2, max(0, bx):bx + 2] = True
    blit_mask(surface, x0, y0, m)


def s70_sunburst(surface, rect, t, seed=0.0):
    """A rising sun, its rays wheeling round."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    dx, dy = xx - 15.5, yy - 15.5
    d = np.hypot(dx, dy)
    th = np.arctan2(-dy, dx)
    r = 6.2 + 1.0 * math.exp(-_beat(t) * 5)
    m = ((d <= r) & (d > r - 1.3)) | (d <= r - 2.6)
    m |= (d > r + 1.2) & (np.cos(12 * (th - tt * 0.3)) > 0.15)
    blit_mask(surface, x0, y0, m)


@for_energy("slow")
def s70_fiber(surface, rect, t, seed=0.0):
    """A fibre-optic lamp: a spray of swaying strands with glowing tips."""
    x0, y0, w, h = rect
    tt = t + seed
    m = np.zeros((h, w), bool)
    m[14:16, 13:19] = True
    m[7:14, 15:17] = True                            # the bundle coming up out of the base
    u = np.linspace(0.0, 1.0, 18)
    for i in range(9):
        sp = (i - 4) / 4.0
        tx = 15.5 + 13 * sp + 2.5 * math.sin(tt * 0.8 + i * 0.7)
        ty = 2 + 9 * sp * sp + math.sin(tt * 1.1 + i)
        cx, cy = 15.5 + 5 * sp, -2.0
        xs = (1 - u) ** 2 * 15.5 + 2 * u * (1 - u) * cx + u * u * tx
        ys = (1 - u) ** 2 * 13.0 + 2 * u * (1 - u) * cy + u * u * ty
        plot(m, xs[7:], ys[7:])
        if math.sin(tt * 3 + i * 2.1) > -0.2:        # tips twinkle
            plot(m, [tx - 1, tx + 1, tx, tx], [ty, ty, ty - 1, ty + 1])
    blit_mask(surface, x0, y0, m)


def s70_disco_lights(surface, rect, t, seed=0.0):
    """A mirror ball overhead, throwing beams that sweep the room."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    dx, dy = xx - 15.5, yy - 3
    d = np.hypot(dx, dy)
    th = np.arctan2(dy, dx / 1.6)
    ball = d <= 3.3
    m = _outline(ball) | (ball & ((xx + yy + int(tt * 5)) % 2 == 0))
    m[0, 15:17] = True
    m |= (d > 4.6) & (np.cos(5 * (th - tt * 0.8)) > 0.86)
    m |= (d > 4.6) & (np.cos(3 * (th + tt * 0.55)) > 0.95)
    blit_mask(surface, x0, y0, m)


@for_energy("fast")
def s70_simon(surface, rect, t, seed=0.0):
    """The follow-the-lights game: one pad after another flashing on the beat."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    k, ph = _beat_index(t), _beat(t)
    dx, dy = (xx - 15.5) / 1.7, yy - 7.5
    d = np.hypot(dx, dy)
    gap = (np.abs(xx - 15.5) < 1) | (np.abs(dy) < 1)
    m = (d <= 7.7) & (d > 6.6) & ~gap
    m |= (d <= 2.4) & (d > 1.3)
    if ph < 0.7:
        pad = int(hash01(k, int(seed * 10)) * 4) % 4
        m |= (d <= 6.6) & (d > 3.2) & ~gap & ((dx > 0) == (pad % 2 == 1)) & ((dy > 0) == (pad >= 2))
    blit_mask(surface, x0, y0, m)


ANIMATIONS = [
    s70_dance_floor, s70_skate, s70_reels, s70_vu, s70_cradle, s70_chopper,
    s70_strut, s70_rainbow, s70_saucer, s70_pinball, s70_sunburst, s70_fiber,
    s70_disco_lights, s70_simon,
]
