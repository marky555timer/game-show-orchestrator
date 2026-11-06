"""drivers/green_light_engine.py
Self-flashing green light on USB relay channel config.GREEN_LIGHT_RELAY_CHANNEL
(2026-10-27). Like the red strobe on channel 3, the light makes its own flash
pattern whenever it has power, so the Pi only closes the contact and opens it.

It comes on when a question is graded and either:
  * every connected phone player answered correctly, or
  * the answer picked on the arcade panel (or joystick) was correct.
It stays on for as long as that question's graded result is on screen (which
varies: normal hold, last-question hold, a panel press skipping ahead...) and
goes off the moment the show moves on to the next question or back to DJ mode.

Safety, as with the strobe: runs on its own thread (a stalled main loop can't
strand it on), any single ON is capped at GREEN_LIGHT_MAX_ON_SECONDS, blackout
switches it off, and the wanted state is re-sent every second so a relay board
that reconnected (it switches every channel off when it connects) catches up.
"""
import threading
import time

import config
from state import state
from drivers import relay_engine

_graded_at = None   # state.quiz_graded_at of the round that lit it
_lit_at = 0.0       # monotonic
_on = False
_last_send = 0.0
_started = False


def on_correct_round(reason):
    """Called right after a round is graded with a correct result."""
    global _graded_at, _lit_at
    if not config.GREEN_LIGHT_ENABLED:
        return
    _lit_at = time.monotonic()
    _graded_at = state.quiz_graded_at
    print(f"[GREEN LIGHT] On ({reason}).")


def _wanted(now):
    global _graded_at
    if _graded_at is None:
        return False
    if (state.lights_out or now - _lit_at >= config.GREEN_LIGHT_MAX_ON_SECONDS
            or not state.quiz_locked or state.quiz_graded_at != _graded_at):
        _graded_at = None
        print("[GREEN LIGHT] Off.")
        return False
    return True


def _step(now):
    global _on, _last_send
    want = _wanted(now)
    if want != _on:
        _on = want
        relay_engine.switch(config.GREEN_LIGHT_RELAY_CHANNEL, _on)
        _last_send = now
    elif now - _last_send >= 1.0 and relay_engine.available():
        relay_engine.switch(config.GREEN_LIGHT_RELAY_CHANNEL, _on)
        _last_send = now


def _loop():
    while True:
        try:
            _step(time.monotonic())
        except Exception as e:
            print(f"[GREEN LIGHT] {e}")
        time.sleep(0.05)


def start():
    global _started
    if _started or not config.GREEN_LIGHT_ENABLED:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="green-light").start()
