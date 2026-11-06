"""drivers/dmx_head.py
The ZU&RU 70W RGBW moving head -- the show's robotic "face".

Not part of the 16-channel fixture table; it lives at
config.DMX_HEAD_BASE_CHANNEL (14-channel mode) and is written by
apply() from EnttecDMXPro.render() just before each frame goes out, so it
tracks every existing lighting path (themes, flashes, blackouts) without
any of them knowing about it:

- color: copied from fixture 2 (an uplight), held through blackouts and
  smoothed; brightness stays between config.DMX_HEAD_MIN_BRIGHTNESS and
  DMX_HEAD_IDLE_BRIGHTNESS (5-10%), never strobing;
- idle music (live show, DJ mode): a gentle beat-locked pan/tilt sway;
- gameplay (live show, Game mode or Price Game): dead still, then every
  few seconds a sharp look right, a sharp look left, blinking twice at
  each stop like an eye, then back to center;
- setup/countdown/intro/outro/etc.: parked dead still at center;
- round start / winner: one full 360-degree spin (spin());
- graded answer (state.fixture_flash_mode/until): spin + rapid green
  blink, then solid green, on a right answer (nod if spins are off), full red + head shake on a loss, then fade back to the dim color.
"""
import math
import time

import config
from state import state

_UPLIGHT_REF_BASE = 1 + 1 * config.DMX_FIXTURE_CHANNELS  # fixture 2's dimmer channel

_react_mode = ""
_react_start = 0.0
_seen_flash_until = 0.0
# Smoothed idle look (dimmer, r, g, b, w) + last non-black color, so beat
# flashes/blackouts on fixture 2 don't make the head strobe.
_smooth = None
_last_color = (0, 0, 255, 0)
_last_t = 0.0
# Calibration hold (web /api/dmx/head-hold): raw pan/tilt until this time.
_hold_until = 0.0
_hold_pan = None
_hold_tilt = None
# 360-degree "look-around" spin: started on round start / winner / web
# button. Turns from the current front position (_pan_home) to the other one
# a full turn away, which becomes the new home (540-deg pan setting, see
# config.DMX_HEAD_PAN_TURN_UNITS) -- so spins alternate direction.
_spin_start = 0.0
_pan_home = config.DMX_HEAD_PAN_CENTER
_spin_from = _spin_to = _pan_home
_seen_mode = None
_seen_win = False


def spin(now=None):
    """Starts one full-turn spin (web /api/dmx/head-spin, and automatically
    on a game round starting or a winner sequence)."""
    global _spin_start, _spin_from, _spin_to
    now = time.time() if now is None else now
    if _spin_start and now - _spin_start < config.DMX_HEAD_SPIN_SECONDS:
        return   # one already turning
    turn = config.DMX_HEAD_PAN_TURN_UNITS
    _spin_from = _pan_home
    _spin_to = _pan_home + turn if _pan_home + turn <= 255.0 else _pan_home - turn
    _spin_start = now


def hold(pan=None, tilt=None, seconds=120.0):
    """Parks the head at raw pan/tilt (None = configured center) for
    `seconds`, overriding every behavior; seconds <= 0 releases."""
    global _hold_until, _hold_pan, _hold_tilt
    _hold_pan, _hold_tilt = pan, tilt
    _hold_until = time.time() + seconds if seconds > 0 else 0.0


def _set16(data, ch, pos):
    """Writes a 0-255.99 position as coarse (ch) + fine (ch+1)."""
    pos = max(0.0, min(255.999, pos))
    data[ch] = int(pos)
    data[ch + 1] = int((pos % 1.0) * 256)


def _room(center, amp):
    """Clamps a swing amplitude so center +/- amp stays inside 0-255 --
    keeps sway/nod/shake symmetric when a center sits near a range end."""
    return max(0.0, min(amp, center, 255.0 - center))


def _swing(center, amp, phase):
    """Pan or tilt at angle `phase` (radians) of a swing cycle. Symmetric
    (center + amp*sin) when there's room below; otherwise (center sits near
    0 -- the replacement unit's level is tilt 6 and its front is pan 0, the
    0/255 seam it can't cross without a full spin) it swings one-sided UP
    from center and back (1-cos, so it starts AT center -- no jump),
    keeping the full amplitude instead of clamping to the units below."""
    if center >= amp:
        return center + _room(center, amp) * math.sin(phase)
    return min(255.0, center + amp * (1.0 - math.cos(phase)) / 2.0)


def _detect_reaction(now):
    global _react_mode, _react_start, _seen_flash_until
    until = state.fixture_flash_until
    if until != _seen_flash_until:
        _seen_flash_until = until
        if state.fixture_flash_mode in ("win", "loss") and until > now:
            _react_mode = state.fixture_flash_mode
            _react_start = now
            if _react_mode == "win" and config.DMX_HEAD_SPIN_ENABLED:
                spin(now)  # right answer = spin with rapid green blink


def _detect_spin(now):
    """Round start (DJ -> Game) and winner sequence start trigger a spin."""
    global _seen_mode, _seen_win
    if not config.DMX_HEAD_SPIN_ENABLED:
        return
    if (_seen_mode is not None and _seen_mode != state.MODE_GAME
            and state.mode == state.MODE_GAME and state.show_phase == "live"):
        spin(now)
    if state.win_sequence_active and not _seen_win:
        spin(now)
    _seen_mode = state.mode
    _seen_win = state.win_sequence_active


def _behavior():
    """sway = idle music, scan = gameplay, still = setup/countdown/intro/outro/etc."""
    if state.show_phase != "live" or state.westminster_active:
        return "still"
    if state.mode == state.MODE_GAME or state.price_game_active:
        return "scan"
    return "sway"


def _scan(now, pc):
    """Returns (pan, dimmer) for the gameplay look-around. Brightness sits
    at the 10% ceiling and dips to the 5% floor for each blink."""
    dimmer = config.DMX_HEAD_IDLE_BRIGHTNESS * 255
    low = math.ceil(config.DMX_HEAD_MIN_BRIGHTNESS * 255)
    t = now % config.DMX_HEAD_SCAN_PERIOD_SECONDS
    stop = config.DMX_HEAD_SCAN_STOP_SECONDS
    if t >= 2 * stop:
        return pc, dimmer
    sign = 1 if t < stop else -1
    # No room on the low side (front = pan 0 seam): look right, then back
    # at the audience (blinking there), instead of right-then-left.
    one_sided = pc < config.DMX_HEAD_SCAN_UNITS
    local = t % stop
    # two blinks per stop, after the head has snapped there
    blink_len = stop * 0.12
    for start in (stop * 0.4, stop * 0.7):
        if start <= local < start + blink_len:
            dimmer = low
    if one_sided:
        return (pc + config.DMX_HEAD_SCAN_UNITS if sign > 0 else pc), dimmer
    return pc + sign * _room(pc, config.DMX_HEAD_SCAN_UNITS), dimmer


def apply(data, now=None):
    """Fills the head's channels in `data` (the bytearray DMX universe)."""
    global _smooth, _last_color, _last_t, _react_mode, _pan_home
    if not config.DMX_HEAD_ENABLED:
        return
    if now is None:
        now = time.time()
    base = config.DMX_HEAD_BASE_CHANNEL
    _detect_reaction(now)
    _detect_spin(now)

    src = _UPLIGHT_REF_BASE
    dimmer, r, g, b, w = (data[src], data[src + 1], data[src + 2],
                          data[src + 3], data[src + 4])
    if (r, g, b, w) != (0, 0, 0, 0):
        _last_color = (r, g, b, w)
    r, g, b, w = _last_color
    target = (max(math.ceil(config.DMX_HEAD_MIN_BRIGHTNESS * 255),
                  dimmer * config.DMX_HEAD_IDLE_BRIGHTNESS),
              r, g, b, w)
    dt = max(0.0, min(0.5, now - _last_t)) if _last_t else 0.5
    _last_t = now
    a = 1.0 - math.exp(-dt / config.DMX_HEAD_SMOOTH_SECONDS)
    if _smooth is None:
        _smooth = target
    else:
        _smooth = tuple(c + (t - c) * a for c, t in zip(_smooth, target))
    dimmer, r, g, b, w = (int(round(v)) for v in _smooth)

    spin_t = now - _spin_start
    if _spin_start and spin_t >= config.DMX_HEAD_SPIN_SECONDS:
        _pan_home = _spin_to   # same facing, a full turn along
    pc, tc = _pan_home, config.DMX_HEAD_TILT_CENTER
    pan, tilt = pc, tc
    speed = config.DMX_HEAD_SWAY_SPEED
    behavior = _behavior()
    if behavior == "sway":
        period = max(0.2, state.dj_tempo_period)
        pan = _swing(pc, config.DMX_HEAD_SWAY_PAN_UNITS,
                     2 * math.pi * now / (period * config.DMX_HEAD_SWAY_BEATS))
        tilt = _swing(tc, config.DMX_HEAD_SWAY_TILT_UNITS,
                           2 * math.pi * now / (period * config.DMX_HEAD_SWAY_BEATS / 2))
    elif behavior == "scan":
        pan, dimmer = _scan(now, pc)
        speed = config.DMX_HEAD_REACT_SPEED  # fast = sharp snaps

    if _react_mode:
        t = now - _react_start
        # Right answer (with spin on): rapid green blink for the spin, then
        # solid green, then fade. Wrong answer: red shake for REACT_SECONDS.
        win_spin = _react_mode == "win" and config.DMX_HEAD_SPIN_ENABLED
        spin_len = config.DMX_HEAD_SPIN_SECONDS
        react_len = (spin_len + config.DMX_HEAD_WIN_SOLID_SECONDS if win_spin
                     else config.DMX_HEAD_REACT_SECONDS)
        total = react_len + config.DMX_HEAD_FADE_SECONDS
        if t >= total:
            _react_mode = ""
        else:
            rr, gg, bb = (0, 255, 0) if _react_mode == "win" else (255, 0, 0)
            blink_off = False
            if win_spin and t < react_len:
                k = 1.0
                blink_off = (t < spin_len and
                             int(t * config.DMX_HEAD_WIN_BLINK_HZ * 2) % 2 == 1)
            elif t < react_len:
                k = 1.0
                phase = (2 * math.pi * config.DMX_HEAD_REACT_CYCLES
                         * t / config.DMX_HEAD_REACT_SECONDS)
                # nod = win, shake = loss. Which physical axis reads as a
                # nod depends on how the head is mounted (SWAP flag).
                if _react_mode == "win":
                    amp, on_tilt = config.DMX_HEAD_NOD_UNITS, not config.DMX_HEAD_SWAP_NOD_SHAKE_AXES
                else:
                    amp, on_tilt = config.DMX_HEAD_SHAKE_UNITS, config.DMX_HEAD_SWAP_NOD_SHAKE_AXES
                if on_tilt:
                    tilt = _swing(tc, amp, phase)
                else:
                    pan = _swing(pc, amp, phase)
                speed = config.DMX_HEAD_REACT_SPEED
            else:
                k = 1.0 - (t - react_len) / config.DMX_HEAD_FADE_SECONDS
            # Blend the reaction color/brightness back into the dim uplight look.
            idle_d = dimmer
            dimmer = int(idle_d + (255 - idle_d) * k)
            r = int(r + (rr - r) * k)
            g = int(g + (gg - g) * k)
            b = int(b + (bb - b) * k)
            w = int(w * (1.0 - k))
            if blink_off:
                dimmer = 0  # rapid green blink: dark between flashes

    spin_len = config.DMX_HEAD_SPIN_SECONDS
    if _spin_start and spin_t < spin_len + 0.6:
        # One full turn from home to the other front position, ease in/out,
        # then hold there briefly (it is the new home from then on).
        x = min(1.0, spin_t / spin_len)
        pan = _spin_from + (_spin_to - _spin_from) * (1.0 - math.cos(math.pi * x)) / 2.0
        tilt = tc
        speed = config.DMX_HEAD_REACT_SPEED

    if now < _hold_until:
        pan = pc if _hold_pan is None else _hold_pan
        tilt = tc if _hold_tilt is None else _hold_tilt
        speed = config.DMX_HEAD_SWAY_SPEED
        dimmer = max(dimmer, 60)  # bright enough to read the beam's direction

    _set16(data, base, pan)
    _set16(data, base + 2, tilt)
    data[base + 4] = int(speed)
    data[base + 5] = max(0, min(255, int(dimmer)))
    # Everything above the dimmer is zeroed first (strobe, macros, auto/sound,
    # reset -- reset must never see 250+), then color is written at the
    # offsets this unit actually uses (config.DMX_HEAD_RGBW_OFFSETS).
    for off in range(6, 14):
        data[base + off] = 0
    for off, val in zip(config.DMX_HEAD_RGBW_OFFSETS, (r, g, b, w)):
        data[base + off] = val
