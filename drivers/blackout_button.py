"""drivers/blackout_button.py
Physical blackout push button (2026-10-28): a momentary switch on
config.BLACKOUT_BUTTON_PIN (GPIO26 / pin 37 since the 2026-10-28 swap) and GND, internal pull-up, active
LOW. Each press toggles the admin blackout (drivers/blackout_engine.py) --
the same thing as the web remote's BLACKOUT button: every light and sound
stops / resumes exactly where it was.

A press counts once the pin has read LOW for BLACKOUT_BUTTON_PRESS_SECONDS
(rejects noise spikes on a long lead), and the button must be released
before it can toggle again, so holding it down doesn't flicker. Ignored for
the first few seconds after start and until the pin has read released once,
so a stuck / shorted switch can't black out the show at boot.

No-op off a Pi (no RPi.GPIO) and harmless with nothing wired. poll() runs
every frame from main.py, next to shutdown_button.poll().
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
_armed_at = 0.0
_seen_released = False
_down_since = 0.0
_latched = False      # this press already toggled; wait for release
_last_toggle = 0.0


def init():
    """Claims the pin -- call once from main.py at startup."""
    global _initialized, _armed_at
    if not _AVAILABLE or _initialized or not config.BLACKOUT_BUTTON_ENABLED:
        return
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(config.BLACKOUT_BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    _initialized = True
    _armed_at = time.monotonic()
    print(f"[BLACKOUT BTN] Armed on GPIO{config.BLACKOUT_BUTTON_PIN} (physical pin {config.GPIO_PHYSICAL_PIN.get(config.BLACKOUT_BUTTON_PIN, '?')}).")


def poll(now=None):
    global _seen_released, _down_since, _latched, _last_toggle
    if not _AVAILABLE or not _initialized:
        return
    now = time.monotonic()
    down = GPIO.input(config.BLACKOUT_BUTTON_PIN) == GPIO.LOW
    if not _seen_released:
        if not down and now - _armed_at >= config.BLACKOUT_BUTTON_STARTUP_IGNORE_SECONDS:
            _seen_released = True
            print("[BLACKOUT BTN] Pin reads released -- button live.")
        return
    if not down:
        _down_since = 0.0
        _latched = False
        return
    if _latched:
        return
    if not _down_since:
        _down_since = now
        return
    if (now - _down_since >= config.BLACKOUT_BUTTON_PRESS_SECONDS
            and now - _last_toggle >= config.BLACKOUT_BUTTON_COOLDOWN_SECONDS):
        _latched = True
        _last_toggle = now
        from drivers import blackout_engine
        active = blackout_engine.toggle()
        print(f"[BLACKOUT BTN] Pressed -- blackout {'ON' if active else 'OFF'}.")


def cleanup():
    if _AVAILABLE and _initialized:
        GPIO.cleanup(config.BLACKOUT_BUTTON_PIN)
