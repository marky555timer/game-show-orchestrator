"""THE 80s -- idle theme for 1980s songs (2026-10-13).

Single-panel (32x16) animations for panels 3-6, dealt like any other idle
theme (graphics/animations.py IDLE_THEMES["80s"]). Songs released in the
1980s pick this theme automatically
(drivers/light_prefs_engine.py::resolve_idle_theme); the song's energy tag
then narrows the deal -- @for_energy("slow") ones only on slow songs,
@for_energy("fast") only on fast ones, unmarked on either.
"""
import math

import numpy as np

import config
from graphics.love_theme import _outline
from graphics.technical_dreams import (
    blit_mask, _grid, _beat, _stamp, _st, _levels, _x2, text_mask,
    sprite, draw_line, draw_box, disc, tri, noise_rng, for_energy,
)


def _beat_index(t):
    return int(t / config.TEMPO_PERIOD_DEFAULT_SECONDS)


def e80_cassette(surface, rect, t, seed=0.0):
    """A cassette tape with both reels turning."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    m = np.zeros((h, w), bool)
    draw_box(m, 1, 0, 30, 16)
    draw_box(m, 5, 3, 22, 9)
    a = (t + seed) * 2.5
    for cx in (10, 21):
        d = np.hypot(xx - cx, yy - 7)
        th = np.arctan2(yy - 7, xx - cx)
        m |= (d <= 3.3) & (d > 2.4)
        m |= (d <= 2.4) & (d > 0.9) & (np.cos(3 * (th - a)) > 0.45)
    m[7, 14:18] = True                      # tape between the reels
    draw_line(m, 8, 15, 10, 13)
    draw_line(m, 10, 13, 21, 13)
    draw_line(m, 21, 13, 23, 15)
    m[14, 13] = m[14, 18] = True
    blit_mask(surface, x0, y0, m)


_CUBE_PATS = [
    sprite("####", "####", "####", "####"),
    sprite("####", "#..#", "#..#", "####"),
    sprite("#.#.", ".#.#", "#.#.", ".#.#"),
    sprite("####", "....", "####", "...."),
    sprite("#.#.", "#.#.", "#.#.", "#.#."),
    sprite("....", ".##.", ".##.", "...."),
]


@for_energy("fast")
def e80_cube(surface, rect, t, seed=0.0):
    """Two puzzle-cube faces; a row or column twists on every beat."""
    x0, y0, w, h = rect
    k = _beat_index(t)
    s = _st.get(("cube", x0, y0))
    if s is None:
        rng = np.random.RandomState(int(seed * 100) % 100000)
        s = _st[("cube", x0, y0)] = {"k": k, "g": [rng.randint(0, 6, (3, 3)) for _ in range(2)]}
    if k != s["k"]:
        s["k"] = k
        g, i = s["g"][k % 2], (k // 2) % 3
        if (k // 6) % 2:
            g[i] = np.roll(g[i], 1)
        else:
            g[:, i] = np.roll(g[:, i], 1)
    m = np.zeros((h, w), bool)
    for f, left in enumerate((1, 17)):
        for r in range(3):
            for c in range(3):
                _stamp(m, _CUBE_PATS[s["g"][f][r, c]], left + c * 5, 1 + r * 5)
    blit_mask(surface, x0, y0, m)


_GHOST = sprite(".#####.", "#######", "#.##.##", "#######", "#######", "#######", "#.#.#.#")
_GHOST_B = sprite(".#####.", "#######", "#.##.##", "#######", "#######", "#######", ".#.#.#.")


@for_energy("fast")
def e80_chomper(surface, rect, t, seed=0.0):
    """A chomper eating its way along a row of dots, a ghost on its tail."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    px = -6 + ((tt % 6.0) / 6.0) * (w + 26)
    m = np.zeros((h, w), bool)
    m[1, :] = m[14, :] = True
    for x in range(3, w, 4):
        if x > px + 2 or x < px - 22:        # eaten dots grow back once the ghost has gone by
            m[8, x] = True
    mouth = abs(math.sin(tt * 9))
    d = np.hypot(xx - px, yy - 8)
    m |= (d <= 3.5) & (np.abs(np.arctan2(yy - 8, xx - px)) > mouth * 0.75)
    _stamp(m, _GHOST if int(tt * 6) % 2 else _GHOST_B, int(px) - 16, 5)
    blit_mask(surface, x0, y0, m)


def e80_sunset(surface, rect, t, seed=0.0):
    """A striped sun going down over a neon grid that rolls toward you."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    hz = 9
    still = _st.get(("sunset", w, h))
    if still is None:
        still = disc(h, w, w / 2 - 0.5, hz, 7.2) & (yy < hz) & (yy != 5) & (yy != 7)
        still[hz, :] = True
        for k in range(-3, 4):
            draw_line(still, w / 2 - 0.5 + k * 2.5, hz + 1, w / 2 - 0.5 + k * 5, h - 1)
        _st[("sunset", w, h)] = still
    m = still.copy()
    for i in range(2):
        f = (i + ((t + seed) * 0.9) % 1.0) / 2
        m[hz + 2 + int(4.99 * f * f), :] = True
    blit_mask(surface, x0, y0, m)


@for_energy("fast")
def e80_boombox(surface, rect, t, seed=0.0):
    """A boombox, speaker cones thumping on the beat."""
    x0, y0, w, h = rect
    kick = math.exp(-_beat(t) * 6)
    m = np.zeros((h, w), bool)
    draw_box(m, 0, 4, 32, 12)
    m[1, 8:24] = True                       # handle
    m[1:4, 8] = m[1:4, 23] = True
    for cx in (7, 24):
        m |= disc(h, w, cx, 10, 4.3) & ~disc(h, w, cx, 10, 3.3)
        m |= disc(h, w, cx, 10, 1.0 + 2.0 * kick)
    draw_box(m, 13, 6, 6, 5)                # tape door
    m[8, 14] = m[8, 17] = True
    m[13, 13:19:2] = True                   # buttons
    blit_mask(surface, x0, y0, m)


_INV_TOP = ("...##...", "..####..", ".######.", "##.##.##", "########")
_INV_A = sprite(*_INV_TOP, "..#..#..", ".#.##.#.", "#.#..#.#")
_INV_B = sprite(*_INV_TOP, ".#.##.#.", "#......#", ".#....#.")


@for_energy("fast")
def e80_invaders(surface, rect, t, seed=0.0):
    """Three invaders marching on the beat over a cannon that keeps firing."""
    x0, y0, w, h = rect
    k = _beat_index(t)
    tt = t + seed
    step = k % 8
    dx = step if step < 4 else 8 - step
    m = np.zeros((h, w), bool)
    for i in range(3):
        _stamp(m, _INV_A if k % 2 else _INV_B, dx + i * 10, 0)
    cx = int(w / 2 + 10 * math.sin(tt * 0.9))
    m[15, cx - 2:cx + 3] = True
    m[14, cx - 1:cx + 2] = True
    sy = 12 - int(((tt * 1.8) % 1.0) * 5)
    m[sy:sy + 2, cx] = True
    blit_mask(surface, x0, y0, m)


_PLAY_TRI = sprite("#....", "##...", "###..", "####.", "###..", "##...", "#....")


@for_energy("slow")
def e80_vhs(surface, rect, t, seed=0.0):
    """VCR on-screen display: PLAY, a running counter, a tracking band rolling through."""
    x0, y0, w, h = rect
    tt = t + seed
    m = np.zeros((h, w), bool)
    _stamp(m, text_mask("PLAY"), 1, 0)
    if tt % 1.2 < 0.8:
        _stamp(m, _PLAY_TRI, 26, 0)
    secs = int(tt) % 6000
    count = text_mask("%d:%02d" % (secs // 60, secs % 60))
    _stamp(m, count, w - 1 - count.shape[1], 9)
    top = int((tt * 5) % (h + 12)) - 4
    for r in range(top, top + 3):
        if 0 <= r < h:
            m[r] = np.roll(m[r], 2 + r - top) | (noise_rng.rand(w) < 0.3)
    blit_mask(surface, x0, y0, m)


_SHAPES = [
    sprite("...#...", "..#.#..", ".#...#.", "#######"),
    sprite(".###.", "#...#", "#...#", "#...#", ".###."),
    sprite("#...#...#", ".#.#.#.#.", "..#...#.."),
    sprite("..#..", "..#..", "#####", "..#..", "..#.."),
    sprite("#.#.#", ".....", "#.#.#", ".....", "#.#.#"),
]


@for_energy("fast")
def e80_shapes(surface, rect, t, seed=0.0):
    """Triangles, squiggles, rings and dots drifting past like a loud 80s print."""
    x0, y0, w, h = rect
    tt = t + seed
    m = np.zeros((h, w), bool)
    for k, sp in enumerate(_SHAPES):
        sh, sw = sp.shape
        vx = (3.0 + 1.3 * k) * (1 if k % 2 else -1)
        vy = (1.5 + 0.7 * k) * (-1 if k % 3 else 1)
        _stamp(m, sp, int((k * 9.1 + tt * vx) % (w + sw)) - sw, int((k * 5.3 + tt * vy) % (h + sh)) - sh)
    blit_mask(surface, x0, y0, m)


_NOTE = sprite(".###", ".#..", ".#..", "##..", "##..")
_ARP = (0, 2, 4, 7, 4, 2, 5, 3)


@for_energy("slow")
def e80_keys(surface, rect, t, seed=0.0):
    """A synth keyboard playing a slow arpeggio, a note floating up from each key."""
    x0, y0, w, h = rect
    ph = _beat(t)
    m = np.zeros((h, w), bool)
    m[8:, :] = True
    m[9:, 3::4] = False                     # gaps between the white keys
    for i in (0, 1, 3, 4, 5):               # black keys
        m[9:13, i * 4 + 2:i * 4 + 5] = False
    x = _ARP[_beat_index(t) % len(_ARP)] * 4
    if ph < 0.6:
        m[13:, x:x + 3] = False             # the key being played
    if ph < 0.85:
        _stamp(m[:8], _NOTE, min(x, w - 4), int(2 - ph * 5))
    blit_mask(surface, x0, y0, m)


_GLINTS = ((3, 3), (27, 4), (5, 12), (26, 11), (1, 8), (30, 8))


@for_energy("slow")
def e80_mirror_ball(surface, rect, t, seed=0.0):
    """A mirror ball turning slowly and throwing off glints."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    tt = t + seed
    dx, dy = (xx - (w / 2 - 0.5)) / 6.6, (yy - 8.5) / 6.6
    inside = dx * dx + dy * dy <= 1
    u = np.arcsin(np.clip(dx / np.sqrt(np.maximum(1 - dy * dy, 1e-3)), -1, 1))
    v = np.arcsin(np.clip(dy, -1, 1))
    facets = (np.floor(u * 2.3 + tt * 0.8) + np.floor(v * 2.3 + 0.5)) % 2 == 0
    m = _outline(inside) | (inside & facets)
    m[0:2, 15:17] = True
    for k, (gx, gy) in enumerate(_GLINTS):
        tw = math.sin(tt * (1.3 + 0.37 * k) + k * 1.9)
        if tw > 0.5:
            m[gy, gx] = True
        if tw > 0.9:
            m[gy - 1:gy + 2, gx] = True
            m[gy, max(0, gx - 1):gx + 2] = True
    blit_mask(surface, x0, y0, m)


_BLOCKS = [
    sprite("####"), sprite("#", "#", "#", "#"), sprite("##", "##"),
    sprite("###", ".#."), sprite(".#.", "###"), sprite("#.", "##", "#."), sprite(".#", "##", ".#"),
    sprite("##.", ".##"), sprite(".##", "##."), sprite("#.", "#.", "##"), sprite("###", "#.."),
    sprite(".#", ".#", "##"), sprite("#..", "###"),
]
_BLOCK_STEP_SECONDS = 0.07


def _block_fits(g, shape, px, py):
    sh, sw = shape.shape
    return py + sh <= g.shape[0] and not (g[py:py + sh, px:px + sw] & shape).any()


def _blocks_step(s):
    g, rng = s["g"], s["rng"]
    if s["p"] is None:
        shape = _BLOCKS[rng.randint(len(_BLOCKS))]
        spots = []
        for px in range(g.shape[1] - shape.shape[1] + 1):
            if not _block_fits(g, shape, px, 0):
                continue
            py = 0
            while _block_fits(g, shape, px, py + 1):
                py += 1
            spots.append((py + shape.shape[0], rng.rand(), px))
        if not spots:
            g[:] = False
            return
        s["p"] = [shape, max(spots)[2], 0]   # the column where it lands lowest
        return
    shape, px, py = s["p"]
    if _block_fits(g, shape, px, py + 1):
        s["p"][2] += 1
        return
    _stamp(g, shape, px, py)
    s["p"] = None
    keep = g[~g.all(axis=1)]
    g[:] = False
    g[g.shape[0] - len(keep):] = keep
    if g[:2].any():
        g[:] = False


@for_energy("fast")
def e80_blocks(surface, rect, t, seed=0.0):
    """Falling blocks stacking up; full rows vanish."""
    x0, y0, w, h = rect
    s = _st.get(("blocks", x0, y0))
    if s is None:
        s = _st[("blocks", x0, y0)] = {"t": t, "acc": 0.0, "p": None, "g": np.zeros((h // 2, w // 2), bool),
                                       "rng": np.random.RandomState(int(seed * 100) % 100000)}
    s["acc"] += max(0.0, min(0.2, t - s["t"]))
    s["t"] = t
    while s["acc"] >= _BLOCK_STEP_SECONDS:
        s["acc"] -= _BLOCK_STEP_SECONDS
        _blocks_step(s)
    cells = s["g"].copy()
    if s["p"]:
        _stamp(cells, *s["p"])
    blit_mask(surface, x0, y0, _x2(cells))


def e80_pong(surface, rect, t, seed=0.0):
    """A bat-and-ball video game rally."""
    x0, y0, w, h = rect
    tt = t + seed
    bx, by = 2 + tri(tt * 0.28) * (w - 6), tri(tt * 0.37 + 0.2) * (h - 2)
    m = np.zeros((h, w), bool)
    m[int(by):int(by) + 2, int(bx):int(bx) + 2] = True
    for x, far in ((1, (bx - 8) / 16), (w - 2, (w - 10 - bx) / 16)):
        f = max(0.0, min(1.0, far))          # bat drifts back to the middle when the ball is away
        y = int(max(0, min(h - 4, by * (1 - f) + 6 * f - 1)))
        m[y:y + 4, x] = True
    m[0::4, w // 2 - 1] = m[1::4, w // 2 - 1] = True
    blit_mask(surface, x0, y0, m)


@for_energy("slow")
def e80_vcr_clock(surface, rect, t, seed=0.0):
    """The VCR nobody ever set: 12:00, blinking."""
    x0, y0, w, h = rect
    m = np.zeros((h, w), bool)
    draw_box(m, 0, 0, w, h)
    m[13, 3:6] = m[13, 26:29] = True
    if (t + seed) % 1.2 < 0.7:
        clock = text_mask("12:00")
        _stamp(m, clock, (w - clock.shape[1]) // 2, 3)
    blit_mask(surface, x0, y0, m)


def e80_sliders(surface, rect, t, seed=0.0):
    """Graphic-equaliser sliders riding the music."""
    x0, y0, w, h = rect
    m = np.zeros((h, w), bool)
    for i, lv in enumerate(_levels(t, 7, seed)):
        x = 4 + i * 4
        m[1:h - 1:2, x] = True
        y = int(round(13 - lv * 11))
        m[y:y + 2, x - 1:x + 2] = True
    blit_mask(surface, x0, y0, m)


ANIMATIONS = [
    e80_cassette, e80_cube, e80_chomper, e80_sunset, e80_boombox, e80_invaders,
    e80_vhs, e80_shapes, e80_keys, e80_mirror_ball, e80_blocks, e80_pong,
    e80_vcr_clock, e80_sliders,
]
