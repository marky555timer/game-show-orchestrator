"""Story songs (2026-10-23): Pink Floyd "On the Run" scored as the Wizard of
Oz tornado scene -- Dorothy and Toto running, Miss Gulch on her bicycle, the
twister on the horizon, the witch on her broom, the house landing and the
rainbow. For this one song the top LED panels (graphics/oz_scene.py), the
marquee (wled_engine._apply_story) and the outline strip
(accent_engine._target_look) are all driven off the song position, and the
"TRIVIA NIGHT / SCAN QR" idle prompts on panels 3-6 are suppressed.

The song position is time.time() - state.now_playing_started_at (the same
clock drivers/hook_words.py uses), so a seek or a cue-out re-aligns it. Times
below are seconds into "Pink Floyd - On The Run (2023 Remaster).mp3"
(213.6s; sequencer opening, hush at 12s, build at 37s, lull at 65s, build at
87s, the relentless run 107-172s, the explosion at 183.1s, quiet after 190s).
"""
import math
import os
import time

import config
from state import state

SONG_FILES = {"pink floyd - on the run (2023 remaster).mp3"}

BOOM_AT = 183.1
CRESCENDO_AT = BOOM_AT - 10.0   # the big flashing (lights + sky flashes) lives only here
FULLSCREEN_END = 186.0          # the six-panel tornado + explosion hand back to the top panels here

# (start, name) -- the acts, in order
ACTS = [
    (0.0, "kansas"),     # Dorothy + Toto running, clear sky
    (12.0, "gulch"),     # Miss Gulch on her bike, clouds gathering
    (37.0, "twister"),   # the funnel rises on the horizon
    (65.0, "lull"),      # the eerie calm before it
    (87.0, "witch"),     # the wind returns, bike snatched up, the witch
    (107.0, "chase"),    # the relentless run: witch + twister after them
    (CRESCENDO_AT, "engulf"),   # the twister takes everything -- the big flashing starts here
    (BOOM_AT, "boom"),   # explosion flash
    (186.0, "ashes"),    # dark, a few specks falling
    (190.0, "rainbow"),  # the house lands, the rainbow
]

# Lightning strikes. Before CRESCENDO_AT a bolt is only a line drawn on the top
# panels (no flashing); from CRESCENDO_AT on they flash the whole rig, ramping up.
BOLTS = [45.0, 58.0, 95.0, 104.0, 116.6, 134.0, 152.0, 167.0,
         173.6, 174.6, 175.4, 176.1, 176.8, 177.4, 178.0, 178.5, 179.0, 179.5,
         180.0, 180.4, 180.8, 181.2, 181.6, 181.95, 182.3, 182.6, 182.85]


def is_story_file(path):
    return bool(path) and os.path.basename(path).lower() in SONG_FILES


def position(now=None):
    """Seconds into the story song while it plays in DJ mode, else None."""
    if not config.STORY_SONGS_ENABLED or state.blackout_active:
        return None
    if state.mode != state.MODE_DJ or state.show_phase != "live" or state.medley_active:
        return None
    if not is_story_file(state.now_playing_path) or not state.now_playing_started_at:
        return None
    now = time.time() if now is None else now
    pos = now - state.now_playing_started_at
    if pos < -0.5:
        return None
    return max(0.0, pos)


def act(pos):
    name = ACTS[0][1]
    for start, n in ACTS:
        if pos >= start:
            name = n
    return name


def act_progress(pos):
    """(name, 0..1 through the act)."""
    for i, (start, n) in enumerate(ACTS):
        end = ACTS[i + 1][0] if i + 1 < len(ACTS) else 214.0
        if start <= pos < end:
            return n, (pos - start) / (end - start)
    return ACTS[-1][1], 1.0


def bolt_index(pos):
    """How many lightning strikes have happened by `pos` (edge trigger)."""
    return sum(1 for b in BOLTS if pos >= b)


def lightning(pos, raw=False):
    """0..1 flash strength right now: a hard hit that decays over ~0.25s,
    with a quick stutter. Zero before CRESCENDO_AT unless raw (the matrix
    uses raw to know when to draw a bolt line; nothing flashes)."""
    if not raw and pos < CRESCENDO_AT:
        return 0.0
    best = 0.0
    for b in BOLTS:
        d = pos - b
        if 0 <= d < 0.35:
            v = math.exp(-d * 9.0)
            if 0.09 < d < 0.14:
                v *= 0.15            # the stutter
            best = max(best, v)
    if BOOM_AT <= pos < BOOM_AT + 2.0:
        best = max(best, math.exp(-(pos - BOOM_AT) * 1.6))
    return best


def storm(pos):
    """0..1 how threatening things are (drives wind, swirl speed)."""
    pts = [(0, 0.0), (12, 0.08), (37, 0.3), (65, 0.65), (66, 0.35), (87, 0.35),
           (107, 0.6), (140, 0.7), (CRESCENDO_AT, 0.75), (BOOM_AT, 1.0), (186, 0.0)]
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        if t0 <= pos < t1:
            return v0 + (v1 - v0) * (pos - t0) / (t1 - t0)
    return 0.0 if pos >= 186 else 0.0


# Short vignettes on the lower two panel rows: (start s, duration s, kind, row).
# row 0 = panels 3+4, row 1 = panels 5+6. Sparse on purpose -- the top is the show.
# graphics/oz_scene.py draws them; the marquee accents the same pair of panels.
LOWER = [(16, 7, "run", 1), (44, 8, "funnel", 0), (76, 6, "funnel", 1),
         (101, 7, "witch", 0), (123, 8, "twirl", 1), (146, 7, "run", 0),
         (163, 7, "witch", 1)]

ROW_SEGMENTS = {0: ("panel3", "panel4"), 1: ("panel5", "panel6")}  # (left, right)

_ACCENT_COLORS = {"run": (255, 170, 30), "funnel": (40, 255, 70),
                  "witch": (170, 0, 255), "twirl": (90, 255, 160)}


def lower_accent(pos):
    """(row, strength 0..1, kind) while a lower vignette is on, else None."""
    for start, dur, kind, row in LOWER:
        if start <= pos < start + dur:
            return row, max(0.0, min(1.0, (pos - start) / 0.7, (start + dur - pos) / 0.7)), kind
    return None


def fullscreen(pos):
    """True while the tornado takes over all six panels (graphics/oz_scene.py::
    render_full): the last ten seconds of the build, then the explosion."""
    return CRESCENDO_AT <= pos < FULLSCREEN_END


def full_marquee(pos):
    """True when the whole marquee lights (the witch's green burst, the boom,
    the finale); otherwise it stays dark except for the accents. It stays OFF
    for the whole six-panel tornado (CRESCENDO_AT..BOOM_AT) so the borders don't
    compete with it."""
    return pos >= BOOM_AT or 99.0 <= pos < 100.4


def accent_color(kind, strength, ang, t):
    """A comet of the vignette's colour circling the lit pair of panels."""
    c = _ACCENT_COLORS.get(kind, GOLD)
    v = 0.25 + 0.75 * max(0.0, math.cos(ang - t * 3.0)) ** 2
    v *= strength
    return (c[0] * v, c[1] * v, c[2] * v)


# ------------------------------------------------------------ marquee
def _mix(a, b, f):
    f = max(0.0, min(1.0, f))
    return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f)


def _hsv(h, s, v):
    h = (h % 1.0) * 6.0
    i = int(h)
    f = h - i
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    return [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i % 6]


GREEN = (40, 255, 70)
PURPLE = (170, 0, 255)
GOLD = (255, 170, 30)


def marquee_color(x, y, pos, t):
    """RGB (0-255 floats) for the marquee bulb at normalized (x, y)."""
    name, prog = act_progress(pos)
    cx, cy = x - 0.5, y - 0.5
    ang = math.atan2(cy, cx)
    rad = math.hypot(cx, cy)
    s = storm(pos)
    if name == "kansas":
        # sunny gold waves rolling along the strip, dimming toward dusk
        v = 0.55 + 0.45 * math.sin(x * 7.0 - t * 5.0)
        col = _mix(GOLD, (200, 120, 20), prog)
        rgb = tuple(c * v * (1.0 - 0.3 * prog) for c in col)
    elif name == "gulch":
        # dusk turning olive; a slow, uneasy roll
        v = 0.35 + 0.2 * math.sin(x * 4.0 - t * 2.0 + y * 3.0)
        col = _mix((190, 140, 20), (110, 130, 25), prog)
        rgb = tuple(c * v for c in col)
    elif name == "twister":
        # green storm light swirling faster as the funnel grows
        sp = 1.5 + 4.0 * prog
        v = 0.25 + 0.35 * (0.5 + 0.5 * math.sin(ang * 2.0 + t * sp)) + 0.15 * prog
        rgb = tuple(c * v for c in _mix((90, 130, 25), GREEN, 0.4 + 0.5 * prog))
    elif name == "lull":
        # dead calm: slow sickly green breathing, nothing moves
        v = 0.12 + 0.12 * (0.5 + 0.5 * math.sin(t * 1.2))
        if pos > 82.0:                                  # wind starting to come back
            v += 0.25 * (pos - 82.0) / 5.0 * (0.5 + 0.5 * math.sin(ang * 2 + t * 4))
        rgb = tuple(c * v for c in (60, 230, 90))
    elif name == "witch":
        # green and violet witchlight, spinning up
        sp = 2.0 + 2.0 * prog
        f = 0.5 + 0.5 * math.sin(ang * 2.0 - t * sp)
        v = 0.3 + 0.45 * (0.5 + 0.5 * math.sin(rad * 7.0 - t * 3.0))
        rgb = tuple(c * v for c in _mix(GREEN, PURPLE, f))
        if 99.0 <= pos < 100.4:                         # Gulch becomes the witch: green burst
            k = 1.0 - (pos - 99.0) / 1.4
            rgb = _mix(rgb, (120, 255, 120), 0.9 * k)
    elif name == "chase":
        # the vortex: faster and brighter all the way, throbbing on the witch's dives
        sp = 2.0 + 2.2 * prog                       # a steady, easy swirl -- the frenzy is saved for the end
        f = 0.5 + 0.5 * math.sin(ang * 2.0 - t * sp)
        v = 0.4 + 0.5 * (0.5 + 0.5 * math.sin(ang * 2.0 + rad * 4.0 - t * sp))
        rgb = tuple(c * v for c in _mix(GREEN, PURPLE, f))
    elif name == "engulf":
        # everything spinning at once, bleaching toward white as the end nears
        sp = 12.0 + 8.0 * prog
        v = 0.5 + 0.5 * math.sin(ang * 3.0 - t * sp)
        base = _mix(_mix(GREEN, PURPLE, 0.5 + 0.5 * math.sin(t * 5.0 + rad * 8.0)), (255, 255, 255), prog * 0.6)
        rgb = tuple(c * (0.3 + 0.7 * v) for c in base)
    elif name == "boom":
        k = max(0.0, 1.0 - (pos - BOOM_AT) / 2.8)
        rgb = (255 * k, 255 * k * 0.95, 255 * k * 0.8)
    elif name == "ashes":
        # near-dark, a few embers
        flick = 0.5 + 0.5 * math.sin(t * 9.0 + x * 40.0 + y * 17.0)
        v = 0.05 * flick if flick > 0.8 else 0.0
        rgb = (255 * v, 90 * v, 10 * v)
    else:  # rainbow: gentle drifting arc of color, fading in then easing out
        fade = min(1.0, (pos - 190.0) / 5.0) * (1.0 - max(0.0, (pos - 204.0) / 9.0))
        r, g, b = _hsv(x * 0.85 + t * 0.08 - y * 0.15, 0.85, 0.9)
        rgb = (r * 255 * fade, g * 255 * fade, b * 255 * fade)
    L = lightning(pos)
    if L > 0.0:
        rgb = _mix(rgb, (255, 255, 255), min(1.0, L))
    return rgb


# ------------------------------------------------------------ outline
def outline_look(pos):
    """(fx, r, g, b, sx, r2, g2, b2) for the outline strip. Changes only at
    act boundaries / speed steps so the serial link isn't spammed; the
    lightning white flash is sent separately via accent_engine.flash()."""
    name, prog = act_progress(pos)
    F = config
    if name == "kansas":
        return (F.ACCENT_FX_CHASE, 255, 170, 30, 110, 60, 35, 0)
    if name == "gulch":
        return (F.ACCENT_FX_BREATHE, 190, 150, 20, 60 + int(prog * 80), None, None, None)
    if name == "twister":
        return (F.ACCENT_FX_CHASE, 40, 230, 70, 90 + int(prog * 60) // 20 * 20, 0, 40, 0)
    if name == "lull":
        return (F.ACCENT_FX_BREATHE, 50, 220, 90, 25, None, None, None)
    if name == "witch":
        return (F.ACCENT_FX_CHASE, 170, 0, 255, 90 + int(prog * 40) // 20 * 20, 0, 255, 70)
    if name == "chase":
        step = int(prog * 3)                       # three gentle speed steps up the run
        return (F.ACCENT_FX_CHASE, 40, 255, 70, 100 + step * 20, 170, 0, 255)
    if name == "engulf":
        return (F.ACCENT_FX_CHASE_FLASH, 255, 255, 255, 255, 40, 255, 70)
    if name == "boom":
        return (F.ACCENT_FX_SOLID, 255, 255, 255, 128, None, None, None)
    if name == "ashes":
        return (F.ACCENT_FX_SOLID, 0, 0, 0, 128, None, None, None)
    return (F.ACCENT_FX_RAINBOW, 255, 255, 255, 40, None, None, None)
