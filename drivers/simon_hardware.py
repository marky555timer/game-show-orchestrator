"""drivers/simon_hardware.py
Bring-up/test driver for the physical Simon arcade buttons + LEDs wired
directly to the Pi's 40-pin GPIO header (pinout in config.py's
SIMON_HW_BUTTON_PINS/SIMON_HW_LED_PINS, from the operator's own wiring
notes). This is NOT part of the actual Simon game loop -- drivers/
simon_engine.py still runs entirely off the joystick's simon_select_1..4
bindings (inputs/gamepad.py). This module only backs the "Simon Hardware
Test" section of the web remote's Advanced panel, so an operator can
prove each switch and LED is wired to the correct GPIO pin before
anything downstream ever depends on it, same bring-up role as the DMX
relay test buttons (web/remote_server.py's relay1/2/3-test).

No-op everywhere (with a one-line log at import) on anything that isn't
a Pi with RPi.GPIO installed -- same defensive shape as
drivers/power_monitor.py -- so this is always safe to import from the
Windows dev machine or a Pi that hasn't had the button harness wired up
yet.
"""
import threading

import config

try:
    import RPi.GPIO as GPIO
    _AVAILABLE = True
except (ImportError, RuntimeError):
    GPIO = None
    _AVAILABLE = False

_initialized = False
_led_off_timers = {}  # color -> pending threading.Timer

if not _AVAILABLE:
    print("[SIMON HW] RPi.GPIO not available on this host -- hardware test panel will report unavailable.")


def available():
    return _AVAILABLE


def init():
    """Claims the button/LED pins -- call once from main.py at startup.
    Safe to call on a host without RPi.GPIO (no-ops)."""
    global _initialized
    if not _AVAILABLE or _initialized:
        return
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    for pin in config.SIMON_HW_BUTTON_PINS.values():
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    for pin in config.SIMON_HW_LED_PINS.values():
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
    _initialized = True
    print("[SIMON HW] GPIO claimed for Simon hardware test panel "
          f"(buttons: {config.SIMON_HW_BUTTON_PINS}, LEDs: {config.SIMON_HW_LED_PINS}).")


def read_buttons():
    """color -> True (pressed) / False, or {} if unavailable/not yet
    init()'d. Buttons are wired to GND with the internal pull-up
    enabled, so a press reads LOW."""
    if not _AVAILABLE or not _initialized:
        return {}
    return {color: GPIO.input(pin) == GPIO.LOW
            for color, pin in config.SIMON_HW_BUTTON_PINS.items()}


def pulse_led(color, duration=None):
    """One-shot: turns `color`'s LED on immediately, then off again after
    `duration` seconds (config.SIMON_HW_LED_PULSE_SECONDS by default) --
    the "prove it lights up" bring-up test, same role as the DMX relay
    test buttons' pulse_channel(). A plain threading.Timer is enough here
    since this fires at most a few times a minute from an operator
    manually clicking a test button in the web remote, not from the main
    40fps render loop. Returns False if the color/hardware isn't
    available (caller reports that back to the web remote)."""
    if not _AVAILABLE or not _initialized:
        return False
    pin = config.SIMON_HW_LED_PINS.get(color)
    if pin is None:
        return False
    duration = config.SIMON_HW_LED_PULSE_SECONDS if duration is None else duration

    existing = _led_off_timers.get(color)
    if existing is not None:
        existing.cancel()

    GPIO.output(pin, GPIO.HIGH)
    timer = threading.Timer(duration, _turn_off_led, args=(color,))
    timer.daemon = True
    _led_off_timers[color] = timer
    timer.start()
    return True


def set_led(color, on):
    """Immediate, driven level set -- no auto-off timer, unlike pulse_led()'s
    one-shot "flash and auto-off". Used for the loss sequence's repeating
    correct-answer flash (drivers/simon_engine.py), which needs a caller
    (that module's own frame-by-frame update()) driving the on/off cycle
    directly rather than firing a series of independent one-shot pulses.
    Cancels any pending pulse_led() timer for this color first, so a
    leftover timer can't stomp a manually-driven level a moment later."""
    if not _AVAILABLE or not _initialized:
        return
    pin = config.SIMON_HW_LED_PINS.get(color)
    if pin is None:
        return
    existing = _led_off_timers.pop(color, None)
    if existing is not None:
        existing.cancel()
    GPIO.output(pin, GPIO.HIGH if on else GPIO.LOW)


def _turn_off_led(color):
    if not _AVAILABLE:
        return
    pin = config.SIMON_HW_LED_PINS.get(color)
    if pin is not None:
        GPIO.output(pin, GPIO.LOW)


def cleanup():
    """Called from main.py's shutdown teardown -- cancels any pending LED
    pulse timers and releases the pins."""
    if not _AVAILABLE or not _initialized:
        return
    for timer in _led_off_timers.values():
        timer.cancel()
    _led_off_timers.clear()
    GPIO.cleanup(list(config.SIMON_HW_BUTTON_PINS.values()) + list(config.SIMON_HW_LED_PINS.values()))
