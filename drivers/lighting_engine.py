import math
import time
from config import (
    DJ_THEME_ALL_OFF_INDEX, DJ_COLOR_PALETTE,
    CHASE_PACE_MID_SECONDS, CHASE_PACE_FAST_SECONDS, CHASE_PACE_SLOW_SECONDS,
    DMX_NUM_FIXTURES,
)
from state import state
from drivers.dmx_driver import dmx

_UPLIGHT_COUNT = DMX_NUM_FIXTURES - 1  # fixtures 2..11


def _dj_theme_frame(t, color, period):
    """Returns a list of (dimmer, r, g, b) tuples for fixtures 2-11 -- one
    of 4 animated venue-uplighting themes oscillating to `period` (the
    tap-tempo interval)."""
    theme = state.dj_theme_index
    r, g, b = color
    values = []

    if theme == 0:
        # Unison breathing pulse.
        phase = (t % period) / period
        level = (math.sin(phase * 2 * math.pi) + 1.0) / 2.0
        dimmer = int(60 + level * 195)
        values = [(dimmer, r, g, b)] * _UPLIGHT_COUNT

    elif theme == 1:
        # Chase sweep left-to-right across the 10 uplights.
        pos = ((t % period) / period) * _UPLIGHT_COUNT
        for i in range(_UPLIGHT_COUNT):
            dist = min(abs(i - pos), _UPLIGHT_COUNT - abs(i - pos))
            level = max(0.0, 1.0 - dist / 2.5)
            dimmer = int(30 + level * 225)
            values.append((dimmer, r, g, b))

    elif theme == 2:
        # Alternating even/odd fixtures on the beat.
        phase = int((t % (period * 2)) / period)
        for i in range(_UPLIGHT_COUNT):
            on = (i % 2) == phase
            values.append((220 if on else 40, r, g, b))

    else:
        # Sparkle -- each fixture twinkles on its own phase offset.
        for i in range(_UPLIGHT_COUNT):
            phase = ((t + i * (period / _UPLIGHT_COUNT)) % period) / period
            level = (math.sin(phase * 2 * math.pi) + 1.0) / 2.0
            dimmer = int(20 + level * 235)
            values.append((dimmer, r, g, b))

    return values


def _render_dj_uplights(t):
    if state.dj_theme_index == DJ_THEME_ALL_OFF_INDEX:
        dmx.set_all_uplights(0, 0, 0, 0)
        return
    color = DJ_COLOR_PALETTE[state.dj_color_index % len(DJ_COLOR_PALETTE)]
    frame = _dj_theme_frame(t, color, state.dj_tempo_period)
    for i, (dimmer, r, g, b) in enumerate(frame):
        dmx.set_uplight(i + 2, dimmer, r, g, b)


def _chase_pace_seconds(now):
    if state.chase_pace_until and now < state.chase_pace_until:
        return CHASE_PACE_FAST_SECONDS if state.chase_pace_mode == "fast" else CHASE_PACE_SLOW_SECONDS
    return CHASE_PACE_MID_SECONDS


def _render_game_chase(t, now):
    pace = _chase_pace_seconds(now)
    step = int(t / pace) % _UPLIGHT_COUNT
    for i in range(_UPLIGHT_COUNT):
        on = (i == step)
        dmx.set_uplight(i + 2, 255 if on else 15, 255, 255, 255)


def _render_fixture1(t):
    mode = state.fixture1_mode
    if mode == "win":
        elapsed = t - state.fixture1_mode_set_at
        cps = 2.0
        phase = (elapsed * cps) % 1.0
        saw = 1.0 - phase
        dimmer = int(40 + saw * 215)
        dmx.set_fixture1(dimmer, 0, 255, 0)
    elif mode == "loss":
        dmx.set_fixture1(255, 255, 0, 0)
    else:
        dmx.set_fixture1(0, 0, 0, 0)


def update(now):
    """Per-frame DMX renderer, called once per frame from main.py. Owns
    the entire 176-channel frame and calls dmx.render() itself, replacing
    the old event-driven closures that used to live in inputs/gamepad.py."""
    if state.mode == state.MODE_DJ:
        # Reset rule: Fixture 1 is strictly black during DJ mode.
        state.fixture1_mode = "off"
        _render_dj_uplights(now)
    else:
        _render_game_chase(now, now)

    _render_fixture1(now)
    dmx.render()
