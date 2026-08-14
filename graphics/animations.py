import math
import random
import pygame
from config import (
    RED_FULL, RED_DIM, BLACK, BTN1_STATUS_PULSE_INTERVAL_SECONDS,
    IDLE_ANIMATION_THEMES, IDLE_THEME_DEFAULT,
)
from state import state

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
    right after a Btn2 quiz-fetch attempt comes back with "no credits"
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
# NEW CRITTER / NOVELTY SET
# ==========================================
# Same constraints as every set above: pygame.draw.* only (the panel clip
# rect keeps them inside their own 32x16 tile), RED_FULL/RED_DIM/BLACK only
# (red-only matrix hardware).


def anim_squirrel(surface, rect, t, seed=0.0):
    """A squirrel scurries back and forth across the panel floor, its
    bushy tail curling up behind it and its legs alternating mid-scamper."""
    x0, y0, w, h = rect
    span = w + 10
    period = 3.2
    phase = ((t + seed) % period) / period
    tri = phase * 2 if phase < 0.5 else 2 - phase * 2  # there and back
    facing_right = phase < 0.5

    fx = x0 + int(tri * (span - 10)) - 5
    fy = y0 + h - 5
    bob = int(abs(math.sin(t * 10.0)) * 1)

    # Body + head, drawn nose-first in whichever direction it's scurrying.
    nose_dx = 4 if facing_right else -4
    pygame.draw.ellipse(surface, RED_FULL, (fx - 3, fy - 4 - bob, 8, 5))
    pygame.draw.rect(surface, RED_FULL, (fx + nose_dx - 1, fy - 3 - bob, 2, 2))

    # Curled bushy tail, arcing up behind the body (opposite the nose).
    tail_dx = -1 if facing_right else 1
    pygame.draw.arc(surface, RED_FULL,
                     (fx - 5 + tail_dx * 4, fy - 11 - bob, 7, 9), 0.2, 3.4, 2)

    # Alternating scamper legs.
    leg_phase = int(t * 9.0) % 2
    for i, lx in enumerate((-2, 1)):
        ly_off = 1 if (i == leg_phase) else 0
        pygame.draw.line(surface, RED_DIM, (fx + lx, fy), (fx + lx, fy + 2 - ly_off))


def anim_duck(surface, rect, t, seed=0.0):
    """A duck waddles side to side, bobbing its tail and flapping a wing,
    with a bright bill out front."""
    x0, y0, w, h = rect
    cx = x0 + w // 2 + int(math.sin(t * 0.8 + seed) * 8)
    waddle = int(math.sin(t * 7.0 + seed) * 1)
    cy = y0 + h - 5 + waddle
    facing_right = math.cos(t * 0.8 + seed) >= 0

    pygame.draw.ellipse(surface, RED_FULL, (cx - 5, cy - 3, 10, 6))          # body
    pygame.draw.circle(surface, RED_FULL, (cx + (4 if facing_right else -4), cy - 5), 3)  # head
    bill_dx = 3 if facing_right else -3
    pygame.draw.rect(surface, RED_DIM,
                      (cx + (4 if facing_right else -4) + bill_dx - (1 if facing_right else 2),
                       cy - 5, 3, 2))

    # Flapping wing.
    flap = int((math.sin(t * 6.0 + seed) + 1.0) * 1.5)
    pygame.draw.line(surface, RED_DIM, (cx, cy - 2), (cx - 2, cy - 2 - flap))

    # Tail bob.
    tail_dx = -6 if facing_right else 6
    pygame.draw.line(surface, RED_DIM, (cx + tail_dx // 2, cy - 3), (cx + tail_dx, cy - 4 - waddle))

    # Webbed feet, alternating.
    step = int(t * 7.0) % 2
    for i, lx in enumerate((-2, 2)):
        if i == step:
            pygame.draw.line(surface, RED_DIM, (cx + lx, cy + 3), (cx + lx, cy + 4))


def anim_celtic_cross(surface, rect, t, seed=0.0):
    """A Celtic cross (ringed cross) glows and shimmers gently -- the ring
    pulses in brightness while sparkle points travel slowly around it,
    reading as a subtle rotation without needing true bitmap rotation."""
    x0, y0, w, h = rect
    cx, cy = x0 + w // 2, y0 + h // 2

    glow = (math.sin(t * 1.4 + seed) + 1.0) * 0.5
    ring_color = RED_FULL if glow > 0.4 else RED_DIM

    pygame.draw.circle(surface, ring_color, (cx, cy), 6, 1)
    pygame.draw.line(surface, RED_FULL, (cx, cy - 8), (cx, cy + 8), 2)   # vertical beam
    pygame.draw.line(surface, RED_FULL, (cx - 5, cy - 2), (cx + 5, cy - 2), 2)  # crossbar

    # Sparkle points that slowly travel around the ring, selling a subtle
    # "rotation" without needing true bitmap rotation.
    for i in range(3):
        ang = (t * 0.6 + seed) + i * (2 * math.pi / 3)
        px = cx + int(math.cos(ang) * 6)
        py = cy + int(math.sin(ang) * 6)
        surface.set_at((px, py), RED_FULL)


def anim_dancing_pixelman(surface, rect, t, seed=0.0):
    """A blocky pixel-art figure dances in place: arms alternate up/down,
    legs kick out, and the whole body bounces on the beat."""
    x0, y0, w, h = rect
    cx = x0 + w // 2
    bounce = int(abs(math.sin(t * 4.0 + seed)) * 2)
    cy = y0 + h - 3 - bounce

    pygame.draw.rect(surface, RED_FULL, (cx - 1, cy - 10, 3, 3))   # head
    pygame.draw.rect(surface, RED_FULL, (cx - 2, cy - 7, 5, 5))    # torso

    arm_phase = math.sin(t * 5.0 + seed)
    left_up = arm_phase > 0
    pygame.draw.line(surface, RED_FULL, (cx - 2, cy - 6),
                      (cx - 5, cy - 8 if left_up else cy - 3))
    pygame.draw.line(surface, RED_FULL, (cx + 3, cy - 6),
                      (cx + 6, cy - 3 if left_up else cy - 8))

    leg_phase = int(t * 5.0 + seed) % 2
    pygame.draw.line(surface, RED_DIM, (cx - 1, cy - 2),
                      (cx - 3 if leg_phase == 0 else cx - 1, cy + 2))
    pygame.draw.line(surface, RED_DIM, (cx + 1, cy - 2),
                      (cx + 1 if leg_phase == 0 else cx + 3, cy + 2))


# ==========================================
# MYSTERY BAND TEASER (panels 3-6, DJ mode)
# ==========================================
def anim_question_mark(surface, rect, t, seed=0.0):
    """Big pulsing question mark -- shown on panels 3-6 during the DJ-mode
    "Mystery Band" teaser window (drivers/mystery_band_engine.py) while the
    artist/track title is hidden on panels 1+2."""
    x0, y0, w, h = rect
    cx, cy = x0 + w // 2, y0 + h // 2
    pulse = (math.sin(t * 3.0 + seed) + 1.0) / 2.0
    color = RED_FULL if pulse > 0.35 else RED_DIM

    pygame.draw.arc(surface, color, (cx - 5, cy - 8, 10, 8), math.pi * 0.15, math.pi * 1.55, 2)
    pygame.draw.line(surface, color, (cx, cy - 1), (cx, cy + 2), 2)
    pygame.draw.circle(surface, color, (cx, cy + 5), 1)


# ==========================================
# BTN1-HOLD STATUS OVERLAY (panel 3, DJ mode)
# ==========================================
# Shown while Btn1 is held (or during its 10s post-release persistence
# window -- inputs/gamepad.py::_process_btn1_hold()/_handle_btn1_release()),
# reflecting whether track questions / Price Game are available for the
# current track, or the AI is exhausted for it. Same red-only-hardware
# constraint as every other set above -- star/dollar/arrow are told apart by
# SHAPE and a two-frame mid->large size pulse, not by color.
def _mid_large_radius(t, mid, large):
    frame = int(t / BTN1_STATUS_PULSE_INTERVAL_SECONDS) % 2
    return large if frame == 1 else mid


def anim_bold_star(surface, rect, t, seed=0.0):
    """Filled five-point star, pulsing between a mid and large size --
    "track questions available" indicator."""
    x0, y0, w, h = rect
    cx, cy = x0 + w // 2, y0 + h // 2
    r_outer = _mid_large_radius(t, min(w, h) * 0.32, min(w, h) * 0.45)
    r_inner = r_outer * 0.45

    pts = []
    for i in range(10):
        ang = (math.pi * i / 5) - math.pi / 2
        r = r_outer if i % 2 == 0 else r_inner
        pts.append((int(cx + math.cos(ang) * r), int(cy + math.sin(ang) * r)))
    pygame.draw.polygon(surface, RED_FULL, pts)


def anim_bold_dollar(surface, rect, t, seed=0.0):
    """Bold hand-drawn "$" -- vertical bar through two S-curve arcs --
    pulsing between a mid and large size. "Price Game available" indicator."""
    x0, y0, w, h = rect
    cx, cy = x0 + w // 2, y0 + h // 2
    half_h = int(_mid_large_radius(t, h * 0.28, h * 0.40))
    radius = max(3, int(half_h * 0.9))

    pygame.draw.line(surface, RED_FULL, (cx, cy - half_h - 2), (cx, cy + half_h + 2), 2)
    pygame.draw.arc(surface, RED_FULL,
                     (cx - radius, cy - half_h, radius * 2, half_h + 1),
                     math.pi * 0.5, math.pi * 1.9, 2)
    pygame.draw.arc(surface, RED_FULL,
                     (cx - radius, cy - 1, radius * 2, half_h + 1),
                     math.pi * 1.5, math.pi * 2.9, 2)


def anim_arrow_down(surface, rect, t, seed=0.0):
    """Thick downward chevron + shaft, pulsing between a mid and large size,
    with a brightness flicker (RED_FULL/RED_DIM) to read as an alert --
    "no questions / AI exhausted" indicator."""
    x0, y0, w, h = rect
    cx, cy = x0 + w // 2, y0 + h // 2
    half = _mid_large_radius(t, h * 0.22, h * 0.34)
    flicker = (int(t * 4.0 + seed) % 2) == 0
    color = RED_FULL if flicker else RED_DIM

    shaft_top = int(cy - half)
    head_y = int(cy + half * 0.35)
    tip_y = int(cy + half)

    pygame.draw.line(surface, color, (cx, shaft_top), (cx, head_y), 2)
    pygame.draw.polygon(surface, color, [
        (int(cx - half * 0.6), head_y),
        (int(cx + half * 0.6), head_y),
        (cx, tip_y),
    ])


# ==========================================
# BIRTHDAY SET (idle theme, panels 3-6)
# ==========================================
# Same constraints as every set above: pygame.draw.* only (the panel clip
# rect keeps them inside their own 32x16 tile), RED_FULL/RED_DIM/BLACK only
# (red-only matrix hardware), deterministic from t + seed.


def anim_balloon_float(surface, rect, t, seed=0.0):
    """A balloon drifts upward on a wiggling string, swaying side to side,
    fading near the top of its climb and resetting from the bottom --
    same float-and-reset shape as the Halloween set's anim_ghost_float,
    but rising instead of hovering in place."""
    x0, y0, w, h = rect
    period = 4.5
    phase = ((t + seed) % period) / period
    cy = y0 + h + 3 - int(phase * (h + 10))
    cx = x0 + w // 2 + int(math.sin(t * 1.6 + seed) * 5)
    color = RED_DIM if phase > 0.85 else RED_FULL

    pygame.draw.ellipse(surface, color, (cx - 4, cy - 5, 8, 9))
    pygame.draw.polygon(surface, color, [(cx - 1, cy + 4), (cx + 1, cy + 4), (cx, cy + 6)])  # knot
    pts = [(cx + int(math.sin(t * 4.0 + i * 0.8 + seed) * 1), cy + 6 + i * 2) for i in range(3)]
    pygame.draw.lines(surface, RED_DIM, False, pts, 1)


def anim_birthday_cake(surface, rect, t, seed=0.0):
    """A two-tier cake sits still while its candle flame flickers -- same
    candlelight technique as anim_jack_o_lantern (two summed sines at
    incommensurate rates for an irregular, non-obviously-pulsing flicker)."""
    x0, y0, w, h = rect
    cx, cy = x0 + w // 2, y0 + h - 3

    pygame.draw.rect(surface, RED_FULL, (cx - 8, cy - 5, 16, 5))
    pygame.draw.rect(surface, RED_FULL, (cx - 5, cy - 8, 10, 3))
    for dx in range(-8, 9, 3):
        pygame.draw.line(surface, RED_DIM, (cx + dx, cy - 5), (cx + dx, cy - 4))

    pygame.draw.line(surface, RED_DIM, (cx, cy - 8), (cx, cy - 11), 1)
    flick = (math.sin(t * 7.5 + seed) + math.sin(t * 12.0 + seed * 2.0)) * 0.5
    flame_color = RED_FULL if flick > -0.3 else RED_DIM
    pygame.draw.circle(surface, flame_color, (cx, cy - 12), 1)


def anim_confetti_burst(surface, rect, t, seed=0.0):
    """Confetti launches from bottom-center in a fan, arcs under gravity,
    and resets -- individual falling/flying points via set_at(), same
    per-pixel technique (and the same manual bounds check) as anim_rain."""
    x0, y0, w, h = rect
    period = 2.2
    phase = ((t + seed) % period) / period
    origin_x, origin_y = x0 + w // 2, y0 + h - 1

    for i in range(7):
        ang = (i / 7.0) * math.pi - math.pi * 0.15  # fan upward
        speed = 10.0 + (i % 3) * 3.0
        px = origin_x + math.cos(ang) * speed * phase
        py = origin_y - math.sin(ang) * speed * phase + 14.0 * phase * phase  # gravity arc
        ix, iy = int(px), int(py)
        if x0 <= ix < x0 + w and y0 <= iy < y0 + h:
            surface.set_at((ix, iy), RED_FULL if i % 2 == 0 else RED_DIM)


def anim_gift_box(surface, rect, t, seed=0.0):
    """A wrapped present sits still, ribboned, while its lid pops open and
    shut on a slow beat -- the bow rides up and down with the lid."""
    x0, y0, w, h = rect
    cx, cy = x0 + w // 2, y0 + h - 3
    period = 2.0
    phase = ((t + seed) % period) / period
    pop = max(0.0, 1.0 - abs(phase - 0.15) / 0.15)  # quick pop, mostly closed
    lid_lift = int(pop * 3)

    pygame.draw.rect(surface, RED_FULL, (cx - 6, cy - 6, 12, 7))             # box
    pygame.draw.rect(surface, RED_FULL, (cx - 7, cy - 8 - lid_lift, 14, 3))  # lid
    pygame.draw.line(surface, RED_DIM, (cx, cy - 6), (cx, cy + 1), 2)        # vertical ribbon
    pygame.draw.line(surface, RED_DIM, (cx - 6, cy - 3), (cx + 6, cy - 3), 2)  # horizontal ribbon

    ly = cy - 8 - lid_lift
    pygame.draw.polygon(surface, RED_FULL, [(cx - 3, ly), (cx - 1, ly - 3), (cx, ly)])
    pygame.draw.polygon(surface, RED_FULL, [(cx + 3, ly), (cx + 1, ly - 3), (cx, ly)])


def anim_party_hat(surface, rect, t, seed=0.0):
    """A conical party hat wobbles side to side, striped, with a pompom
    bouncing on top."""
    x0, y0, w, h = rect
    cx = x0 + w // 2 + int(math.sin(t * 1.8 + seed) * 3)
    base_y = y0 + h - 4
    tip_y = base_y - 10

    pygame.draw.polygon(surface, RED_FULL, [(cx - 6, base_y), (cx + 6, base_y), (cx, tip_y)])
    for frac in (0.35, 0.6):
        yy = int(base_y - (base_y - tip_y) * frac)
        span = int(6 * (1.0 - frac))
        pygame.draw.line(surface, BLACK, (cx - span, yy), (cx + span, yy))

    bob = int(abs(math.sin(t * 6.0 + seed)) * 2)
    pygame.draw.circle(surface, RED_FULL, (cx, tip_y - 2 - bob), 2)


def anim_streamer_wave(surface, rect, t, seed=0.0):
    """Three party streamers ripple across the panel like slow wavy
    banners, each on its own row, phase, and brightness."""
    x0, y0, w, h = rect
    rows = (2, 7, 12)
    for i, row_y in enumerate(rows):
        pts = []
        for col in range(0, w, 2):
            wave = math.sin(t * 2.5 + col * 0.5 + i * 1.7 + seed) * 2.0
            pts.append((x0 + col, y0 + row_y + int(wave)))
        if len(pts) >= 2:
            pygame.draw.lines(surface, RED_DIM if i == 1 else RED_FULL, False, pts, 1)


def anim_sparkler(surface, rect, t, seed=0.0):
    """A hand-held sparkler stick stays put while its tip fizzes with
    darting sparks -- same traveling-points technique as
    anim_celtic_cross's ring sparkle, but faster and denser so it reads as
    fizzing rather than slow orbiting."""
    x0, y0, w, h = rect
    base_x, base_y = x0 + 6, y0 + h - 2
    tip_x, tip_y = x0 + w - 6, y0 + 4

    pygame.draw.line(surface, RED_DIM, (base_x, base_y), (tip_x, tip_y), 1)

    for i in range(5):
        ang = t * 6.0 + i * (2 * math.pi / 5) + seed
        dist = 2.0 + (math.sin(t * 9.0 + i * 3.1) + 1.0) * 1.5
        px, py = tip_x + int(math.cos(ang) * dist), tip_y + int(math.sin(ang) * dist)
        if x0 <= px < x0 + w and y0 <= py < y0 + h:
            surface.set_at((px, py), RED_FULL if i % 2 == 0 else RED_DIM)
    pygame.draw.circle(surface, RED_FULL, (tip_x, tip_y), 1)


def anim_party_popper(surface, rect, t, seed=0.0):
    """A party popper cone fires a burst of streamer lines outward on a
    loop -- same beat shape as anim_confetti_burst but drawn as connected
    lines from a fixed cone rather than individual falling points, so the
    two read as distinct effects within the same theme."""
    x0, y0, w, h = rect
    cx, cy = x0 + w // 2, y0 + h - 2
    period = 2.4
    phase = ((t + seed) % period) / period

    pygame.draw.polygon(surface, RED_FULL, [(cx - 3, cy), (cx + 3, cy), (cx, cy - 5)])

    if phase < 0.5:
        burst = phase / 0.5
        for i in range(5):
            ang = math.pi * 0.5 + (i - 2) * 0.35
            length = burst * 10.0
            ex = cx + int(math.cos(ang) * length)
            ey = cy - 5 - int(math.sin(ang) * length)
            pygame.draw.line(surface, RED_DIM if i % 2 == 0 else RED_FULL, (cx, cy - 5), (ex, ey), 1)


# ==========================================
# QUESTION MARKS SET (idle theme, panels 3-6)
# ==========================================
def _draw_qmark(surface, cx, cy, r, color, thickness=1):
    """Shared glyph for every animation in this theme: a "?" built from an
    arc hook, a short stem, and a dot, scaled proportionally to `r` (its
    overall cap height) so the same drawing code covers everything from a
    tiny scattered mark to a large floating one."""
    r = max(3, min(8, int(r)))
    hook_w = r
    hook_h = max(2, int(r * 0.8))
    top = cy - int(r * 1.3)

    pygame.draw.arc(surface, color, (cx - hook_w, top, hook_w * 2, hook_h * 2),
                     math.pi * 0.15, math.pi * 1.55, max(1, thickness))
    stem_y0 = top + hook_h + 1
    stem_y1 = stem_y0 + max(1, int(r * 0.35))
    pygame.draw.line(surface, color, (cx, stem_y0), (cx, stem_y1), max(1, thickness))
    pygame.draw.circle(surface, color, (cx, stem_y1 + 2), max(1, thickness))


def anim_qmark_float_up(surface, rect, t, seed=0.0):
    """One large question mark drifts slowly upward, swaying side to side
    like a balloon, fading near the top and resetting from the bottom."""
    x0, y0, w, h = rect
    period = 5.0
    phase = ((t + seed) % period) / period
    cy = y0 + h + 4 - int(phase * (h + 12))
    cx = x0 + w // 2 + int(math.sin(t * 1.3 + seed) * 4)
    color = RED_DIM if phase > 0.85 else RED_FULL
    _draw_qmark(surface, cx, cy, 6, color, thickness=2)


def anim_qmark_scatter(surface, rect, t, seed=0.0):
    """Three question marks of different sizes drift left-to-right at
    their own height, speed, and phase, wrapping around when they exit --
    reads as a loose scattered field rather than one focal object, the
    clearest "varying sizes" statement in this set. Same multi-row-crossing
    technique as the Halloween set's anim_bat_swarm."""
    x0, y0, w, h = rect
    span = w + 10
    marks = ((3.0, 6.0, 2), (4.5, 4.0, 9), (2.5, 8.5, 13))  # (radius, speed, row_y)
    for i, (r, speed, row_y) in enumerate(marks):
        cx = x0 + span - int((t * speed + seed * 13.0 + i * 17.0) % span) - 5
        cy = y0 + row_y
        _draw_qmark(surface, cx, cy, r, RED_DIM if i == 1 else RED_FULL, thickness=1)


def anim_qmark_pulse_grow(surface, rect, t, seed=0.0):
    """A single question mark planted at center, breathing between small
    and large -- pure size change with no travel, the deliberate opposite
    of the other three animations in this set."""
    x0, y0, w, h = rect
    cx, cy = x0 + w // 2, y0 + h // 2
    pulse = (math.sin(t * 2.0 + seed) + 1.0) / 2.0
    r = 3.5 + pulse * 3.5
    color = RED_FULL if pulse > 0.3 else RED_DIM
    _draw_qmark(surface, cx, cy, r, color, thickness=2 if pulse > 0.5 else 1)


def anim_qmark_bounce(surface, rect, t, seed=0.0):
    """A mid-sized question mark bounces around the panel interior like a
    screensaver logo, ricocheting off all four edges -- independent
    triangle-wave motion on each axis."""
    x0, y0, w, h = rect
    r = 3
    margin = r + 2
    span_x = max(2, w - 2 * margin)
    span_y = max(2, h - 2 * margin)

    px = (t * 7.3 + seed * 5.0) % (2 * span_x)
    px = px if px <= span_x else 2 * span_x - px
    py = (t * 5.1 + seed * 8.0) % (2 * span_y)
    py = py if py <= span_y else 2 * span_y - py

    cx = x0 + margin + int(px)
    cy = y0 + margin + int(py)
    _draw_qmark(surface, cx, cy, r, RED_FULL, thickness=1)


# ==========================================
# IDLE ANIMATION DEAL (panels 3-6)
# ==========================================
# GENERAL_IDLE_ANIMATIONS have no seasonal/occasion imagery (dots, lines,
# critters), so they read fine under any theme and are always in the pool.
# IDLE_THEMES layers a themed set on top, selected by state.idle_theme
# (operator-controlled, see web/remote_server.py::/api/idle/theme) -- e.g.
# "Halloween" pool = GENERAL_IDLE_ANIMATIONS + IDLE_THEMES["Halloween"].
GENERAL_IDLE_ANIMATIONS = [
    anim_scanner,
    anim_equalizer,
    anim_rain,
    anim_pulse_rings,
    anim_squirrel,
    anim_duck,
    anim_dancing_pixelman,
]

IDLE_THEMES = {
    "Halloween": [
        anim_bat_swarm,
        anim_ghost_float,
        anim_jack_o_lantern,
        anim_spider_drop,
        anim_lightning_flash,
        anim_grave_hand,
        anim_witch_flyby,
        anim_skull_chatter,
        anim_celtic_cross,
    ],
    "Birthday": [
        anim_balloon_float,
        anim_birthday_cake,
        anim_confetti_burst,
        anim_gift_box,
        anim_party_hat,
        anim_streamer_wave,
        anim_sparkler,
        anim_party_popper,
    ],
    "Question Marks": [
        anim_qmark_float_up,
        anim_qmark_scatter,
        anim_qmark_pulse_grow,
        anim_qmark_bounce,
    ],
}
# Keeps config.IDLE_ANIMATION_THEMES (the admin panel's dropdown source of
# truth) and this dict from silently drifting apart -- fails loudly at
# import time instead of an admin selection quietly doing nothing.
assert set(IDLE_THEMES) == set(IDLE_ANIMATION_THEMES), (
    f"IDLE_THEMES keys {sorted(IDLE_THEMES)} != config.IDLE_ANIMATION_THEMES {sorted(IDLE_ANIMATION_THEMES)}"
)

IDLE_PANEL_IDS = (3, 4, 5, 6)

# panel_id -> (animation function, phase seed). Re-dealt on every new track.
_panel_deal = {}


def _current_theme_pool():
    themed = IDLE_THEMES.get(state.idle_theme) or IDLE_THEMES[IDLE_THEME_DEFAULT]
    return GENERAL_IDLE_ANIMATIONS + themed


def deal_panel_animations():
    """Shuffle GENERAL_IDLE_ANIMATIONS + the current theme's set (see
    state.idle_theme) and deal one *distinct* animation to each of panels
    3-6, like dealing cards face-up across a table. Called whenever a new
    track appears, so the idle DJ display remixes itself per track instead
    of showing the same four animations in the same four slots forever --
    and also called directly the instant the operator changes the theme
    (web/remote_server.py::/api/idle/theme), so a theme switch is visible
    on the very next frame rather than waiting for the next song."""
    pool = _current_theme_pool()
    hand = random.sample(pool, min(len(IDLE_PANEL_IDS), len(pool)))
    _panel_deal.clear()
    for pid, fn in zip(IDLE_PANEL_IDS, hand):
        # Fresh phase seed as well, so an animation dealt to the same panel
        # twice in a row still starts from a different point in its cycle.
        _panel_deal[pid] = (fn, random.uniform(0.0, 10.0))


# Deal an opening hand at import time so the very first rendered frame has
# something on every bottom panel.
deal_panel_animations()


def render_panel_animation(surface, panel_id, rect, t):
    fn, seed = _panel_deal.get(panel_id, (GENERAL_IDLE_ANIMATIONS[0], panel_id * 1.37))

    old_clip = surface.get_clip()
    surface.set_clip(pygame.Rect(rect))
    fn(surface, rect, t, seed)
    surface.set_clip(old_clip)
