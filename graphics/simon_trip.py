"""Simon's lit pad, getting progressively trippier every round (2026-10-15).

Round 1 is the plain solid red square. Each cleared round layers in more
psychedelia on the same 32x16 pad -- a faint shimmer, then pulsing rings,
then a swirling plasma, then kaleidoscope spokes, then posterised
interference bands, row glitch and beat strobe -- all driven by one 0..1
"trip" level so the climb is continuous, never a hard jump. Red-only LED
matrix, so it's all brightness (the pad never goes fully dark: the player
still has to see which square is lit)."""
import math

import numpy as np
import pygame

import config
from state import state
from graphics.technical_dreams import _grid

# The panels are 1-bit (a pixel is on or off -- no gray levels), so the
# 0..1 brightness field below is turned into on/off: low rounds DITHER it
# (sparkle/holes in the solid square, density = brightness), high rounds
# threshold it hard (bold-edged rings/spokes/bands).
_BAYER = (np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]], np.float32) + 0.5) / 16.0


def trip_level(round_n):
    if round_n <= config.SIMON_TRIP_STRAIGHT_ROUNDS:
        return 0.0
    return float(np.clip((round_n - 1) / (config.SIMON_TRIP_ROUNDS_TO_MAX - 1), 0.0, 1.0)) ** 0.75


def pad_field(h, w, t, trip, pad=0):
    """Brightness 0..1 per pixel for the lit pad at trip level `trip`."""
    if trip <= 0.0:
        return np.ones((h, w), np.float32)
    yy, xx = _grid(h, w)
    tt = t + pad * 1.7
    period = max(0.2, state.dj_tempo_period or 0.5)
    ph = (t / period) % 1.0
    kick = math.exp(-ph * 5.0)                      # 1 on the beat, decays

    x = xx - (w - 1) / 2.0
    y = yy - (h - 1) / 2.0
    # Row glitch: late rounds tear the rows sideways.
    if trip > 0.55:
        x = x + np.sin(yy * 1.9 + tt * 23.0) * (trip - 0.55) * 4.5 * (0.4 + kick)
    r = np.hypot(x, y)
    th = np.arctan2(y, x)
    # Swirl: angle twists more with radius as the rounds climb.
    th = th + trip * 0.35 * r * math.sin(tt * 0.6) + tt * trip * 0.9 * (1 if pad % 2 == 0 else -1)

    rings = np.sin(r * (0.55 + 0.9 * trip) - tt * (2.0 + 6.0 * trip) + kick * 1.5 * trip)
    k = 2 + int(trip * 6)                            # spoke count grows
    spokes = np.sin(th * k + tt * 2.2) * np.cos(r * 0.35 - tt)
    plasma = (np.sin(x * 0.45 + tt * 1.3) + np.sin(y * 0.7 - tt * 1.7)
              + np.sin((x + y) * 0.3 + tt) + np.sin(r * 0.5 - tt * 0.8)) / 4.0

    w1 = min(1.0, trip * 3.0)
    w2 = float(np.clip((trip - 0.25) * 2.5, 0.0, 1.0))
    w3 = float(np.clip((trip - 0.10) * 2.0, 0.0, 1.0))
    v = (w1 * rings + w2 * spokes + w3 * plasma) / (w1 + w2 + w3 + 1e-6)
    v = np.clip(0.5 + 0.5 * v, 0.0, 1.0)
    v = v ** (1.0 + trip * 1.6)                      # more contrast as it climbs
    if trip > 0.6:                                   # posterise into hard bands
        steps = 5 - int((trip - 0.6) * 5)            # 5 -> 3 levels
        v = np.floor(v * steps + 0.5) / steps
    v = np.clip(v + kick * 0.35 * trip, 0.0, 1.0)    # beat strobe

    amp = min(1.0, trip * 1.8) * 0.97                # how deep the pattern cuts into solid red
    return 1.0 - amp * (1.0 - v)


def pad_mask(h, w, t, trip, pad=0):
    """1-bit version of pad_field(): bool array, True = pixel lit."""
    if trip <= 0.0:
        return np.ones((h, w), bool)
    f = pad_field(h, w, t, trip, pad)
    shift = int(t * (6 + 10 * trip)) + pad                     # crawling dither so it shimmers
    yy, xx = _grid(h, w)
    bayer = _BAYER[(yy.astype(int) + shift) % 4, (xx.astype(int) + shift * 2) % 4]
    d = max(0.0, 1.0 - trip * 1.6)                            # dither weight: 1 early -> 0 late
    thr = d * bayer + (1.0 - d) * 0.36
    return f > thr


def draw_pad(surface, rect, t, round_n, pad=0):
    x0, y0, w, h = rect
    m = pad_mask(h, w, t, trip_level(round_n), pad)
    rgb = np.zeros((w, h, 3), np.uint8)
    rgb[..., 0] = m.T * 255
    surface.blit(pygame.surfarray.make_surface(rgb), (x0, y0))
