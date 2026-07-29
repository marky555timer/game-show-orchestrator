import math
import pygame
from config import RED_FULL, RED_DIM


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


ANIMATIONS = [anim_scanner, anim_equalizer, anim_rain, anim_pulse_rings]

# Stable per-panel assignment so panels 3/4/5/6 always look distinct.
PANEL_ANIMATION_INDEX = {3: 0, 4: 1, 5: 2, 6: 3}


def render_panel_animation(surface, panel_id, rect, t):
    fn = ANIMATIONS[PANEL_ANIMATION_INDEX.get(panel_id, 0)]
    seed = panel_id * 1.37

    old_clip = surface.get_clip()
    surface.set_clip(pygame.Rect(rect))
    fn(surface, rect, t, seed)
    surface.set_clip(old_clip)
