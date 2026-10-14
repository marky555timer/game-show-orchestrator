"""THE 90s -- idle theme for 1990s songs (2026-10-13).

Single-panel (32x16) animations for panels 3-6, dealt like any other idle
theme (graphics/animations.py IDLE_THEMES["90s"]). Songs released in the
1990s pick this theme automatically
(drivers/light_prefs_engine.py::resolve_idle_theme); the energy tag narrows
the deal the same way as graphics/eighties_theme.py.
"""
import math

import numpy as np

import config
from graphics.love_theme import _outline, _TINY
from graphics.technical_dreams import (
    blit_mask, dither, _grid, _beat, _stamp, _st, _x2, text_mask,
    sprite, draw_line, draw_box, disc, tri, for_energy,
)

_DIRS = ((1, 0), (0, 1), (-1, 0), (0, -1))


def _beat_index(t):
    return int(t / config.TEMPO_PERIOD_DEFAULT_SECONDS)


def n90_cd(surface, rect, t, seed=0.0):
    """A compact disc spinning, light sweeping across it."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    cx, cy = w / 2 - 0.5, h / 2 - 0.5
    d = np.hypot(xx - cx, yy - cy)
    th = np.arctan2(yy - cy, xx - cx)
    m = ((d <= 7.6) & (d > 6.7)) | ((d <= 2.6) & (d > 1.5))
    m |= (d <= 5.6) & (d > 3.6) & (np.cos(2 * (th - (t + seed) * 2.2)) > 0.6)
    blit_mask(surface, x0, y0, m)


def n90_bounce(surface, rect, t, seed=0.0):
    """A "90s" badge drifting about like a screensaver, bouncing off the edges."""
    x0, y0, w, h = rect
    tt = t + seed
    label = text_mask("90s")
    lh, lw = label.shape
    x, y = int(tri(tt * 0.11) * (w - lw - 4)), int(tri(tt * 0.17) * (h - lh - 4))
    m = np.zeros((h, w), bool)
    draw_box(m, x, y, lw + 4, lh + 4)
    _stamp(m, label, x + 2, y + 2)
    blit_mask(surface, x0, y0, m)


_PIPES_STEP_SECONDS = 0.05
_PIPES_FADE_SECONDS = 0.8


def _pipes_step(s, w, h):
    rng = s["rng"]
    if s["left"] <= 0:                      # start another pipe
        s["pos"] = (1 + 2 * rng.randint(w // 2), 1 + 2 * rng.randint(h // 2))
        s["dir"] = rng.randint(4)
        s["left"] = rng.randint(18, 45)
    if rng.rand() < 0.3:
        s["dir"] = (s["dir"] + rng.choice((1, 3))) % 4
    x, y = s["pos"]
    for _ in range(4):                      # turn away from the edge
        dx, dy = _DIRS[s["dir"]]
        if 0 < x + 2 * dx < w and 0 < y + 2 * dy < h:
            break
        s["dir"] = (s["dir"] + 1) % 4
    s["m"][y + dy, x + dx] = s["m"][y + 2 * dy, x + 2 * dx] = s["m"][y, x] = True
    s["pos"] = (x + 2 * dx, y + 2 * dy)
    s["left"] -= 1
    s["n"] += 1


@for_energy("slow")
def n90_pipes(surface, rect, t, seed=0.0):
    """Screensaver pipes growing across the panel, then starting over."""
    x0, y0, w, h = rect
    s = _st.get(("pipes", x0, y0))
    if s is None:
        s = _st[("pipes", x0, y0)] = {"t": t, "acc": 0.0, "n": 0, "left": 0, "fade": None,
                                      "m": np.zeros((h, w), bool),
                                      "rng": np.random.RandomState(int(seed * 100) % 100000)}
    dt = max(0.0, min(0.2, t - s["t"]))
    s["t"] = t
    if s["fade"] is not None:
        s["fade"] += dt
        if s["fade"] >= _PIPES_FADE_SECONDS:
            s["m"][:] = False
            s["n"], s["left"], s["fade"] = 0, 0, None
        else:
            level = np.full((h, w), 1 - s["fade"] / _PIPES_FADE_SECONDS, np.float32)
            blit_mask(surface, x0, y0, s["m"] & dither(level, x0, y0))
            return
    s["acc"] += dt
    while s["acc"] >= _PIPES_STEP_SECONDS:
        s["acc"] -= _PIPES_STEP_SECONDS
        _pipes_step(s, w, h)
    if s["n"] > 150:
        s["fade"] = 0.0
    blit_mask(surface, x0, y0, s["m"])


_SNAKE_STEP_SECONDS = 0.08


def _snake_reset(s, gw, gh):
    s["body"] = [(gw // 2, gh // 2)]
    s["grow"] = 3
    s["food"] = (s["rng"].randint(gw), s["rng"].randint(gh))


def _snake_step(s, gw, gh):
    body, food, rng = s["body"], s["food"], s["rng"]
    hx, hy = body[0]
    moves = [(abs(hx + dx - food[0]) + abs(hy + dy - food[1]), rng.rand(), (hx + dx, hy + dy))
             for dx, dy in _DIRS
             if 0 <= hx + dx < gw and 0 <= hy + dy < gh and (hx + dx, hy + dy) not in body[:-1]]
    if not moves or len(body) > 24:
        _snake_reset(s, gw, gh)
        return
    head = min(moves)[2]
    body.insert(0, head)
    if head == food:
        s["grow"] += 2
        free = [(x, y) for x in range(gw) for y in range(gh) if (x, y) not in body]
        s["food"] = free[rng.randint(len(free))]
    if s["grow"] > 0:
        s["grow"] -= 1
    else:
        body.pop()


@for_energy("fast")
def n90_snake(surface, rect, t, seed=0.0):
    """The phone snake game: chasing food, growing, starting over."""
    x0, y0, w, h = rect
    gw, gh = w // 2, h // 2
    s = _st.get(("snake", x0, y0))
    if s is None:
        s = _st[("snake", x0, y0)] = {"t": t, "acc": 0.0, "rng": np.random.RandomState(int(seed * 100) % 100000)}
        _snake_reset(s, gw, gh)
    s["acc"] += max(0.0, min(0.2, t - s["t"]))
    s["t"] = t
    while s["acc"] >= _SNAKE_STEP_SECONDS:
        s["acc"] -= _SNAKE_STEP_SECONDS
        _snake_step(s, gw, gh)
    cells = np.zeros((gh, gw), bool)
    for x, y in s["body"]:
        cells[y, x] = True
    m = _x2(cells)
    fx, fy = s["food"]
    if int(t * 6) % 3:
        m[fy * 2, fx * 2 + 1] = m[fy * 2 + 1, fx * 2] = True
    else:
        m[fy * 2, fx * 2] = m[fy * 2 + 1, fx * 2 + 1] = True
    blit_mask(surface, x0, y0, m)


_PET_A = sprite(".####.", "#.##.#", "######", ".#..#.")
_PET_B = sprite(".####.", "#.##.#", "######", "#....#")


@for_energy("slow")
def n90_pet(surface, rect, t, seed=0.0):
    """A virtual pet hopping about inside its egg."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    m = _outline(((xx - 15.5) / 9.2) ** 2 + ((yy - 7.5) / 8.2) ** 2 <= 1)
    draw_box(m, 10, 2, 12, 9)
    hop = _beat(t) < 0.5
    _stamp(m, _PET_A if hop else _PET_B, 11 + int(2 + 2 * math.sin(tt * 1.2)), 5 if hop else 6)
    m[13, 12] = m[13, 19] = True
    m[13, 15:17] = True
    _stamp(m, _TINY, 1 if math.sin(tt * 1.5) > 0 else 26, 6)
    blit_mask(surface, x0, y0, m)


@for_energy("fast")
def n90_smiley(surface, rect, t, seed=0.0):
    """A rave smiley bouncing to the beat."""
    x0, y0, w, h = rect
    ph = _beat(t)
    cx = int(round(w / 2 + 8 * math.sin((t + seed) * 0.9)))
    cy = int(round(9 - 2.4 * math.sin(math.pi * ph)))
    m = disc(h, w, cx, cy, 6.3)
    m[cy - 3:cy - 1, cx - 2] = m[cy - 3:cy - 1, cx + 2] = False
    for dx in range(-4, 5):
        m[cy + int(round(4.2 - 0.2 * dx * dx)), cx + dx] = False
    if ph < 0.2:                            # a kick of sparks on the beat
        for sx, sy in ((-9, -4), (9, -4), (-10, 3), (10, 3)):
            if 0 <= cx + sx < w and 0 <= cy + sy < h:
                m[cy + sy, cx + sx] = True
    blit_mask(surface, x0, y0, m)


@for_energy("slow")
def n90_plaid(surface, rect, t, seed=0.0):
    """Flannel plaid sliding slowly by."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    ox, oy = xx + int((t + seed) * 2.0), yy + int((t + seed) * 1.0)
    v = 0.1 + 0.3 * ((ox // 4) % 2) + 0.3 * ((oy // 4) % 2)
    blit_mask(surface, x0, y0, dither(v, x0, y0) | (ox % 8 == 1) | (oy % 8 == 6))


_ARROW = sprite("#....", "##...", "###..", "####.", "#####", "..#..", "...#.")


def _hourglass(p, tick):
    g = np.zeros((16, 16), bool)
    g[0, 1:15] = g[15, 1:15] = True
    for y in range(1, 15):
        xl = y if y < 8 else 15 - y
        g[y, xl] = g[y, 15 - xl] = True
        if y <= 6 and y >= 1 + p * 6:             # sand left in the top bulb
            g[y, xl + 1:15 - xl] = True
        if y >= 9 and y >= 14.99 - p * 6:         # sand piled in the bottom bulb
            g[y, xl + 1:15 - xl] = True
    if 0 < p < 1:
        for y in range(9, 15):                    # the falling stream
            if g[y, 7]:
                break
            g[y, 7] = (y + tick) % 2 == 0
    return g


@for_energy("slow")
def n90_hourglass(surface, rect, t, seed=0.0):
    """The busy-cursor hourglass: sand runs through, it tips over, and starts again."""
    x0, y0, w, h = rect
    tt = t + seed
    c = tt % 7.0
    if c <= 6.0:
        g = _hourglass(c / 6.0, int(tt * 12))
    else:
        q = c - 6.0
        g = _hourglass(1.0, 0) if q < 0.25 else (_hourglass(1.0, 0).T if q < 0.75 else _hourglass(0.0, 0))
    m = np.zeros((h, w), bool)
    _stamp(m, g, 8, 0)
    _stamp(m, _ARROW, 1, 4)
    for i in range(int(min(c, 6.0) / 2.0) + 1)[:3]:
        m[12, 26 + i * 2] = True
    blit_mask(surface, x0, y0, m)


_MINE = sprite("#.#", ".#.", "#.#")
_MINE_COVER = np.ones((3, 3), bool)
_MINE_CLUES = (sprite("...", ".#.", "..."), sprite("#..", "...", "..#"))
_MINE_STEP_SECONDS = 0.2


@for_energy("fast")
def n90_mines(surface, rect, t, seed=0.0):
    """The minefield game: squares uncover one by one until one goes bang."""
    x0, y0, w, h = rect
    tt = t + seed
    cyc = 7.6
    ci, c = int(tt / cyc), tt % cyc
    s = _st.get(("mines", x0, y0))
    if s is None or s[0] != ci:             # a new board each round
        rng = np.random.RandomState((ci * 31 + int(seed * 100)) % 100000)
        s = _st[("mines", x0, y0)] = (ci, rng.permutation(32), rng.rand(32), rng.randint(21, 29))
    _, rank, kinds, safe = s
    shown = min(int(c / _MINE_STEP_SECONDS), safe)
    boom = c >= safe * _MINE_STEP_SECONDS
    m = np.zeros((h, w), bool)
    for i in range(32):
        x, y = (i % 8) * 4, (i // 8) * 4
        if rank[i] < shown:
            if kinds[i] < 0.45:
                _stamp(m, _MINE_CLUES[int(kinds[i] < 0.15)], x, y)
        elif boom and (rank[i] == safe or kinds[i] > 0.6):
            if rank[i] != safe or int(tt * 6) % 2:
                _stamp(m, _MINE, x, y)
        else:
            _stamp(m, _MINE_COVER, x, y)
    if boom and c - safe * _MINE_STEP_SECONDS < 0.4 and int(tt * 14) % 2:
        m = ~m
    blit_mask(surface, x0, y0, m)


@for_energy("slow")
def n90_lava(surface, rect, t, seed=0.0):
    """A pair of lava lamps, blobs rising and sinking."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    m = np.zeros((h, w), bool)
    for k, cx in enumerate((8, 23)):
        ax = np.abs(xx - cx)
        inside = (yy >= 1) & (yy <= 12) & (ax <= 2.5 + (yy - 1) * 0.33)
        f = np.zeros((h, w), np.float32)
        for i in range(3):
            bx = cx + 1.6 * math.sin(tt * 0.3 + i * 2.1 + k)
            by = 6.5 + 5.5 * math.sin(tt * (0.45 + 0.13 * i) + i * 2.0 + k * 1.3)
            f += (1.0 + 0.3 * i) ** 2 / ((xx - bx) ** 2 + (yy - by) ** 2 + 0.3)
        m |= _outline(inside) | (inside & (f > 1.0))
        m |= (yy == 0) & (ax <= 1.5)                                    # cap
        m |= (yy >= 13) & (ax <= 2.5 + (yy - 13))                       # base
    blit_mask(surface, x0, y0, m)


_CREW_ARMS = (((-2, 7), (2, 7)), ((-2, 1), (2, 1)), ((-2, 7), (2, 1)), ((-2, 1), (2, 7)))


@for_energy("fast")
def n90_dance_crew(surface, rect, t, seed=0.0):
    """Five dancers in a row doing the same moves on the beat."""
    x0, y0, w, h = rect
    pose = _beat_index(t) % 4
    top = 1 if _beat(t) < 0.25 else 2
    m = _st.get(("crew", w, h, pose, top))
    if m is not None:
        blit_mask(surface, x0, y0, m)
        return
    m = _st[("crew", w, h, pose, top)] = np.zeros((h, w), bool)
    for i in range(5):
        cx = 3 + i * 6 + (0, 1, 0, -1)[pose]
        draw_box(m, cx - 1, top, 3, 3)
        m[top + 3:top + 9, cx] = True
        for ax, ay in _CREW_ARMS[pose]:
            draw_line(m, cx, top + 4, cx + ax, top + ay)
        draw_line(m, cx, top + 9, cx - 2, top + 12)
        draw_line(m, cx, top + 9, cx + 2, top + 12)
    blit_mask(surface, x0, y0, m)


def n90_floppy(surface, rect, t, seed=0.0):
    """A floppy disk saving: shutter sliding, a progress bar filling."""
    x0, y0, w, h = rect
    tt = t + seed
    m = np.zeros((h, w), bool)
    draw_box(m, 1, 1, 14, 14)
    m[1, 14] = False                        # the clipped corner
    sx = 4 + int(1.5 + 1.5 * math.sin(tt * 1.5))
    draw_box(m, sx, 1, 6, 5)
    m[2:5, sx + 4] = True
    draw_box(m, 3, 8, 10, 7)
    m[10, 5:11] = m[12, 5:11] = True
    drive = text_mask("A:")
    _stamp(m, drive, 18, 1)
    if tt % 1.0 < 0.5:
        m[7, 19 + drive.shape[1]:23 + drive.shape[1]] = True
    draw_box(m, 17, 10, 14, 5)
    for i in range(int(((tt % 5.0) / 5.0) * 4.99)):
        m[11:14, 18 + i * 3:20 + i * 3] = True
    blit_mask(surface, x0, y0, m)


@for_energy("fast")
def n90_windows(surface, rect, t, seed=0.0):
    """A dragged window leaving a trail of copies, like a frozen desktop."""
    x0, y0, w, h = rect
    m = np.zeros((h, w), bool)
    for j in range(3, -1, -1):
        u = t + seed - j * 0.5
        x, y = int(tri(u * 0.21) * (w - 12)), int(tri(u * 0.33 + 0.1) * (h - 8))
        m[y:y + 8, x:x + 12] = False
        draw_box(m, x, y, 12, 8)
        m[y + 1, x + 1:x + 11] = True
        m[y + 1, x + 9] = False
    blit_mask(surface, x0, y0, m)


@for_energy("fast")
def n90_lasers(surface, rect, t, seed=0.0):
    """Rave lasers fanning out from two heads, snapping open on the beat."""
    x0, y0, w, h = rect
    tt = t + seed
    spread = 0.16 + 0.3 * math.exp(-_beat(t) * 4)
    m = np.zeros((h, w), bool)
    for ox, sgn in ((4, 1), (27, -1)):
        aim = -math.pi / 2 + sgn * (0.45 + 0.4 * math.sin(tt * 1.1))
        for i in (-1, 0, 1):
            a = aim + i * spread
            draw_line(m, ox, h - 1, ox + 40 * math.cos(a), h - 1 + 40 * math.sin(a))
        m[h - 2:, ox - 1:ox + 2] = True
    blit_mask(surface, x0, y0, m)


ANIMATIONS = [
    n90_cd, n90_bounce, n90_pipes, n90_snake, n90_pet, n90_smiley, n90_plaid,
    n90_hourglass, n90_mines, n90_lava, n90_dance_crew, n90_floppy,
    n90_windows, n90_lasers,
]
