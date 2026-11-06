"""drivers/strobe_engine.py
Red self-flashing vehicle strobe (12 V, 175 mA, 2026-10-19) on USB relay
channel config.STROBE_RELAY_CHANNEL. The light makes its own flash pattern
whenever it has power, so all the Pi does is switch it on and off -- in
blocks, because many of these lights step to their next pattern when power is
cycled quickly: every ON lasts at least STROBE_MIN_ON_SECONDS and every OFF at
least STROBE_MIN_OFF_SECONDS.

It's on while any of these want it:
  * the end of a live question (hurry!): STROBE_COUNTDOWN_FLASHES short
    pulses (2026-10-23), siren.wav on the USB card with each one. These skip
    the minimum ON/OFF blocks;
  * flash(): Simon lost on the real buttons (with the honk's super blast) and
    a phone round nobody got right ("No winners");
  * the network is red (drivers/net_health_engine.py, same condition as the
    panel 2 warning sign): STROBE_NET_ON_SECONDS out of every
    STROBE_NET_EVERY_SECONDS, not solid.

Safety, as with the honk: runs on its own thread (a stalled main loop can't
strand it on), any single ON is capped at STROBE_MAX_ON_SECONDS, blackout
switches it off, and the wanted state is re-sent every second so a relay board
that reconnected (it switches every channel off when it connects) catches up.
"""
import threading
import time

import config
from state import state
from drivers import live_round_engine, net_health_engine, relay_engine

_lock = threading.Lock()
_event_until = 0.0        # monotonic; flash() keeps it on until then
_on = False
_changed_at = 0.0         # monotonic, last physical on/off change
_last_send = 0.0
_started = False
_sirened = set()          # (deadline, pulse) already given its siren
_counting_down = [False]  # the current ON is a countdown pulse


def flash(seconds, reason=""):
    """Keep the strobe on for (at least) `seconds` from now."""
    global _event_until
    if not config.STROBE_ENABLED:
        return
    with _lock:
        _event_until = max(_event_until, time.monotonic() + seconds)
    print(f"[STROBE] Flash {seconds:.0f} s {reason}".rstrip())


def _countdown_pulse():
    """Index of the countdown pulse that should be lit right now, else None.
    Pulse k is on while the seconds left are in (end_k, end_k + ON], counting
    back from STROBE_COUNTDOWN_END_SECONDS."""
    if not live_round_engine.is_round_active() or not state.round_deadline_at:
        return None
    left = state.round_deadline_at - time.time()
    on, gap = config.STROBE_COUNTDOWN_ON_SECONDS, config.STROBE_COUNTDOWN_GAP_SECONDS
    for k in range(config.STROBE_COUNTDOWN_FLASHES):
        end = config.STROBE_COUNTDOWN_END_SECONDS + (config.STROBE_COUNTDOWN_FLASHES - 1 - k) * (on + gap)
        if end < left <= end + on:
            return k
    return None


def _siren(k):
    """siren.wav on the USB card, once per pulse (muted with the game SFX)."""
    key = (state.round_deadline_at, k)
    if key in _sirened or not state.sfx_enabled:
        return
    if len(_sirened) > 50:
        _sirened.clear()
    _sirened.add(key)
    print(f"[STROBE] Countdown pulse {k + 1}/{config.STROBE_COUNTDOWN_FLASHES} + siren")
    from drivers import sfx_engine
    sfx_engine.play_replacing(config.STROBE_COUNTDOWN_SOUND)


def _net_wants(now):
    return (net_health_engine.show_on_panels()
            and now % config.STROBE_NET_EVERY_SECONDS < config.STROBE_NET_ON_SECONDS)


def _wanted(now):
    if state.lights_out:
        return False
    with _lock:
        if now < _event_until:
            return True
    return _net_wants(now)


def _step(now):
    global _on, _changed_at, _last_send
    want = _wanted(now)
    held = now - _changed_at
    pulse = None if state.lights_out else _countdown_pulse()
    if pulse is not None:
        want = True                       # countdown pulses ignore the minimum blocks
        _siren(pulse)
    elif _on and _counting_down[0] and not want:
        pass                              # a pulse just ended: straight off
    elif _on and (held >= config.STROBE_MAX_ON_SECONDS or state.lights_out):
        want = False                      # caps and blackout beat the minimum ON
    elif _on and not want and held < config.STROBE_MIN_ON_SECONDS:
        want = True
    elif not _on and want and held < config.STROBE_MIN_OFF_SECONDS:
        want = False
    _counting_down[0] = pulse is not None
    if want != _on:
        _on, _changed_at = want, now
        relay_engine.switch(config.STROBE_RELAY_CHANNEL, _on)
        _last_send = now
    elif now - _last_send >= 1.0 and relay_engine.available():
        relay_engine.switch(config.STROBE_RELAY_CHANNEL, _on)
        _last_send = now


def _loop():
    global _changed_at
    _changed_at = time.monotonic() - config.STROBE_MIN_OFF_SECONDS
    while True:
        try:
            _step(time.monotonic())
        except Exception as e:
            print(f"[STROBE] {e}")
        time.sleep(0.05)


def start():
    global _started
    if _started or not config.STROBE_ENABLED:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="strobe").start()
