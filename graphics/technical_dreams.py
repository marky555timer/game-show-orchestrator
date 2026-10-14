"""TECHNICAL DREAMS -- demoscene-style idle theme for panels 3-6 (2026-10-11).

Two parts:
  * ANIMATIONS: single-panel (32x16) effects dealt like any other idle theme
    (graphics/animations.py IDLE_THEMES["Technical Dreams"]).
  * The DEMO: one 64x32 production across all four panels, in the spirit of
    an Assembly party demo -- loader, title, starfield, cube, plasma,
    metaballs, tunnel, rotozoomer, dot torus, landscape, fire, scroller,
    THE END. With this theme on, every other "dancing" turn of the idle
    cycle plays the next two scenes of it (picking up where the last turn
    left off); play_full_demo() runs the whole thing once, start to finish.

The panels are 1-bit (any lit colour is just "on"), so shading is done the
way the old 1-bit demos did it: ordered (Bayer) dithering. Everything is
numpy; a frame is a bool array, blitted as one small surface.
"""
import json
import math
import os
import time

import numpy as np
import pygame

import config
from state import state
from graphics.text_render import _glyph_for, text_width

ON = (255, 0, 0)

# ------------------------------------------------------------------
# Shared helpers
# ------------------------------------------------------------------
_B4 = (np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]) + 0.5) / 16.0


def _bayer(h, w, ox=0, oy=0):
    big = np.tile(_B4, (h // 4 + 2, w // 4 + 2))
    return big[oy % 4: oy % 4 + h, ox % 4: ox % 4 + w]


def dither(v, ox=0, oy=0):
    """0..1 brightness -> on/off pattern (ordered dither)."""
    return v > _bayer(v.shape[0], v.shape[1], ox, oy)


def blit_mask(surface, x0, y0, mask):
    h, w = mask.shape
    rgb = np.zeros((w, h, 3), np.uint8)
    rgb[..., 0] = mask.T * 255
    surface.blit(pygame.surfarray.make_surface(rgb), (x0, y0))


_grids = {}


def _grid(h, w):
    g = _grids.get((h, w))
    if g is None:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        g = _grids[(h, w)] = (yy, xx)
    return g


_text_cache = {}


def text_mask(text, tight=False):
    key = (text, tight)
    m = _text_cache.get(key)
    if m is None:
        parts = []
        for i, ch in enumerate(text):
            g = _glyph_for(ch)
            parts.append(np.array([[c == "1" for c in row] for row in g], bool))
            if not tight and i < len(text) - 1:
                parts.append(np.zeros((7, 1), bool))
        m = np.hstack(parts) if parts else np.zeros((7, 0), bool)
        _text_cache[key] = m
    return m


def _stamp(dst, src, x, y, on=True):
    h, w = src.shape
    H, W = dst.shape
    xa, ya, xb, yb = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if xb <= xa or yb <= ya:
        return
    sub = src[ya - y:yb - y, xa - x:xb - x]
    if on:
        dst[ya:yb, xa:xb] |= sub
    else:
        dst[ya:yb, xa:xb] &= ~sub


def _x2(m):
    return np.kron(m, np.ones((2, 2), bool))


def sprite(*rows):
    """Rows of "#" (lit) / anything else (dark) -> on/off mask."""
    return np.array([[c == "#" for c in r] for r in rows], bool)


def draw_line(m, xa, ya, xb, yb):
    n = int(max(abs(xb - xa), abs(yb - ya))) + 1
    xs = np.rint(np.linspace(xa, xb, n)).astype(int)
    ys = np.rint(np.linspace(ya, yb, n)).astype(int)
    ok = (xs >= 0) & (xs < m.shape[1]) & (ys >= 0) & (ys < m.shape[0])
    m[ys[ok], xs[ok]] = True


def draw_box(m, x, y, w, h, fill=False):
    H, W = m.shape
    xa, ya, xb, yb = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if xb <= xa or yb <= ya:
        return
    if fill:
        m[ya:yb, xa:xb] = True
        return
    if 0 <= y < H:
        m[y, xa:xb] = True
    if 0 <= y + h - 1 < H:
        m[y + h - 1, xa:xb] = True
    if 0 <= x < W:
        m[ya:yb, x] = True
    if 0 <= x + w - 1 < W:
        m[ya:yb, x + w - 1] = True


def disc(h, w, cx, cy, r):
    yy, xx = _grid(h, w)
    return np.hypot(xx - cx, yy - cy) <= r


def ellipse(h, w, cx, cy, rx, ry):
    yy, xx = _grid(h, w)
    return ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2 <= 1


def plot(m, xs, ys):
    """Light the points (xs, ys) -- arrays or lists -- that land on the panel."""
    xs = np.rint(np.asarray(xs, np.float32)).astype(int)
    ys = np.rint(np.asarray(ys, np.float32)).astype(int)
    ok = (xs >= 0) & (xs < m.shape[1]) & (ys >= 0) & (ys < m.shape[0])
    m[ys[ok], xs[ok]] = True


def tri(p):
    """Triangle wave 0..1..0 over one unit of p."""
    p %= 1.0
    return 2 * p if p < 0.5 else 2 - 2 * p


noise_rng = np.random.RandomState(7)   # throwaway noise; seeding a fresh generator every frame is slow on the Pi


def hash01(a, b=0.0):
    """Repeatable 0..1 pseudo-random number for a pair of numbers."""
    return (math.sin(a * 12.9898 + b * 78.233) * 43758.5453) % 1.0


def for_energy(*tags):
    """Marks an animation as suiting only these energy tags ("slow",
    "fast"); unmarked ones suit any song. graphics/animations.py deals
    from the matching part of a theme (state.idle_theme_energy)."""
    def mark(fn):
        fn.energies = tags
        return fn
    return mark


def _beat(t):
    """0..1 position in the current beat on the tempo-locked idle clock
    (graphics/matrix_canvas.py advances it one default period per beat)."""
    return (t / config.TEMPO_PERIOD_DEFAULT_SECONDS) % 1.0


def _rot(p, a, b, c):
    x, y, z = p
    ca, sa, cb, sb, cc, sc = math.cos(a), math.sin(a), math.cos(b), math.sin(b), math.cos(c), math.sin(c)
    y, z = y * ca - z * sa, y * sa + z * ca
    x, z = x * cb + z * sb, -x * sb + z * cb
    x, y = x * cc - y * sc, x * sc + y * cc
    return x, y, z


_CUBE_V = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
_CUBE_E = [(i, j) for i in range(8) for j in range(i + 1, 8)
           if sum(a != b for a, b in zip(_CUBE_V[i], _CUBE_V[j])) == 1]

_st = {}  # per-panel / per-scene state for the stateful effects


# ------------------------------------------------------------------
# Single-panel animations (32x16) -- signature matches graphics/animations.py
# ------------------------------------------------------------------
def td_plasma(surface, rect, t, seed=0.0):
    """Classic sine plasma, colour-cycled through the dither."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    t += seed
    v = (np.sin(xx / 4.0 + t) + np.sin(yy / 3.0 + t * 1.3) + np.sin((xx + yy) / 6.0 + t * 0.7)
         + np.sin(np.hypot(xx - w / 2 - 8 * math.sin(t * 0.5), yy - h / 2 - 4 * math.cos(t * 0.4)) / 2.5 - t * 2))
    blit_mask(surface, x0, y0, dither(0.5 + 0.5 * np.sin(v * 1.5 + t)))


def td_xor_rings(surface, rect, t, seed=0.0):
    """Two drifting sets of concentric rings XORed -- moire interference."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    t += seed
    d1 = np.hypot(xx - (w / 2 + w * 0.35 * math.sin(t * 0.9)), yy - (h / 2 + h * 0.4 * math.cos(t * 1.3)))
    d2 = np.hypot(xx - (w / 2 + w * 0.35 * math.sin(t * 1.1 + 2)), yy - (h / 2 + h * 0.4 * math.sin(t * 0.7 + 1)))
    blit_mask(surface, x0, y0, ((d1 // 3).astype(int) + (d2 // 3).astype(int)) % 2 == 0)


def td_metaballs(surface, rect, t, seed=0.0):
    """Three blobs that melt together, with a dithered glow."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    t += seed
    f = np.zeros((h, w), np.float32)
    for k in range(3):
        cx = w / 2 + (w / 2 - 4) * math.sin(t * (0.7 + 0.3 * k) + k * 2.1)
        cy = h / 2 + (h / 2 - 3) * math.cos(t * (0.9 + 0.2 * k) + k)
        f += 16.0 / ((xx - cx) ** 2 + (yy - cy) ** 2 + 1.0)
    blit_mask(surface, x0, y0, dither(np.clip((f - 0.45) / 0.6, 0, 1)))


def _stars(key, n, seed):
    s = _st.get(key)
    if s is None:
        rng = np.random.RandomState(int(seed * 1000) % 100000)
        s = _st[key] = (rng.uniform(-1, 1, n), rng.uniform(-1, 1, n), rng.uniform(0, 1, n))
    return s


def td_starfield(surface, rect, t, seed=0.0):
    """3D starfield flying out of the middle; close stars get bigger."""
    x0, y0, w, h = rect
    sx, sy, z0 = _stars(("stars", x0, y0), 40, seed)
    z = (z0 - t * 0.35) % 1.0 + 0.03
    px = (w / 2 + sx / z * w * 0.18).astype(int)
    py = (h / 2 + sy / z * h * 0.3).astype(int)
    m = np.zeros((h, w), bool)
    ok = (px >= 0) & (px < w) & (py >= 0) & (py < h)
    m[py[ok], px[ok]] = True
    near = ok & (z < 0.3) & (px + 1 < w)
    m[py[near], px[near] + 1] = True
    blit_mask(surface, x0, y0, m)


def td_cube(surface, rect, t, seed=0.0):
    """Spinning wireframe cube that kicks on the beat."""
    x0, y0, w, h = rect
    t += seed
    k = 13.0 * (1.0 + 0.15 * math.exp(-_beat(t) * 6))
    pts = []
    for v in _CUBE_V:
        x, y, z = _rot(v, t * 0.9, t * 0.6, t * 0.4)
        s = k / (z + 3.6)
        pts.append((x0 + w / 2 + x * s * 1.1, y0 + h / 2 + y * s * 0.95))
    for i, j in _CUBE_E:
        pygame.draw.line(surface, ON, pts[i], pts[j])


def td_tunnel(surface, rect, t, seed=0.0):
    """Endless checkered tunnel, darker in the distance."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    t += seed
    cx, cy = w / 2 + 5 * math.sin(t * 0.6), h / 2 + 2 * math.cos(t * 0.8)
    dx, dy = (xx - cx) / 2.0, yy - cy
    d = np.hypot(dx, dy) + 0.4
    u = 12.0 / d + t * 3
    v = np.arctan2(dy, dx) / math.pi * 6 + t * 0.6
    chk = (np.floor(u).astype(int) + np.floor(v).astype(int)) % 2 == 0
    blit_mask(surface, x0, y0, chk & dither(np.clip(d / 7.0, 0, 1), x0, y0))


def td_rotozoom(surface, rect, t, seed=0.0):
    """Rotating, zooming checkerboard."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    t += seed
    a, s = t * 0.7, 0.6 + 0.45 * math.sin(t * 0.5)
    ca, sa = math.cos(a) * s, math.sin(a) * s
    u = (xx - w / 2) * ca - (yy - h / 2) * sa + t * 3
    v = (xx - w / 2) * sa + (yy - h / 2) * ca + t * 1.5
    blit_mask(surface, x0, y0, (np.floor(u / 3).astype(int) + np.floor(v / 3).astype(int)) % 2 == 0)


def td_life(surface, rect, t, seed=0.0):
    """Conway's Game of Life; reseeds when it dies out or loops."""
    x0, y0, w, h = rect
    key = ("life", x0, y0)
    s = _st.get(key)
    if s is None or t < s["t"] - 1:
        s = _st[key] = {"g": np.random.rand(h, w) < 0.35, "t": t, "gen": 0, "hist": []}
    while t - s["t"] >= 0.14:
        g = s["g"]
        n = sum(np.roll(np.roll(g, dy, 0), dx, 1) for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                if (dy, dx) != (0, 0))
        g = (n == 3) | (g & (n == 2))
        s["gen"] += 1
        sig = g.tobytes()
        if s["gen"] > 240 or g.sum() < 6 or sig in s["hist"]:
            g, s["gen"], s["hist"] = np.random.rand(h, w) < 0.35, 0, []
        else:
            s["hist"] = (s["hist"] + [sig])[-6:]
        s["g"] = g
        s["t"] += 0.14
        if t - s["t"] > 2:
            s["t"] = t
    blit_mask(surface, x0, y0, s["g"])


def _fire_step(heat, rng, source=None, cool=0.07):
    """One step of the classic fire: random hot spots along the bottom (or
    hot `source` pixels, e.g. burning letters), heat rising and cooling."""
    h = heat.shape[0]
    if source is None:
        heat[-2:] = (rng.rand(2, heat.shape[1]) > 0.45) * rng.uniform(0.7, 1.3, (2, heat.shape[1]))
    else:
        heat[-2:] = 0
        heat[source & (rng.rand(*heat.shape) > 0.25)] = 1.2
    up = (np.roll(heat, -1, 1)[1:] + heat[1:] + np.roll(heat, 1, 1)[1:]
          + np.vstack([heat[2:], heat[-1:]])) / 4.0
    heat[:h - 1] = np.clip(up - cool * rng.uniform(0.5, 1.5, up.shape), 0, 1.3)
    return heat


def td_fire(surface, rect, t, seed=0.0):
    """Old-school fire effect."""
    x0, y0, w, h = rect
    key = ("fire", x0, y0)
    s = _st.get(key)
    if s is None or t < s["t"] - 1:
        s = _st[key] = {"heat": np.zeros((h + 2, w), np.float32), "t": t, "rng": np.random.RandomState()}
    steps = 0
    while t - s["t"] >= 0.05 and steps < 6:
        _fire_step(s["heat"], s["rng"])
        s["t"] += 0.05
        steps += 1
    if t - s["t"] > 1:
        s["t"] = t
    blit_mask(surface, x0, y0, dither(s["heat"][:h] * 1.25, x0, y0))


def _levels(t, n, seed):
    pulse = math.exp(-_beat(t) * 5)
    out = []
    for i in range(n):
        wob = (math.sin(t * (1.7 + 0.37 * i) + seed + i * 1.3) + math.sin(t * (3.1 + 0.21 * i) + i)) / 4 + 0.5
        out.append(min(1.0, 0.15 + 0.55 * wob + 0.4 * pulse * (1 - abs(i - n / 2) / n)))
    return out


def td_gauges(surface, rect, t, seed=0.0):
    """Segmented LED level meters with falling peak-hold dots."""
    x0, y0, w, h = rect
    n, bw = 7, 3
    left = x0 + (w - (n * (bw + 1) - 1)) // 2
    key = ("gauges", x0, y0)
    s = _st.setdefault(key, {"peak": [0.0] * n, "t": t})
    dt = max(0.0, min(0.2, t - s["t"]))
    s["t"] = t
    segs = h // 2
    for i, lv in enumerate(_levels(t, n, seed)):
        s["peak"][i] = max(lv, s["peak"][i] - dt * 0.5)
        lit = int(round(lv * segs))
        bx = left + i * (bw + 1)
        for k in range(lit):
            pygame.draw.rect(surface, ON, (bx, y0 + h - 1 - k * 2, bw, 1))
        pk = min(segs - 1, int(round(s["peak"][i] * segs)))
        pygame.draw.rect(surface, ON, (bx, y0 + h - 1 - pk * 2, bw, 1))


def td_needles(surface, rect, t, seed=0.0):
    """A pair of analog VU meters: tick arc, red-zone ticks, damped needle."""
    x0, y0, w, h = rect
    key = ("needles", x0, y0)
    s = _st.setdefault(key, {"pos": [0.0, 0.0], "t": t})
    dt = max(0.0, min(0.2, t - s["t"]))
    s["t"] = t
    lv = _levels(t, 2, seed)
    for k in range(2):
        s["pos"][k] += (lv[k] - s["pos"][k]) * min(1.0, dt * 9)
        px, py, r, ys = x0 + 8 + k * 16, y0 + h - 2, 7, 1.6
        for j in range(9):
            a = math.radians(150 - j * 15)
            tx, ty = px + r * math.cos(a), py - r * math.sin(a) * ys
            pygame.draw.rect(surface, ON, (int(round(tx)), int(round(ty)), 1, 2 if j >= 7 else 1))
        a = math.radians(150 - s["pos"][k] * 120)
        pygame.draw.line(surface, ON, (px, py), (px + (r - 1) * math.cos(a), py - (r - 1) * math.sin(a) * ys))
        pygame.draw.rect(surface, ON, (px - 1, py, 3, 2))


def td_scope(surface, rect, t, seed=0.0):
    """Oscilloscope Lissajous trace over a dotted graticule."""
    x0, y0, w, h = rect
    t += seed
    for gx in range(0, w, 8):
        for gy in range(0, h, 4):
            surface.set_at((x0 + gx, y0 + gy), ON)
    a, b = 3, 2 + int(t / 6) % 3
    pts = [(x0 + w / 2 + (w / 2 - 2) * math.sin(a * u + t),
            y0 + h / 2 + (h / 2 - 1) * math.sin(b * u + t * 0.3))
           for u in np.linspace(0, 2 * math.pi, 90)]
    pygame.draw.lines(surface, ON, False, pts)


def td_radar(surface, rect, t, seed=0.0):
    """Radar sweep with a fading trail and blips that light as it passes."""
    x0, y0, w, h = rect
    yy, xx = _grid(h, w)
    cx, cy = w / 2 - 0.5, h / 2 - 0.5
    ex, ey = (xx - cx) / (w / 2), (yy - cy) / (h / 2)
    r = np.hypot(ex, ey)
    sweep = (t * 2.0 + seed) % (2 * math.pi)
    behind = (sweep - np.arctan2(ey, ex)) % (2 * math.pi)
    trail = np.clip(1 - behind / 1.6, 0, 1) * (r <= 1.0)
    m = dither(trail * 0.9, x0, y0) | ((r > 0.93) & (r <= 1.05)) | (r < 0.1)
    rng = np.random.RandomState(int(seed * 100) % 1000)
    for _ in range(4):
        ba, br = rng.uniform(0, 2 * math.pi), rng.uniform(0.3, 0.85)
        if (sweep - ba) % (2 * math.pi) < 2.0:
            bx, by = int(cx + br * math.cos(ba) * w / 2), int(cy + br * math.sin(ba) * h / 2)
            m[max(0, by):by + 2, max(0, bx):bx + 2] = True
    blit_mask(surface, x0, y0, m)


def td_helix(surface, rect, t, seed=0.0):
    """Scrolling DNA double helix: front strand solid, back strand dotted,
    rungs every 4px."""
    x0, y0, w, h = rect
    cy, amp = y0 + (h - 1) / 2, h / 2 - 1.5
    prev = None
    for x in range(0, w + 1):
        ph = x * 0.22 + t * 2.5 + seed
        a = (x0 + x, cy + amp * math.sin(ph))
        b = (x0 + x, cy - amp * math.sin(ph))
        front_a = math.cos(ph) > 0
        if prev is not None:
            pa, pb = prev
            if front_a or x % 2:
                pygame.draw.line(surface, ON, pa, a)
            if not front_a or x % 2:
                pygame.draw.line(surface, ON, pb, b)
        if x % 4 == 0:
            for yy in range(int(min(a[1], b[1])) + 1, int(max(a[1], b[1])), 2):
                surface.set_at((x0 + x, yy), ON)
        prev = (a, b)


def td_raster(surface, rect, t, seed=0.0):
    """Copper bars bouncing up and down, shaded with the dither."""
    x0, y0, w, h = rect
    yy, _ = _grid(h, w)
    t += seed
    v = np.zeros((h, w), np.float32)
    for k in range(3):
        c = (h - 1) / 2 + (h / 2 - 2) * math.sin(t * 1.7 + k * 2.1)
        v = np.maximum(v, np.clip(1 - np.abs(yy - c) / 3.2, 0, 1))
    blit_mask(surface, x0, y0, dither(v, int(t * 10), 0))


def td_terrain(surface, rect, t, seed=0.0):
    """Wireframe landscape rushing towards you."""
    x0, y0, w, h = rect
    t += seed
    hor, fr = 2, (t * 1.6) % 1.0
    for i in range(1, 9):
        z = i - fr
        if z < 0.4:
            continue
        pts = []
        for sx in range(0, w + 1, 2):
            wx = (sx - w / 2) * z / 10.0
            hgt = 1.6 * math.sin(wx * 0.9 + (z + t * 1.6) * 0.8) + math.sin(wx * 0.35 - t * 0.3)
            pts.append((x0 + sx, y0 + hor + (h - hor) / z - hgt / z * 3))
        pygame.draw.lines(surface, ON, False, pts)


ANIMATIONS = [
    td_plasma, td_xor_rings, td_metaballs, td_starfield, td_cube, td_tunnel,
    td_rotozoom, td_life, td_fire, td_gauges, td_needles, td_scope, td_radar,
    td_helix, td_raster, td_terrain,
]

# ------------------------------------------------------------------
# Editable text (remote: Idle Animation Theme -> Technical Dreams text)
# ------------------------------------------------------------------
DEFAULT_HEADLINE = "TRIVIACADE"
DEFAULT_SCROLLER = ("TRIVIACADE    BROUGHT TO YOU BY DJ MARK    TECHNICAL DREAMS    2048 PIXELS    "
                    "20 FRAMES A SECOND    ONE COLOUR    GREETINGS TO EVERYONE ON THE DANCE FLOOR    "
                    "TO THE BAR    CODE BY CLAUDE    KEEP DREAMING")
HEADLINE_MAX, SCROLLER_MAX = 24, 600
_texts = {"loaded": False, "headline": "", "scroller": ""}


def _load_texts():
    if _texts["loaded"]:
        return
    _texts["loaded"] = True
    try:
        with open(config.TD_TEXT_FILE, encoding="utf-8") as f:
            d = json.load(f)
        _texts["headline"] = str(d.get("headline", ""))[:HEADLINE_MAX]
        _texts["scroller"] = str(d.get("scroller", ""))[:SCROLLER_MAX]
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[TECHNICAL DREAMS] Could not read {config.TD_TEXT_FILE}: {e}")


def headline():
    _load_texts()
    return _texts["headline"].strip() or DEFAULT_HEADLINE


def scroller():
    _load_texts()
    return _texts["scroller"].strip() or DEFAULT_SCROLLER


def get_texts():
    return {"headline": headline(), "scroller": scroller(),
            "default_headline": DEFAULT_HEADLINE, "default_scroller": DEFAULT_SCROLLER,
            "headline_max": HEADLINE_MAX, "scroller_max": SCROLLER_MAX}


def set_texts(new_headline, new_scroller):
    """Saves both texts; blank (or the default) means "use the default", and
    only what differs from the default is stored."""
    _load_texts()
    h = " ".join(str(new_headline or "").split())[:HEADLINE_MAX]
    sc = " ".join(str(new_scroller or "").split())[:SCROLLER_MAX]
    _texts["headline"] = "" if h == DEFAULT_HEADLINE else h
    _texts["scroller"] = "" if sc == " ".join(DEFAULT_SCROLLER.split()) else sc
    store = {k: _texts[k] for k in ("headline", "scroller") if _texts[k]}
    tmp = config.TD_TEXT_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=1)
    os.replace(tmp, config.TD_TEXT_FILE)
    return get_texts()


def headline_lines():
    """The headline as 1 or 2 lines that fit the 64px width ("|" forces
    the break; otherwise the most even split at a space)."""
    text = headline()
    if "|" in text:
        lines = [x.strip() for x in text.split("|", 1)]
    elif text_width(text) <= 64:
        lines = [text]
    else:
        words = text.split()
        if len(words) > 1:
            k = min(range(1, len(words)),
                    key=lambda k: abs(text_width(" ".join(words[:k])) - text_width(" ".join(words[k:]))))
            lines = [" ".join(words[:k]), " ".join(words[k:])]
        else:
            half = (len(text) + 1) // 2
            lines = [text[:half], text[half:]]
    out = []
    for line in lines:
        while line and text_width(line) > 64:
            line = line[:-1]
        out.append(line)
    return out


def _headline_mask(ys, from_row=0):
    """Headline stamped on a canvas: one line at ys[0], or two at ys[1:].
    from_row > 0 keeps only each line's glyph rows from that row down."""
    m = np.zeros((H, W), bool)
    lines = headline_lines()
    rows = [ys[0]] if len(lines) == 1 else ys[1:]
    for line, y in zip(lines, rows):
        a = text_mask(line)
        if from_row:
            a = a.copy()
            a[:from_row] = False
        _stamp(m, a, (W - a.shape[1]) // 2, y)
    return m


# ------------------------------------------------------------------
# The DEMO -- 64x32 across panels 3-6
# ------------------------------------------------------------------
GAP = max(0, int(getattr(config, "SI_ROW23_GAP_PX", 0)))  # physical seam between left/right panels
H, W = 32, 64 + GAP   # virtual canvas; the seam columns are dropped before display
_KEEP = np.r_[0:32, 32 + GAP:W]
_buf = None


def _lines_canvas():
    global _buf
    if _buf is None:
        _buf = pygame.Surface((W, H))
    _buf.fill((0, 0, 0))
    return _buf


def _canvas_mask():
    return pygame.surfarray.array_red(_buf).T > 0


def _sc_boot(tl, dur, beat):
    """A loader typing itself out, then a progress bar."""
    m = np.zeros((H, W), bool)
    n = int(tl * 20)  # characters typed so far
    y = 0
    for line in ("TC-OS 1.0", "ONE COLOUR", "LOADING"):
        shown = line[:max(0, n)]
        n -= len(line) + 3  # a short pause between lines
        if not shown:
            break
        _stamp(m, text_mask(shown), 1, y)
        if len(shown) < len(line):
            if int(tl * 4) % 2 == 0:
                cx = 2 + text_mask(shown).shape[1]
                m[y:y + 7, cx:cx + 3] = True
            break
        y += 8
    if n > 0:
        _stamp(m, text_mask("." * (int(tl * 3) % 4)), 2 + text_mask("LOADING").shape[1], 16)
        p = min(1.0, n / 60.0)
        m[25, 1:W - 1] = m[31, 1:W - 1] = True
        m[25:32, 1] = m[25:32, W - 2] = True
        m[27:30, 3:3 + int(p * (W - 6))] = True
    return m


def _title_targets():
    key = ("title_targets", headline())
    t = _st.get(key)
    if t is None:
        m = _headline_mask((12, 4, 20))
        ys, xs = np.nonzero(m)
        rng = np.random.RandomState(7)
        ang = rng.uniform(0, 2 * math.pi, len(xs))
        t = _st[key] = (xs.astype(float), ys.astype(float),
                                   W / 2 + 60 * np.cos(ang), H / 2 + 40 * np.sin(ang),
                                   rng.uniform(0, 1.6, len(xs)), rng.uniform(0.6, 1.4, len(xs)))
    return t


def _sc_title(tl, dur, beat):
    xs, ys, sx, sy, delay, spd = _title_targets()
    p = np.clip((tl - delay) / 1.4, 0, 1)
    p = 1 - (1 - p) ** 3
    x, y = sx + (xs - sx) * p, sy + (ys - sy) * p
    boom = dur - 1.6
    if tl > boom:
        e = (tl - boom) ** 1.6 * 30 * spd
        x = x + (xs - W / 2) / 10 * e + np.cos(delay * 9) * e * 0.4
        y = y + (ys - H / 2) / 6 * e + np.sin(delay * 9) * e * 0.4
    m = np.zeros((H, W), bool)
    xi, yi = np.round(x).astype(int), np.round(y).astype(int)
    ok = (xi >= 0) & (xi < W) & (yi >= 0) & (yi < H)
    m[yi[ok], xi[ok]] = True
    if 3.2 < tl < boom:  # shimmer: a glint sweeping across the settled letters
        gx = (tl - 3.2) * 40 % (W + 30) - 15
        yy, xx = _grid(H, W)
        m &= ~((np.abs(xx - gx - (yy - 16) * 0.5) < 1.2) & m)
    return m


def _sc_stars(tl, dur, beat):
    sx, sy, z0 = _stars("demo_stars", 140, 3.3)
    speed = 0.15 + 0.9 * (tl / dur) ** 2
    travelled = 0.15 * tl + 0.3 * tl ** 3 / dur ** 2
    buf = _lines_canvas()
    z = (z0 - travelled) % 1.0 + 0.02
    zt = z + min(0.08, speed * 0.06)
    for i in range(len(z)):
        a = (W / 2 + sx[i] / z[i] * 14, H / 2 + sy[i] / z[i] * 9)
        b = (W / 2 + sx[i] / zt[i] * 14, H / 2 + sy[i] / zt[i] * 9)
        pygame.draw.line(buf, ON, a, b)
    return _canvas_mask()


def _sc_cube(tl, dur, beat):
    buf = _lines_canvas()
    kick = 1.0 + 0.18 * math.exp(-beat * 6)
    for scale, sgn, cx in ((22.0 * kick, 1, W / 2), (8.0, -1, W / 2)):
        pts = []
        for v in _CUBE_V:
            x, y, z = _rot(v, tl * 0.8 * sgn, tl * 0.55, tl * 0.35 * sgn)
            s = scale / (z + 3.4)
            pts.append((cx + x * s * 1.2, H / 2 + y * s))
        for i, j in _CUBE_E:
            pygame.draw.line(buf, ON, pts[i], pts[j])
    return _canvas_mask()


def _sc_plasma(tl, dur, beat):
    yy, xx = _grid(H, W)
    v = (np.sin(xx / 7.0 + tl) + np.sin(yy / 5.0 + tl * 1.2) + np.sin((xx + yy) / 9.0 + tl * 0.6)
         + np.sin(np.hypot(xx - W / 2 - 18 * math.sin(tl * 0.4), yy - H / 2 - 8 * math.cos(tl * 0.5)) / 4 - tl * 2))
    return dither(0.5 + 0.5 * np.sin(v * 1.3 + tl * 1.5))


def _sc_metaballs(tl, dur, beat):
    yy, xx = _grid(H, W)
    f = np.zeros((H, W), np.float32)
    r = 30.0 * (1 + 0.25 * math.exp(-beat * 5))
    for k in range(5):
        cx = W / 2 + (W / 2 - 6) * math.sin(tl * (0.5 + 0.13 * k) + k * 1.7)
        cy = H / 2 + (H / 2 - 5) * math.cos(tl * (0.7 + 0.11 * k) + k * 2.3)
        f += r / ((xx - cx) ** 2 + (yy - cy) ** 2 + 1.0)
    m = dither(np.clip((f - 0.4) / 0.7, 0, 1))
    return ~m if int(tl / (dur / 4)) % 2 else m


def _sc_tunnel(tl, dur, beat):
    yy, xx = _grid(H, W)
    cx, cy = W / 2 + 12 * math.sin(tl * 0.7), H / 2 + 5 * math.cos(tl * 0.9)
    dx, dy = (xx - cx) / 2.0, yy - cy
    d = np.hypot(dx, dy) + 0.5
    u = 20.0 / d + tl * 4
    v = np.arctan2(dy, dx) / math.pi * 8 + tl * 0.8
    chk = (np.floor(u).astype(int) + np.floor(v).astype(int)) % 2 == 0
    return chk & dither(np.clip(d / 12.0, 0, 1))


def _td_tile():
    t = _st.get("td_tile")
    if t is None:
        t = np.zeros((10, 14), bool)
        _stamp(t, text_mask("TC"), 1, 1)
        _st["td_tile"] = t
    return t


def _sc_rotozoom(tl, dur, beat):
    yy, xx = _grid(H, W)
    tile = _td_tile()
    a = tl * 0.6
    s = 0.35 + 0.3 * (1 + math.sin(tl * 0.8))
    ca, sa = math.cos(a) * s, math.sin(a) * s
    u = ((xx - W / 2) * ca - (yy - H / 2) * sa + tl * 6).astype(int) % tile.shape[1]
    v = ((xx - W / 2) * sa + (yy - H / 2) * ca + tl * 2).astype(int) % tile.shape[0]
    return tile[v, u]


_TORUS = [((1 + 0.45 * math.cos(b)) * math.cos(a), (1 + 0.45 * math.cos(b)) * math.sin(a), 0.45 * math.sin(b))
          for a in np.linspace(0, 2 * math.pi, 20, endpoint=False)
          for b in np.linspace(0, 2 * math.pi, 9, endpoint=False)]


def _sc_dots(tl, dur, beat):
    m = np.zeros((H, W), bool)
    kick = 1 + 0.12 * math.exp(-beat * 6)
    for p in _TORUS:
        x, y, z = _rot(p, tl * 0.9, tl * 0.5, tl * 0.3)
        s = 24.0 * kick / (z + 3.2)
        px, py = int(W / 2 + x * s * 2.0), int(H / 2 + y * s * 0.8)
        size = 2 if z < -0.2 else 1
        m[max(0, py):max(0, py + size), max(0, px):max(0, px + size)] = True
    return m


def _sc_terrain(tl, dur, beat):
    buf = _lines_canvas()
    hor, fr = 6, (tl * 2.0) % 1.0
    for i in range(1, 12):
        z = i - fr
        if z < 0.45:
            continue
        pts = []
        for sx in range(0, W + 1, 2):
            wx = (sx - W / 2) * z / 14.0
            hgt = 1.8 * math.sin(wx * 0.8 + (z + tl * 2.0) * 0.7) + 1.2 * math.sin(wx * 0.3 - tl * 0.4)
            pts.append((sx, hor + (H - hor) / z - hgt / z * 5))
        pygame.draw.lines(buf, ON, False, pts)
    sun_y = hor - 1
    pygame.draw.circle(buf, ON, (W // 2, sun_y), 5)
    pygame.draw.line(buf, (0, 0, 0), (0, sun_y + 1), (W, sun_y + 1))
    return _canvas_mask()


def _sc_fire(tl, dur, beat):
    s = _st.get("demo_fire")
    if s is None or tl < s["tl"] - 0.5:
        s = _st["demo_fire"] = {"heat": np.zeros((H + 2, W), np.float32), "tl": tl,
                                "rng": np.random.RandomState(5)}
    txt = _headline_mask((16, 8, 22))
    src = np.zeros((H + 2, W), bool)
    src[:H] = _headline_mask((16, 8, 22), from_row=3)  # letters' lower half burns; flames lick up through them
    steps = 0
    while tl - s["tl"] >= 0.05 and steps < 6:
        _fire_step(s["heat"], s["rng"], src, cool=0.03)
        s["tl"] += 0.05
        steps += 1
    m = dither(np.clip(s["heat"][:H], 0, 1))
    return m | txt


_SCROLL_PX_PER_SEC = 26.0


def _greetz():
    return "    " + scroller() + "    "


def _scroller_seconds():
    return (_x2(text_mask(_greetz())).shape[1] + W) / _SCROLL_PX_PER_SEC


def _sc_scroller(tl, dur, beat):
    yy, xx = _grid(H, W)
    bars = np.zeros((H, W), np.float32)
    for k in range(2):
        c = H / 2 + 13 * math.sin(tl * 1.6 + k * 3.1)
        bars = np.maximum(bars, np.clip(1 - np.abs(yy - c) / 3.0, 0, 1) * 0.55)
    m = dither(bars, int(tl * 12), 0)
    txt = _x2(text_mask(_greetz()))
    off = int(tl * _SCROLL_PX_PER_SEC) - W
    layer = np.zeros((H, W), bool)
    for x in range(W):
        col = x + off
        if 0 <= col < txt.shape[1]:
            dy = int(round(4 * math.sin(tl * 2.5 + x * 0.05)))
            y = 9 + dy
            ya, yb = max(0, y), min(H, y + 14)
            layer[ya:yb, x] = txt[ya - y:yb - y, col]
    halo = layer.copy()
    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        halo |= np.roll(np.roll(layer, dy, 0), dx, 1)
    return (m & ~halo) | layer


def _sc_end(tl, dur, beat):
    m = np.zeros((H, W), bool)
    a, b = _x2(text_mask("THE", tight=False)), _x2(text_mask("END"))
    _stamp(m, a, (W - a.shape[1]) // 2, 1)
    _stamp(m, b, (W - b.shape[1]) // 2, 17)
    fade = min(1.0, tl / 0.8, max(0.0, (dur - tl) / 1.5))
    return m & dither(np.full((H, W), fade, np.float32))


SCENES = [  # (name, renderer, seconds)
    ("boot", _sc_boot, 5.0),
    ("title", _sc_title, 8.0),
    ("stars", _sc_stars, 6.0),
    ("cube", _sc_cube, 9.0),
    ("plasma", _sc_plasma, 8.0),
    ("metaballs", _sc_metaballs, 8.0),
    ("tunnel", _sc_tunnel, 8.0),
    ("rotozoom", _sc_rotozoom, 8.0),
    ("dots", _sc_dots, 8.0),
    ("terrain", _sc_terrain, 8.0),
    ("fire", _sc_fire, 8.0),
    ("scroller", _sc_scroller, None),   # as long as the greetings take to scroll past
    ("end", _sc_end, 5.0),
]
XFADE = 0.5


def _dur(k):
    d = SCENES[k][2]
    return d if d is not None else _scroller_seconds()


def full_seconds():
    return sum(_dur(k) for k in range(len(SCENES)))


# What's playing: a list of scene indexes and when it started.
_run = {"scenes": [], "start": 0.0, "full": False}
_turn_next = 0      # next scene a theme turn plays
_turn_parity = 0    # alternates dancing turns: animations / demo


def play_full_demo(now=None):
    """Web remote: play the whole demo once, now."""
    now = time.time() if now is None else now
    _run.update(scenes=list(range(len(SCENES))), start=now, full=True)
    _st.pop("demo_fire", None)
    return full_seconds()


def on_dancing(now):
    """Idle cycle entered its "dancing" phase: with this theme on, every
    other turn is a slice of the demo (next two scenes). Returns the turn's
    length in seconds, or None for a normal animation turn."""
    global _turn_next, _turn_parity
    if state.idle_theme != THEME or running(now):
        return None
    _turn_parity ^= 1
    if not _turn_parity:
        return None
    ks = [_turn_next % len(SCENES), (_turn_next + 1) % len(SCENES)]
    _turn_next = (_turn_next + 2) % len(SCENES)
    _run.update(scenes=ks, start=now, full=False)
    _st.pop("demo_fire", None)
    return sum(_dur(k) for k in ks)


def running(now=None):
    now = time.time() if now is None else now
    if not _run["scenes"]:
        return False
    return now - _run["start"] < sum(_dur(k) for k in _run["scenes"])


def full_running(now=None):
    return _run["full"] and running(now)


def stop():
    _run["scenes"] = []


def _render_scene(k, tl, beat):
    name, fn, _ = SCENES[k]
    return fn(max(0.0, tl), _dur(k), beat)


_frame = {"t": None, "m": None}


def frame(now):
    """The demo's current 64x32 frame (seam columns already dropped)."""
    if _frame["t"] is not None and abs(now - _frame["t"]) < 0.005:
        return _frame["m"]
    beat = (now / max(0.2, state.dj_tempo_period)) % 1.0
    el = now - _run["start"]
    ks = _run["scenes"]
    m = np.zeros((H, W), bool)
    start = 0.0
    for i, k in enumerate(ks):
        d = _dur(k)
        if el < start + d or i == len(ks) - 1:
            lead = XFADE if i > 0 else 0.0
            tl = el - start + lead
            m = _render_scene(k, tl, beat)
            into = el - (start + d - XFADE)
            if into > 0 and i + 1 < len(ks):
                nxt = _render_scene(ks[i + 1], into, beat)
                m = np.where(_bayer(H, W) < into / XFADE, nxt, m)
            break
        start += d
    out = m[:, _KEEP]
    _frame.update(t=now, m=out)
    return out


_QUAD = {3: (0, 0), 4: (0, 32), 5: (16, 0), 6: (16, 32)}


def render_demo_panel(surface, pid, rect, now):
    """Draws this panel's quarter of the demo frame."""
    r, c = _QUAD[pid]
    blit_mask(surface, rect[0], rect[1], frame(now)[r:r + 16, c:c + 32])


THEME = "Technical Dreams"
