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
- graded answer (state.fixture_flash_mode/until): full green + nod on a
  win, full red + head shake on a loss, then fade back to the dim color.
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


def _set16(data, ch, pos):
    """Writes a 0-255.99 position as coarse (ch) + fine (ch+1)."""
    pos = max(0.0, min(255.999, pos))
    data[ch] = int(pos)
    data[ch + 1] = int((pos % 1.0) * 256)


def _room(center, amp):
    """Clamps a swing amplitude so center +/- amp stays inside 0-255 --
    keeps sway/nod/shake symmetric when a center sits near a range end."""
    return max(0.0, min(amp, center, 255.0 - center))


def _detect_reaction(now):
    global _react_mode, _react_start, _seen_flash_until
    until = state.fixture_flash_until
    if until != _seen_flash_until:
        _seen_flash_until = until
        if state.fixture_flash_mode in ("win", "loss") and until > now:
            _react_mode = state.fixture_flash_mode
            _react_start = now


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
    local = t % stop
    # two blinks per stop, after the head has snapped there
    blink_len = stop * 0.12
    for start in (stop * 0.4, stop * 0.7):
        if start <= local < start + blink_len:
            dimmer = low
    return pc + sign * _room(pc, config.DMX_HEAD_SCAN_UNITS), dimmer


def apply(data, now=None):
    """Fills the head's channels in `data` (the bytearray DMX universe)."""
    global _smooth, _last_color, _last_t, _react_mode
    if not config.DMX_HEAD_ENABLED:
        return
    if now is None:
        now = time.time()
    base = config.DMX_HEAD_BASE_CHANNEL
    _detect_reaction(now)

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

    pc, tc = config.DMX_HEAD_PAN_CENTER, config.DMX_HEAD_TILT_CENTER
    pan, tilt = pc, tc
    speed = config.DMX_HEAD_SWAY_SPEED
    behavior = _behavior()
    if behavior == "sway":
        period = max(0.2, state.dj_tempo_period)
        pan = pc + _room(pc, config.DMX_HEAD_SWAY_PAN_UNITS) * math.sin(
            2 * math.pi * now / (period * config.DMX_HEAD_SWAY_BEATS))
        tilt = tc + _room(tc, config.DMX_HEAD_SWAY_TILT_UNITS) * math.sin(
            2 * math.pi * now / (period * config.DMX_HEAD_SWAY_BEATS / 2))
    elif behavior == "scan":
        pan, dimmer = _scan(now, pc)
        speed = config.DMX_HEAD_REACT_SPEED  # fast = sharp snaps

    if _react_mode:
        t = now - _react_start
        total = config.DMX_HEAD_REACT_SECONDS + config.DMX_HEAD_FADE_SECONDS
        if t >= total:
            _react_mode = ""
        else:
            rr, gg, bb = (0, 255, 0) if _react_mode == "win" else (255, 0, 0)
            if t < config.DMX_HEAD_REACT_SECONDS:
                k = 1.0
                wave = math.sin(2 * math.pi * config.DMX_HEAD_REACT_CYCLES
                                * t / config.DMX_HEAD_REACT_SECONDS)
                # nod = win, shake = loss. Which physical axis reads as a
                # nod depends on how the head is mounted (SWAP flag).
                if _react_mode == "win":
                    amp, on_tilt = config.DMX_HEAD_NOD_UNITS, not config.DMX_HEAD_SWAP_NOD_SHAKE_AXES
                else:
                    amp, on_tilt = config.DMX_HEAD_SHAKE_UNITS, config.DMX_HEAD_SWAP_NOD_SHAKE_AXES
                if on_tilt:
                    tilt = tc + _room(tc, amp) * wave
                else:
                    pan = pc + _room(pc, amp) * wave
                speed = config.DMX_HEAD_REACT_SPEED
            else:
                k = 1.0 - (t - config.DMX_HEAD_REACT_SECONDS) / config.DMX_HEAD_FADE_SECONDS
            # Blend the reaction color/brightness back into the dim uplight look.
            idle_d = dimmer
            dimmer = int(idle_d + (255 - idle_d) * k)
            r = int(r + (rr - r) * k)
            g = int(g + (gg - g) * k)
            b = int(b + (bb - b) * k)
            w = int(w * (1.0 - k))

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
