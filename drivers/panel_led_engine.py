"""drivers/panel_led_engine.py
The four arcade-button LEDs outside of Simon/trivia (2026-10-09):

  - Press feedback: any panel button lights its own LED while held and for
    config.PANEL_LED_PRESS_LINGER_SECONDS after release, in every mode
    except Simon (simon_engine.py already lights its own pads) and blackout.
  - Music-play animation: during ordinary DJ-mode play the four LEDs run
    beat-synced patterns off the same tap tempo the DMX uplights use, moving
    on to the next pattern every config.PANEL_LED_PATTERN_BEATS beats.
    Turned off per song with state.panel_leds_fx (saved as an exception in
    light_prefs.csv's panel_leds_off column, like dmx_speed_div).

This module only computes what the LEDs should show; inputs/gamepad.py::
_sync_panel_leds() is still the single place that writes them (edge-
triggered), so nothing here touches GPIO directly.

The LEDs are plain on/off GPIO outputs, so every pattern is a sequence of
on/off steps -- no fades."""
import random
import time

import config
from state import state

_COLORS = tuple(config.SIMON_HW_COLOR_ORDER)  # green, red, yellow, blue
_N = len(_COLORS)

_held = set()        # colors physically down right now
_release_at = {}     # color -> time it was let go (linger window)


def note_press(color):
    _held.add(color)
    _release_at.pop(color, None)


def note_release(color, now):
    _held.discard(color)
    _release_at[color] = now


def pressed_mask(now):
    """Tuple of bools in _COLORS order: lit for a held button, or one let go
    less than PANEL_LED_PRESS_LINGER_SECONDS ago."""
    linger = config.PANEL_LED_PRESS_LINGER_SECONDS
    return tuple(c in _held or now - _release_at.get(c, -1e9) < linger for c in _COLORS)


def music_fx_wanted():
    """Ordinary music play: DJ mode, live show, nothing else using the
    buttons or taking over the lights. The medley is excluded (its game uses
    green/red as TRUE/FALSE, and lit buttons would suggest an answer)."""
    if not state.panel_leds_fx:
        return False
    if state.mode != state.MODE_DJ or state.show_phase != "live":
        return False
    if (state.trivia_confirm_active or state.medley_active or state.intermission_active
            or state.price_game_active or state.westminster_active or state.win_sequence_active
            or state.quiz_locked):
        return False
    return bool(state.now_playing_path)


# ------------------------------------------------------------
# Patterns. Each takes (beat, frac) -- the whole-beat count since the epoch
# and the 0..1 position inside the current beat -- and returns the tuple of
# lit LED indexes into _COLORS (green=0, red=1, yellow=2, blue=3).
# ------------------------------------------------------------
def _chase(beat, frac):
    return (beat % _N,)


def _ping_pong(beat, frac):
    seq = (0, 1, 2, 3, 2, 1)
    return (seq[beat % len(seq)],)


def _pairs(beat, frac):
    return (0, 2) if beat % 2 == 0 else (1, 3)


def _fill(beat, frac):
    # 1, 2, 3, then all 4 lit across each bar, flashing off on the last half-beat
    step = beat % _N
    if step == _N - 1 and frac >= 0.5:
        return ()
    return tuple(range(step + 1))


def _double_chase(beat, frac):
    return ((beat * 2 + (1 if frac >= 0.5 else 0)) % _N,)


def _inside_out(beat, frac):
    return (1, 2) if beat % 2 == 0 else (0, 3)


def _downbeat_flash(beat, frac):
    # all four punch on the bar's first beat, one chasing LED on the others
    if beat % 4 == 0:
        return tuple(range(_N)) if frac < 0.5 else ()
    return (beat % _N,)


def _sparkle(beat, frac):
    half = beat * 2 + (1 if frac >= 0.5 else 0)
    rng = random.Random(half)  # deterministic per half-beat: no flicker within one
    return tuple(rng.sample(range(_N), rng.choice((1, 1, 2))))


PATTERNS = (
    ("chase", _chase),
    ("ping-pong", _ping_pong),
    ("pairs", _pairs),
    ("fill", _fill),
    ("double chase", _double_chase),
    ("inside-out", _inside_out),
    ("downbeat flash", _downbeat_flash),
    ("sparkle", _sparkle),
)


def music_fx_mask(now):
    """Tuple of bools in _COLORS order for the current animation frame."""
    period = max(config.PANEL_LED_MIN_BEAT_SECONDS, state.dj_tempo_period or 0.5)
    beats = now / period
    beat = int(beats)
    frac = beats - beat
    _name, fn = PATTERNS[(beat // config.PANEL_LED_PATTERN_BEATS) % len(PATTERNS)]
    lit = fn(beat, frac)
    return tuple(i in lit for i in range(_N))


def current_pattern_name(now=None):
    """For status display / debugging."""
    now = time.time() if now is None else now
    period = max(config.PANEL_LED_MIN_BEAT_SECONDS, state.dj_tempo_period or 0.5)
    beat = int(now / period)
    return PATTERNS[(beat // config.PANEL_LED_PATTERN_BEATS) % len(PATTERNS)][0]
