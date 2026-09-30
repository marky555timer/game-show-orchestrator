"""drivers/recovery_button.py
Physical manual-recovery button on GPIO4 (physical pin 7 -> GND). One press
resets the marquee ESP32's serial link (wled_engine.force_reconnect(), same
effect as a power cycle) and starts a Bluetooth gamepad reconnect
(bluetooth_engine.reconnect_paired_devices_async(), same call the Setup
screen's blue button uses). Replaces the old blind periodic marquee reset
-- see wled_engine._PROACTIVE_RECONNECT_INTERVAL_S.

No-op off a Pi (no RPi.GPIO) and harmless with nothing wired: the internal
pull-up holds the pin HIGH, which reads as "not pressed". poll() is called
every frame from inputs/gamepad.py::process_events().
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
_was_down = False
_down_since = 0.0
_last_fired = 0.0
_DEBOUNCE_S = 0.05


def init():
    """Claims the pin -- call once from main.py at startup."""
    global _initialized
    if not _AVAILABLE or _initialized or not config.RECOVERY_BUTTON_ENABLED:
        return
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(config.RECOVERY_BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    _initialized = True
    print(f"[RECOVERY] Button armed on GPIO{config.RECOVERY_BUTTON_PIN} (physical pin 7).")


def _fire(now):
    # Lazy imports: same import-order-cycle reasoning as simon_engine.py's.
    from drivers import wled_engine, bluetooth_engine
    from state import state
    reset = wled_engine.force_reconnect()
    state.gamepad_connect_feedback_text = "CONNECTING..."
    state.gamepad_connect_feedback_until = now + config.GAMEPAD_CONNECT_PENDING_MAX_SECONDS
    bluetooth_engine.reconnect_paired_devices_async()
    print(f"[RECOVERY] Button pressed -- marquee link {'reset' if reset else 'not connected (nothing to reset)'}, "
          "Bluetooth gamepad reconnect started.")


def poll(now=None):
    """Debounced, edge-triggered, with a cooldown so a bounce or a held
    button can't hammer the marquee's serial port."""
    global _was_down, _down_since, _last_fired
    if not _AVAILABLE or not _initialized:
        return
    if now is None:
        now = time.time()
    down = GPIO.input(config.RECOVERY_BUTTON_PIN) == GPIO.LOW
    if down and not _was_down:
        _down_since = now
    elif down and _was_down and _down_since:
        if (now - _down_since >= _DEBOUNCE_S
                and now - _last_fired >= config.RECOVERY_BUTTON_COOLDOWN_SECONDS):
            _last_fired = now
            _down_since = 0.0  # fire once per press
            _fire(now)
    elif not down:
        _down_since = 0.0
    _was_down = down


def cleanup():
    if _AVAILABLE and _initialized:
        GPIO.cleanup(config.RECOVERY_BUTTON_PIN)
