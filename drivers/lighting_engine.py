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

    elif theme == 3:
        # Sparkle -- each fixture twinkles on its own phase offset.
        for i in range(_UPLIGHT_COUNT):
            phase = ((t + i * (period / _UPLIGHT_COUNT)) % period) / period
            level = (math.sin(phase * 2 * math.pi) + 1.0) / 2.0
            dimmer = int(20 + level * 235)
            values.append((dimmer, r, g, b))

    elif theme == 4:
        # Bidirectional converging chase -- two pulses start at the outer
        # fixtures and sweep toward the center, colliding, then restart.
        half = (_UPLIGHT_COUNT - 1) / 2.0
        phase = (t % period) / period
        pos = phase * half
        for i in range(_UPLIGHT_COUNT):
            dist_from_edge = min(i, _UPLIGHT_COUNT - 1 - i)
            dist = abs(dist_from_edge - pos)
            level = max(0.0, 1.0 - dist / 1.8)
            dimmer = int(25 + level * 230)
            values.append((dimmer, r, g, b))

    elif theme == 5:
        # Random twinkle strobe -- deterministic pseudo-random per-fixture
        # flicker (hashed from fixture index + a coarse time bucket) rather
        # than true randomness, so the pattern is reproducible frame to
        # frame instead of jittering.
        bucket = int(t / max(0.05, period / 6.0))
        for i in range(_UPLIGHT_COUNT):
            on = ((i * 2654435761 + bucket * 40503) >> 5) % 3 == 0
            dimmer = 235 if on else 15
            values.append((dimmer, r, g, b))

    elif theme == 6:
        # Wave gradient -- a smooth traveling sine brightness wave across
        # the fixtures (continuous, unlike the discrete chase in theme 1).
        for i in range(_UPLIGHT_COUNT):
            phase = ((t / period) + (i / _UPLIGHT_COUNT)) % 1.0
            level = (math.sin(phase * 2 * math.pi) + 1.0) / 2.0
            dimmer = int(30 + level * 225)
            values.append((dimmer, r, g, b))

    else:
        # Heartbeat pulse burst -- all fixtures snap into a quick double
        # pulse ("lub-dub"), then fade together before the next beat.
        phase = (t % period) / period
        if phase < 0.12:
            level = phase / 0.12
        elif phase < 0.24:
            level = 1.0 - (phase - 0.12) / 0.12
        elif phase < 0.34:
            level = (phase - 0.24) / 0.10
        elif phase < 0.5:
            level = 1.0 - (phase - 0.34) / 0.16
        else:
            level = 0.0
        dimmer = int(15 + level * 240)
        values = [(dimmer, r, g, b)] * _UPLIGHT_COUNT

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


def _render_price_strobe(t):
    """70s/80s Price Game intro, phase 1: rapid on/off strobe across all 10
    uplights (fixtures 2-11), ~10Hz full white/black alternation."""
    on = int(t * 10.0) % 2 == 0
    if on:
        dmx.set_all_uplights(255, 255, 255, 255)
    else:
        dmx.set_all_uplights(0, 0, 0, 0)


def _render_grade_flash(now):
    """Amplified game feedback (Section 5): the instant an answer is graded,
    ALL 10 uplight fixtures (2-11) snap to a brief solid green/red pulse
    (state.fixture_flash_mode/until, set by inputs/gamepad.py::
    trigger_big_win/trigger_loss), overriding the normal chase for that
    short window. Returns True while the pulse is live so the caller skips
    _render_game_chase for this frame; once fixture_flash_until passes, the
    chase resumes on its own -- Fixture 1's own win/loss lamp
    (_render_fixture1) is untouched either way."""
    if now >= state.fixture_flash_until:
        return False
    if state.fixture_flash_mode == "win":
        dmx.set_all_uplights(255, 0, 255, 0)
        return True
    if state.fixture_flash_mode == "loss":
        dmx.set_all_uplights(255, 255, 0, 0)
        return True
    return False


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
    if state.price_game_active and state.price_game_phase == "strobe":
        # Price Game intro, phase 1 -- overrides whatever mode we're in.
        _render_price_strobe(now)
    elif state.mode == state.MODE_DJ and not state.price_game_active:
        # Reset rule: Fixture 1 is strictly black during DJ mode.
        state.fixture1_mode = "off"
        _render_dj_uplights(now)
    elif _render_grade_flash(now):
        pass  # brief ALL-fixture win/loss pulse overrides the chase this frame
    else:
        # GAME_MODE chase, and also the Price Game intro's banner/
        # question-wait phases -- "return to the standard mid-pace chase"
        # per the bonus-round spec (chase_pace_mode is reset to "mid" by
        # price_game_engine.start_price_game()).
        _render_game_chase(now, now)

    _render_fixture1(now)
    dmx.render()
