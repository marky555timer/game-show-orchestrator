"""drivers/honk_engine.py
The duck-call blower (2026-10-18): a 12 V blower run at 5 V with a bird call
taped to its hose, on USB relay channel config.HONK_RELAY_CHANNEL (own fully
isolated supply; 3.5 A at start, 1.75 A running). Honks on every 2nd wrong
answer in a row (2nd, 4th, 6th...); a right answer resets the count, a timed-out
round (nobody answered) neither counts nor resets. Losing Simon on the real
buttons gets the SUPER BLAST (a long solid, then a random staccato tail).

A honk is a Morse-ish on/off pattern, made fresh at random each time
(_random_pattern, config.HONK_* ranges) and never the same dot/dash rhythm
twice in a row -- a repeated one sounds mechanical. The call only sounds once
the motor is above a threshold speed, so it always opens with a long "dash" to
spin up (full pitch takes ~0.33 s from power on) and only then does dots and
dashes -- the off gaps stay short enough (the honk dies ~0.77 s after power
off) that the motor never drops below the threshold. Dots on their own don't
spin it up and arc the contacts harder, so a pattern must start long.

Safety, because the relay board latches its last command (a crash mid-honk
would otherwise leave the motor running):
  * the pattern runs on its own thread, not the main loop, and always ends
    with OFF (finally);
  * any single ON is capped at HONK_MAX_ON_SECONDS and the whole pattern at
    HONK_MAX_TOTAL_SECONDS, whatever config says;
  * HONK_COOLDOWN_SECONDS between honks (occasional use, contact wear);
  * relay_engine switches every channel off when the board connects and when
    the app shuts down.
Muted (state.sfx_enabled off) or blacked out -> no honk.
"""
import random
import threading
import time

import config
from state import state
from drivers import relay_engine

_lock = threading.Lock()
_running = False
_last_honk_end = 0.0
_wrong_streak = 0
_last_shape = {False: "", True: ""}   # last dot/dash rhythm per kind (normal, super)


def _between(r):
    return random.uniform(r[0], r[1])


def _random_pattern(super_blast=False):
    """A fresh [(on, off), ...]: the spin-up (or the super blast's solid), then
    random dots/dashes with short gaps and the odd longer pause. Its dot/dash
    shape ("-.-..") differs from the last one of the same kind."""
    if super_blast:
        first, count = config.HONK_SUPER_SOLID_ON, config.HONK_SUPER_ELEMENTS
    else:
        first, count = config.HONK_SPINUP_ON, config.HONK_ELEMENTS
    for _ in range(20):
        n = random.randint(count[0], count[1])
        marks = ["-" if random.random() < config.HONK_DASH_SHARE else "." for _ in range(n)]
        if not super_blast and "-" not in marks and random.random() < 0.5:
            marks[random.randrange(n)] = "-"   # mostly some dash after the spin-up
        pauses = [random.random() < config.HONK_LETTER_GAP_SHARE for _ in range(n)]
        shape = "-" + "".join(m + (" " if p else "") for m, p in zip(marks, pauses))
        if shape != _last_shape[super_blast]:
            break
    _last_shape[super_blast] = shape
    out = [[_between(first), _between(config.HONK_GAP_OFF)]]
    for mark, pause in zip(marks, pauses):
        on = _between(config.HONK_DASH_ON if mark == "-" else config.HONK_DOT_ON)
        off = _between(config.HONK_LETTER_GAP_OFF if pause else config.HONK_GAP_OFF)
        out.append([on, off])
    out[-1][1] = 0.0
    return [tuple(p) for p in out], shape.rstrip()


def _safe_pattern(pattern, max_on=None, max_total=None):
    """A pattern clipped to its safety caps: [(on, off), ...]."""
    max_on = config.HONK_MAX_ON_SECONDS if max_on is None else max_on
    max_total = config.HONK_MAX_TOTAL_SECONDS if max_total is None else max_total
    out, total = [], 0.0
    for on, off in pattern:
        on = max(0.0, min(float(on), max_on))
        off = max(0.0, float(off))
        if total + on > max_total:
            break
        out.append((on, off))
        total += on + off
    return out


def _run(pattern):
    global _running, _last_honk_end
    ch = config.HONK_RELAY_CHANNEL
    try:
        for on, off in pattern:
            relay_engine.switch(ch, True)
            time.sleep(on)
            relay_engine.switch(ch, False)
            time.sleep(off)
    finally:
        relay_engine.switch(ch, False)
        with _lock:
            _running = False
            _last_honk_end = time.monotonic()


def honk(reason="", super_blast=False):
    """Fire one honk now (if allowed). super_blast: the Simon game-over blast
    (config.HONK_SUPER_*: long solid + staccatos, its own caps). Returns
    (ok, message)."""
    global _running
    if not config.HONK_ENABLED:
        return False, "honk is turned off (config.HONK_ENABLED)"
    if state.blackout_active or not state.sfx_enabled:
        return False, "muted"
    if not relay_engine.available():
        return False, "relay board not connected"
    with _lock:
        if _running:
            return False, "already honking"
        wait = config.HONK_COOLDOWN_SECONDS - (time.monotonic() - _last_honk_end)
        if _last_honk_end and wait > 0:
            return False, f"cooling down ({wait:.0f} s)"
        _running = True
    pattern, shape = _random_pattern(super_blast)
    if super_blast:
        pattern = _safe_pattern(pattern, config.HONK_SUPER_MAX_ON_SECONDS,
                                config.HONK_SUPER_MAX_TOTAL_SECONDS)
    else:
        pattern = _safe_pattern(pattern)
    print(f"[HONK] {'SUPER BLAST' if super_blast else 'Honk'} {shape}! {reason}".rstrip())
    threading.Thread(target=_run, args=(pattern,), daemon=True, name="honk").start()
    return True, "honk"


def note_wrong():
    """inputs/gamepad.py::trigger_loss() -- a real wrong answer (not a timeout)."""
    global _wrong_streak
    _wrong_streak += 1
    if _wrong_streak % 2 == 0:
        ok, msg = honk(f"({_wrong_streak} wrong in a row)")
        if not ok:
            print(f"[HONK] {_wrong_streak} wrong in a row, no honk: {msg}")


def simon_lost():
    """drivers/simon_engine.py -- a miss on the real buttons ends the game."""
    ok, msg = honk("(lost Simon)", super_blast=True)
    if not ok:
        print(f"[HONK] Lost Simon, no blast: {msg}")


def note_right():
    """inputs/gamepad.py::trigger_big_win()."""
    global _wrong_streak
    _wrong_streak = 0
