"""THE 50s -- idle theme for 1950s songs (2026-10-13).

Single-panel (32x16) animations for panels 3-6, dealt like any other idle
theme (graphics/animations.py IDLE_THEMES["50s"]). Songs released in the
1950s pick this theme automatically
(drivers/light_prefs_engine.py::resolve_idle_theme); the song's energy tag
then narrows the deal -- @for_energy("slow") ones only on slow songs,
@for_energy("fast") only on fast ones, unmarked on either.
"""
import math

import numpy as np

import config
from graphics.love_theme import _outline
from graphics.technical_dreams import (
    blit_mask, _grid, _beat, _stamp, _st, _levels, text_mask,
    sprite, draw_line, draw_box, disc, ellipse, plot, noise_rng, hash01, for_energy,
)

_NOTE = sprite(".###", ".#..", ".#..", "##..", "##..")
_HEART = sprite(".#.#.", "#####", ".###.", "..#..")


def _beat_index(t):
    return int(t / config.TEMPO_PERIOD_DEFAULT_SECONDS)


def _swing(t, beats=2.0):
    """Smooth -1..1 swing, there and back once every `beats` beats."""
    return math.sin(2 * math.pi * t / (config.TEMPO_PERIOD_DEFAULT_SECONDS * beats))


def f50_jukebox(surface, rect, t, seed=0.0):
    """An arch-top jukebox: bubbles climbing the tubes, a record turning, the grille thumping."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    dx = np.abs(xx - 15.5)
    d = np.hypot(dx, yy - 7)
    r = np.where(yy < 7, d, dx)                       # distance out from the arch's spine
    m = (r <= 7.6) & (r > 6.6)
    path = np.where(yy < 7, 8 + np.arctan2(7 - yy, dx) * 6.0, 15 - yy)
    m |= (r <= 6.6) & (r > 5.4) & (((path - tt * 7) % 4) < 1.6)
    th = np.arctan2(yy - 7, xx - 15.5)
    m |= (yy < 7) & (d <= 4.2) & ((d > 3.2) | (np.cos(2 * (th - tt * 3)) > 0.6))
    m[7, 11:21] = True
    for i, lv in enumerate(_levels(t, 4, seed)):
        m[13 - int(lv * 4.99):14, 12 + i * 2] = True
    m[15, 7:25] = True
    blit_mask(surface, x0, y0, m)


def f50_record(surface, rect, t, seed=0.0):
    """A 45 spinning under the tone arm, notes drifting off it."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    d = np.hypot(xx - 9.5, yy - 7.5)
    th = np.arctan2(yy - 7.5, xx - 9.5)
    a = tt * 3.5
    m = (d <= 7.9) & (d > 6.9)
    m |= (d <= 3.6) & (d > 1.9) & (np.cos(th - a) < 0.75)          # label, with a notch
    m |= (np.abs(d - 5.3) < 0.6) & (np.cos(3 * (th - a)) > 0.35)   # groove glints
    hx = 18.5 + 1.2 * math.sin(tt * 1.5)
    draw_line(m, 29, 1, hx, 8)
    m[1:3, 28:31] = True
    m[8:10, int(hx) - 1:int(hx) + 1] = True
    k, ph = _beat_index(t), _beat(t)
    if ph < 0.9:
        _stamp(m, _NOTE, 22 + (k % 3) * 3, int(11 - ph * 9))
    blit_mask(surface, x0, y0, m)


@for_energy("fast")
def f50_jive(surface, rect, t, seed=0.0):
    """A couple jiving: hands swinging, skirt and ponytail flying."""
    x0, y0, w, h = rect
    k, ph = _beat_index(t), _beat(t)
    sw = _swing(t)
    hop = 1 if ph < 0.35 else 0
    bh = 1 if 0.5 <= ph < 0.85 else 0
    step = k % 2
    gx, bx = 9, 22
    m = np.zeros((h, w), bool)
    # her
    m |= disc(h, w, gx, 3 - hop, 1.6)
    draw_line(m, gx - 2, 3 - hop, gx - 4, 5 - hop - round(sw * 1.5))
    draw_line(m, gx, 5 - hop, gx, 8 - hop)
    for r in range(4):
        c = gx + round(sw * 1.7 * r / 3)
        m[9 - hop + r, max(0, c - 1 - r):c + 2 + r] = True
    draw_line(m, gx - 1, 13 - hop, gx - (3 if step else 1), 15)
    draw_line(m, gx + 1, 13 - hop, gx + (1 if step else 3), 15)
    # him
    m |= disc(h, w, bx, 3 - bh, 1.6)
    m[1 - bh, bx:bx + 3] = True
    m[5 - bh:10, bx - 1:bx + 2] = True
    draw_line(m, bx - 1, 10, bx - (1 if step else 3), 15)
    draw_line(m, bx + 1, 10, bx + (3 if step else 1), 15)
    # joined hands swing between them, free arms counter-swing
    hy = 7 + round(3 * sw)
    draw_line(m, gx, 6 - hop, 15, hy)
    draw_line(m, bx - 1, 6 - bh, 16, hy)
    draw_line(m, gx, 6 - hop, gx - 4, 7 - round(2 * sw))
    draw_line(m, bx + 1, 6 - bh, bx + 5, 7 + round(2 * sw))
    blit_mask(surface, x0, y0, m)


_CAR = sprite(
    "#.........................",
    "##.......########.........",
    "###.....##......##........",
    "#########........########.",
    "##########################",
    "##########################",
    ".###...############...###.",
)


@for_energy("fast")
def f50_tailfin(surface, rect, t, seed=0.0):
    """A tail-finned cruiser rolling down the highway."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    m = np.zeros((h, w), bool)
    for i in range(3):                               # the night air going by
        sx = int((i * 13 - tt * (30 + 7 * i)) % (w + 10)) - 5
        m[i, max(0, sx):max(0, sx + 4)] = True
    bob = 1 if math.sin(tt * 5.5) > 0.7 else 0
    _stamp(m, _CAR, 3, 4 - bob)
    for cx in (8, 23):
        d = np.hypot(xx - cx, yy - 12)
        th = np.arctan2(yy - 12, xx - cx)
        m |= (d <= 2.6) & ((d > 1.5) | (np.cos(th - tt * 9) > 0.5))
    m[15] = ((xx[0] + tt * 26) % 8) < 4
    blit_mask(surface, x0, y0, m)


@for_energy("slow")
def f50_milkshake(surface, rect, t, seed=0.0):
    """A milkshake with a cherry on top, sloshing gently, hearts floating up either side."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    glass = (yy >= 5) & (yy <= 13) & (np.abs(xx - 15.5) <= 5.6 - (yy - 5) * 0.32)
    liquid = glass & (yy >= 8.0 + 1.0 * np.sin(xx * 0.8 + tt * 2.5))
    m = _outline(glass) | liquid
    m |= ellipse(h, w, 15.5, 4.4, 4.8, 2.2) & (yy < 5)                 # whipped cream
    m |= disc(h, w, 15.5, 1.0, 1.2)                                    # cherry
    m[14, 15:17] = True
    m[15, 12:20] = True
    draw_line(m, 19, 5, 22, 0)                                         # straw
    m[0, 22:26] = True
    for i, hx in enumerate((2, 25)):
        ph = (tt * 0.22 + i * 0.5) % 1.0
        _stamp(m, _HEART, hx + round(1.2 * math.sin(tt * 1.6 + i * 2)), int(15 - ph * 20))
    blit_mask(surface, x0, y0, m)


_ROCKET = sprite(
    "###.............",
    ".####...........",
    "..#########.....",
    "..############..",
    "..#####..#######",
    "..############..",
    "..#########.....",
    ".####...........",
    "###.............",
)


@for_energy("fast")
def f50_rocket(surface, rect, t, seed=0.0):
    """A space-age rocket riding its flame through the stars."""
    x0, y0, w, h = rect
    tt = t + seed
    m = np.zeros((h, w), bool)
    for i in range(7):
        sx = int((hash01(i, 3) * 40 - tt * (9 + 5 * i)) % (w + 6)) - 3
        row = int(hash01(i, 9) * h)
        m[row, max(0, sx):max(0, sx + 1 + i % 3)] = True
    ry = int(round(3.5 + 2.4 * math.sin(tt * 1.7)))
    rx = 12 + int(round(1.5 * math.sin(tt * 0.9)))
    _stamp(m, _ROCKET, rx, ry)
    for r, reach in ((3, 4), (4, 8), (5, 4)):        # flame
        n = 2 + noise_rng.randint(0, reach)
        m[ry + r, max(0, rx + 2 - n):rx + 2] = True
    blit_mask(surface, x0, y0, m)


def _atom_orbits():
    orbits = _st.get("atom")
    if orbits is None:
        u = np.linspace(0, 2 * math.pi, 96, endpoint=False)
        orbits = []
        for k in range(3):
            a = k * math.pi / 3
            ex, ey = 7.2 * np.cos(u), 2.4 * np.sin(u)
            orbits.append((15.5 + 1.9 * (ex * math.cos(a) - ey * math.sin(a)),
                           7.5 + ex * math.sin(a) + ey * math.cos(a)))
        _st["atom"] = orbits
    return orbits


def f50_atom(surface, rect, t, seed=0.0):
    """The atomic age: electrons whirling round a nucleus that throbs on the beat."""
    x0, y0, w, h = rect
    tt = t + seed
    m = disc(h, w, 15.5, 7.5, 1.4 + 1.3 * math.exp(-_beat(t) * 5))
    for k, (xs, ys) in enumerate(_atom_orbits()):
        plot(m, xs, ys)
        i = int((tt * (0.5 + 0.13 * k) + k * 0.31) % 1.0 * len(xs))
        ex, ey = int(round(xs[i])), int(round(ys[i]))
        m[max(0, ey - 1):ey + 2, max(0, ex - 1):ex + 2] = True
    blit_mask(surface, x0, y0, m)


@for_energy("fast")
def f50_hula(surface, rect, t, seed=0.0):
    """A hula hoop going round and round a pair of swinging hips."""
    x0, y0, w, h = rect
    a = math.pi * t / config.TEMPO_PERIOD_DEFAULT_SECONDS    # once round every two beats
    ca, sa = math.cos(a), math.sin(a)
    hipx = 15.5 + 1.6 * ca
    topx = 15.5 - 0.6 * ca
    m = disc(h, w, topx, 2, 1.7)
    draw_line(m, topx, 4, hipx, 9)
    draw_line(m, topx, 5, 10, 3 + sa)
    draw_line(m, topx, 5, 21, 3 - sa)
    draw_line(m, hipx, 9, 13, 15)
    draw_line(m, hipx, 9, 18, 15)
    u = np.linspace(0, 2 * math.pi, 48, endpoint=False)
    hx = hipx + 2 * ca + 8.5 * np.cos(u)
    hy = 9 + 0.8 * sa + 2.2 * np.sin(u) + 1.3 * np.cos(u - a)
    show = (np.sin(u) > 0) | (np.abs(hx - hipx) > 1.5)        # the far side passes behind her
    plot(m, hx[show], hy[show])
    blit_mask(surface, x0, y0, m)


def _tv_picture():
    pic = _st.get("tv_pic")
    if pic is None:
        yy, xx = _grid(7, 15)
        d = np.hypot(xx - 7, yy - 3)
        pic = ((d <= 3.4) & (d > 2.4)) | (yy == 3) | (xx == 7)
        _st["tv_pic"] = pic
    return pic


@for_energy("slow")
def f50_tv(surface, rect, t, seed=0.0):
    """A rabbit-eared television whose picture keeps slipping its vertical hold."""
    x0, y0, w, h = rect
    tt = t + seed
    c = tt % 7.0
    m = np.zeros((h, w), bool)
    draw_box(m, 3, 2, 26, 13)
    m[15, 5] = m[15, 26] = True
    draw_line(m, 15, 2, 10, 0)
    draw_line(m, 16, 2, 21, 0)
    draw_box(m, 5, 4, 17, 9)
    m[5:7, 24:26] = m[9:11, 24:26] = True
    m[12, 23:27] = True
    pic = _tv_picture()
    if c > 4.0:                                      # rolling, then it snaps back
        pic = np.roll(pic, int((c - 4.0) * 9) % 7, axis=0)
        pic = np.roll(pic, int(round(1.5 * math.sin(c * 9))), axis=1)
    m[5:12, 6:21] = pic ^ (noise_rng.rand(7, 15) < (0.12 if c > 4.0 else 0.03))
    blit_mask(surface, x0, y0, m)


@for_energy("fast")
def f50_guitar(surface, rect, t, seed=0.0):
    """A hollow-body guitar, strings ringing on every beat."""
    x0, y0, w, h = rect
    tt = t + seed
    body = disc(h, w, 6, 8, 5.6) | disc(h, w, 12.5, 8, 4.4)
    m = _outline(body)
    m |= _outline(disc(h, w, 11, 8, 2.0))
    m[5:12, 4] = True                                # bridge
    m[6, 17:28] = m[10, 17:28] = True                # neck
    m[6:11, 19:28:3] = True
    draw_box(m, 28, 5, 4, 7)
    m[4, 29] = m[12, 29] = m[4, 31] = m[12, 31] = True
    amp = 1.7 * math.exp(-_beat(t) * 3.5)
    xs = np.arange(5, 29)
    bow = np.sin(math.pi * (xs - 5) / 23.0)
    for i, base in enumerate((7, 9)):
        plot(m, xs, base + amp * bow * math.sin(tt * (31 + 9 * i) + i))
    blit_mask(surface, x0, y0, m)


def f50_diner(surface, rect, t, seed=0.0):
    """A DINER sign lighting up letter by letter, bulbs chasing along it."""
    x0, y0, w, h = rect
    tt = t + seed
    k = _beat_index(t) % 8
    xs = np.arange(w)
    step = int(tt * 9)
    m = np.zeros((h, w), bool)
    m[0] = m[1] = (xs + step) % 4 < 2
    m[14] = m[15] = (xs - step) % 4 < 2
    word = "DINER"
    shown = word[:k + 1] if k < 5 else (word if k != 6 else "")
    _stamp(m, text_mask(shown), 2, 4)
    blit_mask(surface, x0, y0, m)


_STARS = ((2, 1), (9, 3), (24, 1), (29, 5), (5, 7), (27, 9), (13, 0))


@for_energy("slow")
def f50_sputnik(surface, rect, t, seed=0.0):
    """A little satellite beeping its way over the turning Earth."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    m = np.zeros((h, w), bool)
    for i, (sx, sy) in enumerate(_STARS):
        if math.sin(tt * (1.1 + 0.3 * i) + i * 2.2) > -0.4:
            m[sy, sx] = True
    d = np.hypot(xx - 15.5, yy - 27)
    land = np.sin(xx * 0.55 + tt * 0.9) * np.cos(yy * 1.3 + xx * 0.2 + tt * 0.3) > 0.35
    m |= ((d <= 18) & (d > 17)) | ((d <= 17) & land)
    sx, sy = 14 + 3 * math.sin(tt * 0.5), 4 + 1.3 * math.sin(tt * 0.8)
    m |= disc(h, w, sx, sy, 2.4)
    for ex, ey in ((-7, -4), (-9, -1.5), (-9, 1.5), (-7, 4)):
        draw_line(m, sx - 1, sy, sx + ex, sy + ey)
    ph = _beat(t)
    if ph < 0.75:                                    # beep... beep...
        d2 = np.hypot(xx - sx, yy - sy)
        m |= (np.abs(d2 - (4 + ph * 10)) < 0.6) & (np.abs(yy - sy) < (xx - sx) * 0.7)
    blit_mask(surface, x0, y0, m)


@for_energy("fast")
def f50_bubblegum(surface, rect, t, seed=0.0):
    """A kid blowing a bubble bigger and bigger... until it pops."""
    x0, y0, w, h = rect
    kb = (t / config.TEMPO_PERIOD_DEFAULT_SECONDS) % 8.0
    m = _outline(disc(h, w, 15.5, 7.5, 7.4))
    wide = kb > 4
    for ex in (12, 18):
        m[4 if wide else 5:7, ex:ex + 2] = True
    m[0, 13:19] = True                               # fringe
    m[1, 11:21:2] = True
    if kb < 6:
        r = 0.9 + kb / 6 * 6.3 + 0.3 * math.sin(t * 8)
        bubble = disc(h, w, 15.5, 10.5 - r * 0.42, r)
        m &= ~bubble
        m |= _outline(bubble)
        if r > 3:
            m[int(10.5 - r * 0.42 - r * 0.45), int(15.5 - r * 0.45)] = True
    elif kb < 6.7:
        f = (kb - 6) / 0.7
        for j in range(8):
            a = j * math.pi / 4 + 0.3
            ca, sa = math.cos(a), math.sin(a)
            draw_line(m, 15.5 + ca * (3 + 8 * f) * 1.6, 8 + sa * (3 + 6 * f),
                      15.5 + ca * (5 + 8 * f) * 1.6, 8 + sa * (5 + 6 * f))
    else:
        m[9:12, 13:18] = True                        # gum all over
        m[8, 12] = m[12, 16] = m[10, 19] = m[11, 11] = True
    blit_mask(surface, x0, y0, m)


@for_energy("slow")
def f50_doowop(surface, rect, t, seed=0.0):
    """Four singers swaying shoulder to shoulder, the harmonies drifting up."""
    x0, y0, w, h = rect
    lean = 1.8 * _swing(t, 4.0)
    m = np.zeros((h, w), bool)
    for i in range(4):
        cx = 4 + i * 8
        m |= disc(h, w, cx + lean, 6.5, 1.9)
        for r in range(9, 16):
            c = cx + lean * (15 - r) / 7.0
            half = 3 if r < 11 else 2
            m[r, max(0, int(round(c)) - half):int(round(c)) + half + 1] = True
    k, ph = _beat_index(t), _beat(t)
    if ph < 0.9:
        _stamp(m, _NOTE, (k * 11) % 27 + 1, int(4 - ph * 9))
    blit_mask(surface, x0, y0, m)


ANIMATIONS = [
    f50_jukebox, f50_record, f50_jive, f50_tailfin, f50_milkshake, f50_rocket,
    f50_atom, f50_hula, f50_tv, f50_guitar, f50_diner, f50_sputnik,
    f50_bubblegum, f50_doowop,
]
