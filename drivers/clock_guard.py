"""drivers/clock_guard.py
Wall-clock step guard (2026-10-26). The Pi has no RTC battery, so it boots
with the clock restored from its last shutdown (hours stale) and NTP steps
it forward once WiFi is up -- ~30s into the boot, often after a show has
started. Auto-DJ and the show runner time songs off time.time(), so a 15h
step made "On the Run" read as played-out one second in and skip.

Every frame (main.py) compares time.time() against time.monotonic(). If
their offset moves by more than STEP_THRESHOLD_S, the clock was stepped:
every wall-clock anchor the show runs on is shifted by the same amount --
the blackout engine's list (Auto-DJ track timer, deadlines, medley clock)
plus now_playing_started_at -- so nothing sees the jump. start.sh also
waits (bounded) for NTP sync before launching, so this is the backstop.
"""
import time

from state import state

STEP_THRESHOLD_S = 2.0
_offset = None

# "started at" anchors: after a shift these can never be in the future
_STARTED = ("auto_dj_track_started_at", "now_playing_started_at")


def update():
    global _offset
    off = time.time() - time.monotonic()
    if _offset is None:
        _offset = off
        return
    step = off - _offset
    _offset = off
    if abs(step) < STEP_THRESHOLD_S:
        return
    from drivers import blackout_engine
    blackout_engine._shift(step)
    if state.now_playing_started_at > 0:
        state.now_playing_started_at += step
    now = time.time()
    for name in _STARTED:  # anything stamped mid-frame after the step was already right
        if getattr(state, name, 0.0) > now:
            setattr(state, name, now)
    print(f"[CLOCK] Wall clock stepped {step:+.1f}s (NTP sync?) -- show timers shifted to match.")
