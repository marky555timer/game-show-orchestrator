"""drivers/shutdown_button.py
Physical shutdown push button (2026-10-07): a momentary switch on config.SHUTDOWN_BUTTON_PIN
(GPIO16 / pin 36 since the 2026-10-28 swap; was GPIO26) and GND, internal pull-up, active
LOW. Held for SHUTDOWN_BUTTON_HOLD_SECONDS it:

  1. engages the admin blackout (drivers/blackout_engine.py) -- every light,
     the red matrix text and all sound go dark at once,
  2. after SHUTDOWN_BUTTON_DARK_SECONDS (so the dark frames actually reach
     the panels/marquee/DMX), requests the same graceful teardown + Pi
     poweroff as the web remote's "SHUT DOWN PI" (cache save, DMX blackout,
     drivers stopped, then `sudo shutdown -h now` from main.py).

The short hold keeps a wire bump or a noise spike on a long lead from
powering the show off. No-op off a Pi (no RPi.GPIO) and harmless with
nothing wired (the pull-up reads "not pressed"). poll() is called every
frame from inputs/gamepad.py::process_events(), next to recovery_button.
"""
import time

import config

try:
    import RPi.GPIO as GPIO
    _AVAILABLE = True
except (ImportError, RuntimeError):
    GPIO = None
    _AVAILABLE = False

_initialized = False
_down_since = 0.0
_fired_at = 0.0  # blackout engaged, poweroff pending
_armed_at = 0.0
_seen_released = False  # a press only counts after the pin has read HIGH once
_stuck_warned = False
_reason = "SHUTDOWN BUTTON (GPIO)"


def init():
    """Claims the pin -- call once from main.py at startup."""
    global _initialized
    if not _AVAILABLE or _initialized or not config.SHUTDOWN_BUTTON_ENABLED:
        return
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(config.SHUTDOWN_BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    global _armed_at
    _initialized = True
    _armed_at = time.monotonic()
    print(f"[SHUTDOWN BTN] Armed on GPIO{config.SHUTDOWN_BUTTON_PIN} (physical pin {config.GPIO_PHYSICAL_PIN.get(config.SHUTDOWN_BUTTON_PIN, '?')}), "
          f"hold {config.SHUTDOWN_BUTTON_HOLD_SECONDS:.1f}s.")


def _fire(now):
    global _fired_at
    from drivers import blackout_engine
    _fired_at = now
    blackout_engine.engage()
    print("[SHUTDOWN BTN] Pressed -- blackout engaged, powering off the Pi.")


def request(reason):
    """Same blackout -> graceful poweroff as the GPIO26 hold, triggered from
    elsewhere (the green+blue panel-button hold). poll() finishes the hand-off."""
    global _reason
    if _fired_at or not _AVAILABLE or not _initialized:
        return
    _reason = reason
    _fire(time.monotonic())


def poll(now=None):
    global _down_since, _seen_released, _stuck_warned
    if not _AVAILABLE or not _initialized:
        return
    # Monotonic, not the caller's wall clock: the Pi's clock jumps (9 min on
    # 2026-10-08) when NTP syncs as WiFi comes up during the boot load, which
    # ended the startup window early and could turn a short LOW blip into an
    # instant "held" -- the likely cause of the ~25s false power-offs.
    now = time.monotonic()
    if _fired_at:
        # Blackout is out on the wire; now hand off to the graceful
        # teardown (main.py), which ends in `sudo shutdown -h now`.
        if now - _fired_at >= config.SHUTDOWN_BUTTON_DARK_SECONDS:
            from state import state
            if not state.shutdown_requested:
                state.shutdown_reason = _reason
                state.poweroff_after_exit = True
                state.shutdown_requested = True
        return
    down = GPIO.input(config.SHUTDOWN_BUTTON_PIN) == GPIO.LOW
    # Boot-loop guard: ignore the button for the first few seconds, and only
    # after it has been seen released -- a stuck / normally-closed / shorted
    # switch then just logs a warning instead of powering off every boot.
    if now - _armed_at < config.SHUTDOWN_BUTTON_STARTUP_IGNORE_SECONDS or not _seen_released:
        if not down and now - _armed_at >= config.SHUTDOWN_BUTTON_STARTUP_IGNORE_SECONDS:
            _seen_released = True
            print("[SHUTDOWN BTN] Pin reads released -- button live.")
        elif down and not _stuck_warned:
            # Logged once per LOW episode -- diagnoses the ~25s false trigger.
            _stuck_warned = True
            print(f"[SHUTDOWN BTN] GPIO{config.SHUTDOWN_BUTTON_PIN} reads PRESSED "
                  f"{now - _armed_at:.1f}s after start -- ignored (startup window).")
        elif not down and _stuck_warned:
            _stuck_warned = False
            print(f"[SHUTDOWN BTN] ...released again {now - _armed_at:.1f}s after start.")
        _down_since = 0.0
        return
    if not down:
        _down_since = 0.0
    elif not _down_since:
        _down_since = now
    elif now - _down_since >= config.SHUTDOWN_BUTTON_HOLD_SECONDS:
        _fire(now)


def cleanup():
    if _AVAILABLE and _initialized:
        GPIO.cleanup(config.SHUTDOWN_BUTTON_PIN)
