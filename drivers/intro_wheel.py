"""drivers/intro_wheel.py
Pure timing/geometry for the "spin the wheel" decade picker (2026-10-14;
show open or a mid-show spin item since 2026-10-17), shared by the LED matrix (graphics/matrix_canvas.py) and the
marquee lamps (drivers/wled_engine.py) so the pointer, the highlighted
square and the lamps can never drift apart. No drawing and no I/O here.

The wheel is the 64x32 block formed by LED panels 3-6; each panel is one
square: panel 3 = 60s, 4 = 70s, 5 = 80s, 6 = 90s
(config.SHOW_INTRO_WHEEL_DECADES, in that order). Angles are degrees
clockwise from straight up, so the squares sit at: panel 4 (top-right)
0-90, panel 6 (bottom-right) 90-180, panel 5 (bottom-left) 180-270,
panel 3 (top-left) 270-360. The landing decade itself is rigged by drivers/show_engine.py into state.show_wheel_target
(the decade of the playlist song after the spin).
"""
import random

import config
from state import state

_PANEL_START_ANGLE = {4: 0.0, 6: 90.0, 5: 180.0, 3: 270.0}

# Every decade the library knows, oldest first ("1950s" ... "2020s").
ALL_DECADES = sorted(config.MUSIC_DECADES)


def can_land(decade):
    return decade in ALL_DECADES


def wheel_decades(target=None):
    """The four decades on the wheel (panels 3-6), oldest first. Normally
    config.SHOW_INTRO_WHEEL_DECADES; a target outside them slides the window
    so the target sits at its low end (older) or high end (newer)."""
    default = list(config.SHOW_INTRO_WHEEL_DECADES)
    target = state.show_wheel_target if target is None else target
    if not can_land(target) or target in default:
        return default
    i = ALL_DECADES.index(target)
    if target < default[0]:
        return ALL_DECADES[i:i + 4]
    return ALL_DECADES[max(0, i - 3):i + 1]


def target_panel():
    """Panel number (3-6) of the decade the wheel will land on."""
    try:
        return 3 + wheel_decades().index(state.show_wheel_target)
    except ValueError:
        return 3


def _land_angle():
    # Somewhere inside the target square, away from its edges. Seeded from
    # the intro's start time so every frame (and both renderers) agree.
    rng = random.Random(int(state.show_wheel_started_at * 1000))
    return _PANEL_START_ANGLE[target_panel()] + 45.0 + rng.uniform(-25.0, 25.0)


def panel_at(angle):
    a = angle % 360.0
    if a < 90.0:
        return 4
    if a < 180.0:
        return 6
    if a < 270.0:
        return 5
    return 3


def pointer(elapsed):
    """`elapsed` = seconds since the wheel started (state.show_wheel_started_at).
    Returns (angle_degrees, panel_under_pointer, landed). Eases out to a stop
    after config.SHOW_INTRO_WHEEL_TURNS extra revolutions, over
    state.show_wheel_spin_seconds (one pass of wheelTheme.wav)."""
    p = max(0.0, min(1.0, elapsed / max(0.1, state.show_wheel_spin_seconds)))
    eased = 1.0 - (1.0 - p) ** 3
    angle = eased * (config.SHOW_INTRO_WHEEL_TURNS * 360.0 + _land_angle())
    return angle, panel_at(angle), p >= 1.0


def blink_on(elapsed):
    """The landed square's blink phase (True = lit), counted from the stop."""
    since = max(0.0, elapsed - state.show_wheel_spin_seconds)
    return int(since / (config.SHOW_INTRO_WHEEL_BLINK_PERIOD_SECONDS / 2.0)) % 2 == 0
