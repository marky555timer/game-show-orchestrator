import math
import random
import pygame
from config import RED_FULL, RED_DIM, BLACK

# NOTE ON CLIPPING: render_panel_animation() sets a clip rect around the
# panel before calling an animation, and pygame's draw.* primitives honour
# that clip -- but Surface.set_at() does NOT. Animations that use set_at()
# must bounds-check themselves (see anim_rain); everything else should stick
# to draw.* so a stray coordinate can't bleed into a neighbouring panel.


def anim_scanner(surface, rect, t, seed=0.0):
    """KITT-style bar sweeping back and forth across the panel."""
    x0, y0, w, h = rect
    period = 2.4
    phase = ((t + seed) % period) / period
    tri = phase * 2 if phase < 0.5 else 2 - phase * 2  # triangle wave 0->1->0

    bar_w = 4
    max_x = w - bar_w
    bar_x = int(tri * max_x)
    mid_y = h // 2

    pygame.draw.rect(surface, RED_FULL, (x0 + bar_x, y0 + mid_y - 1, bar_w, 3))
    for i in range(1, 4):
        trail_x = bar_x - i * 2
        if 0 <= trail_x <= max_x:
            pygame.draw.rect(surface, RED_DIM, (x0 + trail_x, y0 + mid_y - 1, 1, 3))


def anim_equalizer(surface, rect, t, seed=0.0):
    """VU-meter style bars, each column driven by its own sine wave."""
    x0, y0, w, h = rect
    n_bars = max(1, w // 3)
    for i in range(n_bars):
        freq = 1.3 + (i % 5) * 0.35
        phase = seed + i * 0.6
        val = (math.sin(t * freq * 2 * math.pi + phase) + 1.0) / 2.0
        bar_h = max(1, int(val * h))
        bx = x0 + i * 3
        by = y0 + (h - bar_h)
        pygame.draw.rect(surface, RED_FULL, (bx, by, 2, bar_h))


def anim_rain(surface, rect, t, seed=0.0):
    """Sparse falling pixels, deterministic from t + column so it's
    reproducible frame to frame without needing real randomness."""
    x0, y0, w, h = rect
    speed = 9.0
    seed_int = int(seed * 100)

    for col in range(w):
        col_seed = (col * 37 + seed_int) % 97
        if col_seed % 5 != 0:
            continue
        y = (t * speed + col_seed * 1.7) % (h + 6) - 6
        yy = int(y)
        if 0 <= yy < h:
            surface.set_at((x0 + col, y0 + yy), RED_FULL)
        for k in range(1, 4):
            ty = yy - k
            if 0 <= ty < h:
                surface.set_at((x0 + col, y0 + ty), RED_DIM)


def anim_pulse_rings(surface, rect, t, seed=0.0):
    """Expanding square ring pulsing out from the panel center."""
    x0, y0, w, h = rect
    cx, cy = w // 2, h // 2
    period = 2.0
    phase = ((t + seed) % period) / period
    max_r = max(w, h) // 2 + 2
    r = int(phase * max_r)
    color = RED_DIM if phase > 0.6 else RED_FULL
    ring = (x0 + cx - r, y0 + cy - r, r * 2, r * 2)
    pygame.draw.rect(surface, color, ring, 1)


def anim_dancing_cat(surface, rect, t, seed=0.0):
    """Bobbing/leaning cat face -- shown on the DJ-mode status panel when
    the AI factoid/quiz request has failed or is unavailable, so the
    failure is visible on the matrix itself, not just in the console."""
    x0, y0, w, h = rect
    cx = w // 2
    bob = int(math.sin(t * 4.0 + seed) * 1.5)
    lean = int(math.sin(t * 2.2 + seed) * 2)
    fx = x0 + cx + lean
    fy = y0 + h // 2 + bob

    pygame.draw.polygon(surface, RED_FULL, [(fx - 5, fy - 3), (fx - 3, fy - 6), (fx - 1, fy - 3)])
    pygame.draw.polygon(surface, RED_FULL, [(fx + 1, fy - 3), (fx + 3, fy - 6), (fx + 5, fy - 3)])
    pygame.draw.rect(surface, RED_FULL, (fx - 5, fy - 3, 10, 7))

    blink = int(t * 1.5) % 5 == 0
    eye_color = RED_DIM if blink else BLACK
    surface.set_at((fx - 2, fy), eye_color)
    surface.set_at((fx + 2, fy), eye_color)

    wig = int(math.sin(t * 6.0) * 1)
    pygame.draw.line(surface, RED_DIM, (fx - 6, fy + 2 + wig), (fx - 9, fy + 1 + wig))
    pygame.draw.line(surface, RED_DIM, (fx + 6, fy + 2 + wig), (fx + 9, fy + 1 + wig))


def anim_coin_pop(surface, rect, t, seed=0.0):
    """A coin popping and fading -- shown on the DJ-mode status panel
    right after a Btn6 quiz-fetch attempt comes back with "no credits"
    (any API failure triggered by an explicit fetch request), distinct
    from the passive anim_dancing_cat failure indicator."""
    x0, y0, w, h = rect
    cx, cy = x0 + w // 2, y0 + h // 2
    period = 0.8
    phase = ((t + seed) % period) / period

    if phase < 0.35:
        # Pop upward and outward.
        rise = int(phase / 0.35 * 6)
        r = 3 + int(phase / 0.35 * 2)
        color = RED_FULL
    else:
        # Fade back down to nothing.
        fall_phase = (phase - 0.35) / 0.65
        rise = int((1.0 - fall_phase) * 6)
        r = max(1, int((1.0 - fall_phase) * 5))
        color = RED_FULL if fall_phase < 0.6 else RED_DIM

    coin_y = cy - rise
    pygame.draw.circle(surface, color, (cx, coin_y), r, 1)
    pygame.draw.line(surface, color, (cx - 1, coin_y), (cx + 1, coin_y))


def anim_star_burst(surface, rect, t, seed=0.0):
    """Five-point star that bursts outward from center then resets --
    shown on the DJ-mode status panel once a real AI-sourced question
    has loaded for the currently-playing track."""
    x0, y0, w, h = rect
    cx, cy = w // 2, h // 2
    period = 1.6
    phase = ((t + seed) % period) / period
    max_r = min(w, h) // 2

    if phase < 0.15:
        r = 1
        color = RED_FULL
    else:
        burst_phase = (phase - 0.15) / 0.85
        r = 1 + int(burst_phase * (max_r - 1))
        color = RED_FULL if burst_phase < 0.6 else RED_DIM

    for i in range(5):
        ang = (2 * math.pi * i / 5) - math.pi / 2
        px = x0 + cx + int(math.cos(ang) * r)
        py = y0 + cy + int(math.sin(ang) * r)
        pygame.draw.line(surface, color, (x0 + cx, y0 + cy), (px, py), 1)


# ==========================================
# SPOOKY HALLOWEEN SET
# ==========================================
# All four are drawn with pygame.draw.* only, so the panel clip rect keeps
# them inside their own 32x16 tile, and all four use RED_FULL/RED_DIM/BLACK
# only -- the physical matrix panels are red-only hardware, so depth comes
# from brightness and motion rather than hue.


def anim_bat_swarm(surface, rect, t, seed=0.0):
    """Three bats flapping right-to-left at different heights and speeds,
    each bobbing on its own sine so the swarm never marches in lockstep."""
    x0, y0, w, h = rect
    span = w + 12

    for i in range(3):
        speed = 8.0 + i * 3.0
        # Travel right-to-left; the +6 / -6 margin lets a bat slide fully
        # off one edge before wrapping in on the other.
        bx = x0 + span - ((t * speed + seed * 13.0 + i * 9.0) % span) - 6
        by = y0 + 4 + i * 4 + int(math.sin(t * 2.0 + i * 1.7 + seed) * 1.5)
        flap = int(round(math.sin(t * 10.0 + i * 2.1) * 2))
        color = RED_DIM if i == 1 else RED_FULL  # middle bat reads as further away

        pygame.draw.rect(surface, color, (bx, by, 2, 2))
        pygame.draw.line(surface, color, (bx - 1, by), (bx - 4, by - flap))
        pygame.draw.line(surface, color, (bx + 2, by), (bx + 5, by - flap))


def anim_ghost_float(surface, rect, t, seed=0.0):
    """Sheet ghost drifting side to side, bobbing, with a rippling hem and
    hollow (black) eyes and mouth punched out of the lit body."""
    x0, y0, w, h = rect
    cx = x0 + w // 2 + int(math.sin(t * 0.9 + seed) * 6)
    cy = y0 + h // 2 + int(math.sin(t * 2.1 + seed) * 1.5)

    pygame.draw.circle(surface, RED_FULL, (cx, cy - 2), 4)
    pygame.draw.rect(surface, RED_FULL, (cx - 4, cy - 2, 9, 5))

    # Rippling hem -- each column of the skirt drops its own amount.
    for i in range(9):
        wob = int((math.sin(t * 5.0 + i * 1.1 + seed) + 1.0) * 1.5)
        px = cx - 4 + i
        pygame.draw.line(surface, RED_FULL, (px, cy + 3), (px, cy + 3 + wob))

    # Face punched out of the lit body. The eyes are 2px wide and set well
    # apart, and the mouth sits three rows below them -- 1px eyes with the
    # mouth tucked directly between them merge into an "A" at this scale.
    pygame.draw.rect(surface, BLACK, (cx - 3, cy - 4, 2, 2))
    pygame.draw.rect(surface, BLACK, (cx + 2, cy - 4, 2, 2))
    pygame.draw.rect(surface, BLACK, (cx - 1, cy, 3, 1))


def anim_jack_o_lantern(surface, rect, t, seed=0.0):
    """Carved pumpkin: dim shell outline with bright carved features that
    flicker like candlelight. Two summed sines at incommensurate rates give
    an irregular flicker instead of an obvious pulse."""
    x0, y0, w, h = rect
    cx, cy = x0 + w // 2, y0 + h // 2 + 1

    flick = (math.sin(t * 7.3 + seed) + math.sin(t * 13.1 + seed * 2.0)) * 0.5
    glow = RED_FULL if flick > -0.35 else RED_DIM

    pygame.draw.rect(surface, RED_DIM, (cx - 1, cy - 9, 2, 3))          # stem
    pygame.draw.ellipse(surface, RED_DIM, (cx - 7, cy - 6, 15, 12), 1)  # shell

    pygame.draw.polygon(surface, glow, [(cx - 5, cy - 3), (cx - 2, cy - 3), (cx - 4, cy)])
    pygame.draw.polygon(surface, glow, [(cx + 2, cy - 3), (cx + 5, cy - 3), (cx + 4, cy)])

    # Jagged grin -- alternating tooth heights along the mouth line.
    for i, gx in enumerate(range(cx - 4, cx + 5)):
        tooth = 2 if i % 2 == 0 else 1
        pygame.draw.line(surface, glow, (gx, cy + 2), (gx, cy + 1 + tooth))


def anim_spider_drop(surface, rect, t, seed=0.0):
    """Spider abseiling down a thread from the top of the panel and hauling
    itself back up, legs twitching the whole way."""
    x0, y0, w, h = rect
    cx = x0 + w // 2

    period = 4.0
    phase = ((t + seed) % period) / period
    tri = phase * 2 if phase < 0.5 else 2 - phase * 2  # down then back up
    by = y0 + 3 + int(tri * (h - 8))

    pygame.draw.line(surface, RED_DIM, (cx, y0), (cx, by - 2))
    pygame.draw.rect(surface, RED_FULL, (cx - 2, by - 2, 4, 4))

    for i in range(3):
        wig = int(math.sin(t * 8.0 + i * 1.3 + seed) * 1.5)
        ly = by - 1 + i
        pygame.draw.line(surface, RED_FULL, (cx - 3, ly), (cx - 6, ly - 1 + wig))
        pygame.draw.line(surface, RED_FULL, (cx + 3, ly), (cx + 6, ly - 1 + wig))


# ==========================================
# SPOOKY HALLOWEEN SET II
# ==========================================
# Second batch, same constraints as above: pygame.draw.* only (so the panel
# clip rect contains them), RED_FULL/RED_DIM/BLACK only (red-only hardware),
# and every shape kept inside a 32x16 tile.


def anim_lightning_flash(surface, rect, t, seed=0.0):
    """Storm panel: constant drizzle, then a jagged bolt cracks down with a
    double flash. The bolt's zigzag is a fixed offset table rather than
    randomness, so the same strike replays identically frame to frame."""
    x0, y0, w, h = rect

    # Drizzle runs the whole time so the panel is never fully dark.
    for i in range(6):
        col = int((i * 7 + seed * 3) % w)
        y = int((t * 14.0 + i * 5) % h)
        pygame.draw.line(surface, RED_DIM, (x0 + col, y0 + y), (x0 + col, y0 + min(h - 1, y + 2)))

    period = 2.8
    phase = ((t + seed) % period) / period
    if phase >= 0.2:
        return

    strike = phase / 0.2
    # Two quick flashes rather than one, which reads much more like real
    # lightning than a single linear fade.
    if strike < 0.22 or 0.38 < strike < 0.5:
        pygame.draw.rect(surface, RED_DIM, (x0, y0, w, h))

    zig = (0, 3, -1, 4, 1, 5)
    bolt_x = x0 + w // 3 + int(seed * 5) % (w // 3)
    pts = []
    for i, dx in enumerate(zig):
        py = y0 + int(i * (h - 1) / (len(zig) - 1))
        pts.append((bolt_x + dx, py))
    pygame.draw.lines(surface, RED_FULL, False, pts, 1)


def anim_grave_hand(surface, rect, t, seed=0.0):
    """A hand claws its way up out of a dirt mound, fingers flexing, then
    sinks back down. Triangle-wave rise so the retreat is as visible as the
    climb."""
    x0, y0, w, h = rect
    cx = x0 + w // 2
    ground = y0 + h - 3

    # Dirt mound, drawn first so the hand rises in front of it.
    pygame.draw.arc(surface, RED_DIM, (cx - 9, ground - 2, 18, 8), 0.0, math.pi, 1)
    pygame.draw.line(surface, RED_DIM, (x0, ground + 2), (x0 + w - 1, ground + 2))

    period = 3.6
    phase = ((t + seed) % period) / period
    tri = phase * 2 if phase < 0.5 else 2 - phase * 2
    rise = int(tri * (h - 7))
    wrist_y = ground - rise

    pygame.draw.rect(surface, RED_FULL, (cx - 2, wrist_y, 5, rise if rise > 0 else 1))

    # Four fingers, each curling on its own phase so the hand looks like
    # it's grasping rather than waving.
    for i in range(4):
        fx = cx - 3 + i * 2
        curl = int((math.sin(t * 5.0 + i * 1.4 + seed) + 1.0) * 1.5)
        pygame.draw.line(surface, RED_FULL, (fx, wrist_y), (fx, wrist_y - 3 + curl))


def anim_witch_flyby(surface, rect, t, seed=0.0):
    """Witch on a broomstick crosses in front of a dim moon, bobbing, with a
    sparkle trail streaming out behind the bristles."""
    x0, y0, w, h = rect

    # Moon sits behind everything, dim so the witch silhouette reads on top.
    pygame.draw.circle(surface, RED_DIM, (x0 + w - 7, y0 + 5), 4, 1)

    span = w + 16
    wx = x0 + span - int((t * 11.0 + seed * 17.0) % span) - 8
    # Bob amplitude is only 1px and the figure sits a row low on purpose:
    # the hat apex is 7px above wy, so a taller bob clips it off the top.
    wy = y0 + h // 2 + 1 + int(math.sin(t * 2.4 + seed) * 1)

    # Broomstick, then bristles fanning off the tail.
    pygame.draw.line(surface, RED_FULL, (wx - 5, wy + 2), (wx + 6, wy + 2))
    for d in (-2, 0, 2):
        pygame.draw.line(surface, RED_DIM, (wx + 6, wy + 2), (wx + 9, wy + 2 + d))

    # Body sitting on the stick, plus the pointed hat. The brim is wider than
    # the body and the apex rakes backwards (toward the trail), which is what
    # makes the silhouette read as a witch rather than a pawn at this scale.
    pygame.draw.rect(surface, RED_FULL, (wx - 1, wy - 3, 3, 5))
    pygame.draw.line(surface, RED_FULL, (wx - 4, wy - 4), (wx + 4, wy - 4))
    pygame.draw.polygon(surface, RED_FULL, [(wx - 2, wy - 4), (wx + 3, wy - 7), (wx + 2, wy - 4)])

    # Sparkle trail -- alternates on/off per position so it twinkles. Drawn
    # with draw.rect (not set_at) so the panel clip rect contains it.
    for k in range(1, 4):
        sx = wx + 9 + k * 3
        if (int(t * 9.0) + k) % 2 == 0:
            pygame.draw.rect(surface, RED_DIM, (sx, wy + 2, 1, 1))


def anim_skull_chatter(surface, rect, t, seed=0.0):
    """Skull with hollow sockets and a jaw that chatters open and shut, teeth
    showing on the wide frames. Rocks slightly so it isn't a static prop."""
    x0, y0, w, h = rect
    cx = x0 + w // 2
    rock = int(math.sin(t * 1.8 + seed) * 2)
    cy = y0 + h // 2 - 1

    sx = cx + rock

    # Cranium + cheekbones.
    pygame.draw.rect(surface, RED_FULL, (sx - 5, cy - 6, 11, 8))
    pygame.draw.line(surface, RED_FULL, (sx - 6, cy - 3), (sx - 6, cy - 1))
    pygame.draw.line(surface, RED_FULL, (sx + 6, cy - 3), (sx + 6, cy - 1))

    # Hollow sockets and nose punched out of the lit cranium.
    pygame.draw.rect(surface, BLACK, (sx - 4, cy - 5, 3, 3))
    pygame.draw.rect(surface, BLACK, (sx + 2, cy - 5, 3, 3))
    pygame.draw.rect(surface, BLACK, (sx, cy - 1, 1, 1))

    # Jaw chatters on a fast sine; teeth only drawn when it's open enough
    # to see between them.
    gap = int((math.sin(t * 7.0 + seed * 2.0) + 1.0) * 1.5)
    jaw_y = cy + 3 + gap
    pygame.draw.rect(surface, RED_FULL, (sx - 4, jaw_y, 9, 2))
    if gap >= 2:
        for tx in range(sx - 3, sx + 5, 2):
            pygame.draw.line(surface, RED_DIM, (tx, cy + 2), (tx, jaw_y - 1))


# ==========================================
# IDLE ANIMATION DEAL (panels 3-6)
# ==========================================
IDLE_ANIMATIONS = [
    anim_scanner,
    anim_equalizer,
    anim_rain,
    anim_pulse_rings,
    anim_bat_swarm,
    anim_ghost_float,
    anim_jack_o_lantern,
    anim_spider_drop,
    anim_lightning_flash,
    anim_grave_hand,
    anim_witch_flyby,
    anim_skull_chatter,
]

IDLE_PANEL_IDS = (3, 4, 5, 6)

# panel_id -> (animation function, phase seed). Re-dealt on every new track.
_panel_deal = {}


def deal_panel_animations():
    """Shuffle the animation pool and deal one *distinct* animation to each of
    panels 3-6, like dealing cards face-up across a table. Called whenever a
    new track appears, so the idle DJ display remixes itself per track instead
    of showing the same four animations in the same four slots forever."""
    hand = random.sample(IDLE_ANIMATIONS, len(IDLE_PANEL_IDS))
    _panel_deal.clear()
    for pid, fn in zip(IDLE_PANEL_IDS, hand):
        # Fresh phase seed as well, so an animation dealt to the same panel
        # twice in a row still starts from a different point in its cycle.
        _panel_deal[pid] = (fn, random.uniform(0.0, 10.0))


# Deal an opening hand at import time so the very first rendered frame has
# something on every bottom panel.
deal_panel_animations()


def render_panel_animation(surface, panel_id, rect, t):
    fn, seed = _panel_deal.get(panel_id, (IDLE_ANIMATIONS[0], panel_id * 1.37))

    old_clip = surface.get_clip()
    surface.set_clip(pygame.Rect(rect))
    fn(surface, rect, t, seed)
    surface.set_clip(old_clip)
