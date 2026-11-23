"""TRIVIACADE intermission show (2026-11-13).

During the post-win intermission the top strip (panels 1+2) and panels 3+4
used to sit blank next to the NEXT GAME countdown on 5+6. They now play three
10-second scenes in rotation, each one a 64x32 picture across those four
panels with the word TRIVIACADE as the star:

  1. MARQUEE   -- theatre bulbs chase round the strip while the letters pop
                  on one by one, a glint sweeps the word, searchlight beams
                  fan out below.
  2. CHOMP     -- the word under maze walls; a chomper eats a row of dots
                  with a ghost on its tail, grabs the power pellet, turns and
                  eats the (now scared) ghost for 200.
  3. EQ        -- the letters ride a wave on top of a full-width music
                  equalizer with falling peak dots; hypes up in the last
                  seconds.

The top strip (rows 0-15) is designed to be good on its own: announcement
call-outs punch over it (graphics/matrix_canvas.py) and the scene carries on
underneath. Panels are 1-bit, so shading is ordered dithering
(graphics/technical_dreams.py helpers). Scenes start from the one after the
last intermission's first scene, so back-to-back breaks don't repeat.
"""
import math
import time

import numpy as np

from graphics.technical_dreams import (
    text_mask, _stamp, draw_line, disc, dither, blit_mask, plot, hash01,
)

H, W = 32, 64
SCENE_SECONDS = 10.0
STATS_SECONDS = 12.0   # stats page after each scene: long enough to read the scrolling headers
WORD = "TRIVIACADE"
FADE = 0.45

_start = None       # when this intermission's run began
_last_call = 0.0
_first_scene = -1   # advances each intermission
_cache = (0.0, None)


# ------------------------------------------------------------------
# Word helpers
# ------------------------------------------------------------------
def _letters():
    """[(mask 7xw, x)] for each letter of WORD, centred across 64px."""
    masks = [text_mask(c, tight=True) for c in WORD]
    gap = 1
    total = sum(m.shape[1] for m in masks) + gap * (len(masks) - 1)
    x = (W - total) // 2
    out = []
    for m in masks:
        out.append((m, x))
        x += m.shape[1] + gap
    return out


_LETTERS = _letters()


def _word_mask(y, upto=None, dy=None):
    """The word on a 32x64 canvas at row y. upto = how many letters are in;
    dy(k) = per-letter vertical offset."""
    m = np.zeros((H, W), bool)
    for k, (lm, x) in enumerate(_LETTERS):
        if upto is not None and k >= upto:
            break
        _stamp(m, lm, x, y + (dy(k) if dy else 0))
    return m


# ------------------------------------------------------------------
# Shared bulb border for the 64x16 strip
# ------------------------------------------------------------------
_PERIM = ([(x, 0) for x in range(W)] + [(W - 1, y) for y in range(1, 15)]
          + [(x, 15) for x in range(W - 1, -1, -1)] + [(0, y) for y in range(14, 0, -1)])


def _bulbs(m, t, speed=18.0, pitch=4, on_all=False):
    off = int(t * speed)
    for i, (x, y) in enumerate(_PERIM):
        if on_all or (i - off) % pitch == 0:
            m[y, x] = True


# ------------------------------------------------------------------
# Scene 1: MARQUEE
# ------------------------------------------------------------------
def _scene_marquee(t):
    m = np.zeros((H, W), bool)
    n = len(_LETTERS)
    # letters pop in one by one; each arrives as a solid flash block first
    upto = int(max(0.0, (t - 0.4)) / 0.2)
    word = _word_mask(4, upto=min(n, upto))
    if upto < n + 2 and t > 0.4:
        k = min(n - 1, upto)
        lm, x = _LETTERS[k]
        if upto < n and (t - 0.4) % 0.2 < 0.1:
            blk = np.zeros((H, W), bool)
            _stamp(blk, np.ones_like(lm), x, 4)
            word |= blk
    # glint sweeping across once it is all in
    if t > 3.0:
        ph = ((t - 3.0) % 2.2) / 1.1
        if ph < 1.0:
            gx = -6 + ph * (W + 12)
            yy, xx = np.mgrid[0:H, 0:W]
            band = np.abs((xx - gx) - (yy - 8) * 0.6) < 1.8
            word &= ~band
    m |= word
    # bulbs: spin up, then steady chase, rapid flash for the last second
    if t > 0.1:
        flash = t > 9.0 and int(t * 6) % 2 == 0
        _bulbs(m, t, speed=18 if t < 9.0 else 30, on_all=flash)
    # searchlight beams from below the bottom edge
    yy, xx = np.mgrid[16:H, 0:W].astype(np.float32)
    dx, dy = xx - 32, yy - 44
    r = np.hypot(dx, dy)
    ang = np.arctan2(dx, -dy)
    sweep = math.sin(t * 0.9) * 0.5
    beams = np.sin((ang + sweep) * 7.0) > 0.25
    bright = np.clip(1.5 - (r - 6) / 26.0, 0, 1) * beams
    reveal = min(1.0, max(0.0, (t - 1.2) / 1.5))
    m[16:H, :] |= dither(bright * reveal, 0, 16)
    # chase rows hugging the seams of the lower panels
    for x in range(W):
        if (x - int(t * 20)) % 5 == 0:
            m[17, x] = True
        if (x + int(t * 20)) % 5 == 0:
            m[30, x] = True
    return m


# ------------------------------------------------------------------
# Scene 2: CHOMP
# ------------------------------------------------------------------
def _pac(cx, cy, facing, mouth, r=4):
    """Chomper mask 32x64: a disc with a wedge cut out toward `facing` (+1/-1)."""
    d = disc(H, W, cx, cy, r + 0.3)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    a = np.arctan2(yy - cy, (xx - cx) * facing)
    return d & ~((np.abs(a) < mouth) & (np.hypot(xx - cx, yy - cy) > 0.5))


def _ghost(cx, cy, t, scared=False, flash=False):
    """Ghost mask 32x64 centred on (cx, cy): 9x9, dome head, wavy skirt."""
    g = np.zeros((9, 9), bool)
    for dx in range(-4, 5):
        top = int(round(abs(dx) ** 2 / 6.0))            # rounded dome
        g[top:8, dx + 4] = True
    for dx in range(-4, 5):
        if (dx + int(t * 8)) % 3 == 0:
            g[8, dx + 4] = True
    if scared:
        g[3:5, 2:4] = g[3:5, 5:7] = True               # lit eyes
        g[6, 1::2] = False                               # zig-zag mouth
        if flash:
            g &= (np.add.outer(np.arange(9), np.arange(9)) % 2 == 0)
    else:
        g[3:5, 2:4] = g[3:5, 5:7] = False                # dark eye sockets
        g[4, 3] = g[4, 6] = True                         # pupils
    m = np.zeros((H, W), bool)
    _stamp(m, g, int(cx) - 4, int(cy) - 4)
    return m


def _scene_chomp(t):
    m = np.zeros((H, W), bool)
    # arcade title word, with a scanline that crawls down on the beat
    word = _word_mask(4)
    sl = int((t * 9) % 18)
    if 1 < sl < 16 and int(t * 2) % 2 == 0:
        word[sl, :] = ~word[sl, :]
    m |= word
    # maze walls on the strip and around the lane below
    for x in range(2, 62):
        if (x // 4) % 5 != 4 or t < 0.8:
            m[1, x] = m[14, x] = True
    m[18, 1:63] = True
    m[30, 1:63] = True
    LANE = 24
    mouth = 0.15 + 0.55 * abs(math.sin(t * 11))

    def dots(passed_x, direction):
        for x in range(4, 61, 4):
            gone = (x < passed_x) if direction > 0 else (x > passed_x)
            if not gone:
                m[LANE, x] = True

    if t < 4.6:
        # leg 1: left -> right with a ghost on its tail
        px = -6 + (t / 4.6) * 80
        dots(px, +1)
        m |= _ghost(px - 16, LANE, t)
        m |= _pac(int(px), LANE, +1, mouth)
        if int(t * 4) % 2 == 0:                         # blinking power pellet
            m[LANE - 1:LANE + 2, 61:64] = True
    else:
        # leg 2: right -> left, the ghost turns scared and gets eaten
        u = t - 4.6
        px = 72 - (u / 4.4) * 84
        dots(px, -1)
        k = min(1.0, max(0.0, (u - 2.2) / 1.4))
        gap = 16 - 16 * (k * k * (3 - 2 * k))           # chomper closes the gap
        if gap > 4:
            m |= _ghost(px - gap, LANE, t, scared=True, flash=(u > 1.8 and int(u * 8) % 2 == 0))
        elif gap > 0.5 or u < 4.2:
            # caught: it pops "200" where it was
            if gap <= 4:
                _stamp(m, text_mask("200", tight=True), int(px) - 20, LANE - 3)
        m |= _pac(int(px), LANE, -1, mouth)
    return m


# ------------------------------------------------------------------
# Scene 3: EQ
# ------------------------------------------------------------------
_EQ_BARS = 21
_peaks = {}


def _scene_eq(t):
    m = np.zeros((H, W), bool)
    hype = 1.0 + 0.9 * max(0.0, (t - 6.5) / 3.5)
    amp = 1.0 + 1.6 * max(0.0, (t - 6.0) / 4.0)
    # letters on a travelling wave
    word = _word_mask(5, dy=lambda k: int(round(2.2 * amp * math.sin(t * 5.0 - k * 0.75) * (1 if amp < 2 else 0.8))) - 1)
    m |= word
    # under-line that ticks on the beat
    beat = int(t * 4)
    w0 = 4 + (beat % 6) * 10
    m[14, w0:w0 + 8] = True
    # equalizer across panels 3+4
    base = H - 1
    top_limit = 17
    for k in range(_EQ_BARS):
        x = 1 + k * 3
        a = 0.5 + 0.5 * math.sin(t * 5.3 * hype + k * 0.9)
        b = 0.5 + 0.5 * math.sin(t * 2.7 + k * 1.7 + hash01(k) * 6)
        c = 0.5 + 0.5 * math.sin(t * 9.1 + k * 0.4)
        lvl = 0.15 + 0.85 * min(1.0, (0.5 * a + 0.35 * b + 0.15 * c) ** 0.6 * (0.8 + 0.1 * hype))
        h = int(round(lvl * (base - top_limit)))
        h = max(1, min(base - top_limit + 1, h))
        m[base - h + 1:base + 1, x:x + 2] = True
        pk = _peaks.get(k, 0.0)
        pk = max(pk - 0.08, float(h))
        _peaks[k] = pk
        py = base - int(pk) - 1
        if top_limit <= py < base:
            m[py, x:x + 2] = True
    return m


_SCENES = (_scene_marquee, _scene_chomp, _scene_eq)


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------
def _position(now):
    """(scene fn, seconds into the scene). Starts afresh when intermission
    rendering resumes after a gap (a new break) and rotates the first scene."""
    global _start, _last_call, _first_scene
    if _start is None or now - _last_call > 1.5:
        _start = now
        _first_scene = (_first_scene + 1) % len(_SCENES)
    _last_call = now
    e = now - _start
    cycle = SCENE_SECONDS + (STATS_SECONDS if stats_lines() else 0.0)
    idx = (_first_scene + int(e // cycle)) % len(_SCENES)
    c = e % cycle
    return idx, (c if c < SCENE_SECONDS else -1.0)   # -1 = showing the stats page


def stats_lines():
    """[(headline, detail)] x2 for the stats page, or [] before any round has
    been won: (ROUND N WINNER, XXX) and (TONIGHT'S LEADER, XXX 45 PTS)."""
    from state import state
    if not state.last_round_winner_initials:
        return []
    out = [(f"ROUND {state.last_round_number} WINNER", state.last_round_winner_initials)]
    scores = {pid: s for pid, s in state.show_cumulative_scores.items() if s > 0}
    if scores:
        top = max(scores.values())
        names = [(state.quiz_players.get(pid) or {}).get("initials", "").strip() or "???"
                 for pid, s in scores.items() if s == top]
        out.append(("TONIGHT'S LEADER", f"{' & '.join(names)} {top}pt"))
    return out


def showing_stats(now):
    """True while the stats page (not an animation scene) is up."""
    return _position(now)[1] < 0


def frame(now):
    """The current 64x32 on/off picture (computed once per display frame)."""
    global _cache
    ct, cm = _cache
    if cm is not None and abs(now - ct) < 0.012:
        return cm
    idx, t = _position(now)
    if t < 0:
        return np.zeros((H, W), bool)
    m = _SCENES[idx](t)
    edge = min(t, SCENE_SECONDS - t)
    if edge < FADE:
        m = m & dither(np.full((H, W), max(0.0, edge / FADE), np.float32))
    _cache = (now, m)
    return m


def render_top(surface, rect, now):
    """Panels 1+2 (64x16) -- rect is TOP_COMBINED."""
    blit_mask(surface, rect[0], rect[1], frame(now)[0:16, :])


def render_panel(surface, pid, rect, now):
    """Panel 3 (left) or 4 (right) of the lower half."""
    c = 0 if pid == 3 else 32
    blit_mask(surface, rect[0], rect[1], frame(now)[16:32, c:c + 32])
