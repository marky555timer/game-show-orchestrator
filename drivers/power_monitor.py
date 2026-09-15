# drivers/power_monitor.py
"""Undervoltage/brownout logging (2026-08-17): polls `vcgencmd get_throttled`
on a slow timer and prints a timestamped line the instant the Pi's power
chip reports a brownout, so a live crash/reboot leaves a trace in
startup.log (pi_deploy/start.sh redirects stdout there, unbuffered)
instead of vanishing along with the process. No-op on anything that isn't
Linux with vcgencmd on PATH -- the Windows dev machine included -- so
poll() is always safe to call from main.py's loop regardless of host.

Every event line also captures whichever track was playing at that
moment (deck_orchestrator.get_now_playing()), so a brownout can be lined
up by eye against the "[AUTO-DJ] Tracking ..." lines already in the same
log to see whether a track change/seek was actually happening right
before it."""
import shutil
import subprocess
import sys
import time

import config
from drivers.deck_orchestrator import get_now_playing

_AVAILABLE = sys.platform.startswith("linux") and shutil.which("vcgencmd") is not None
_last_poll_at = 0.0
_undervoltage_now = False
_ever_flagged = False

if _AVAILABLE:
    print(f"[POWER] Monitoring vcgencmd get_throttled every "
          f"{config.POWER_MONITOR_POLL_SECONDS:.0f}s.")


def _read_throttled():
    """Raw int parsed from `vcgencmd get_throttled` (e.g. "throttled=0x50005"),
    or None if the call fails for any reason -- never raises."""
    try:
        out = subprocess.run(
            ["vcgencmd", "get_throttled"],
            capture_output=True, text=True, timeout=2.0,
        ).stdout.strip()
        return int(out.split("=", 1)[1], 16)
    except Exception:
        return None


_TEMP_ZONE_PATH = "/sys/class/thermal/thermal_zone0/temp"


def read_cpu_temp_c():
    """Current CPU temperature in Celsius, or None if unavailable (anything
    that isn't Linux, or the thermal zone file doesn't exist -- the Windows
    dev machine included, matching this module's own _AVAILABLE gate).
    Reads the kernel's sysfs thermal zone directly rather than shelling out
    to `vcgencmd measure_temp` -- this gets called from the render loop
    every frame a joystick CPU-temp overlay trigger (drivers/
    joystick_bindings.py) is held, so it needs to be cheap enough to poll
    at 40Hz without spawning a subprocess each time."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        with open(_TEMP_ZONE_PATH, "r") as f:
            millidegrees = int(f.read().strip())
        return millidegrees / 1000.0
    except Exception:
        return None


def poll(now):
    """Call once per frame from main.py's loop -- internally rate-limited to
    config.POWER_MONITOR_POLL_SECONDS so this never spawns the vcgencmd
    subprocess at the 40Hz frame rate."""
    global _last_poll_at, _undervoltage_now, _ever_flagged
    if not _AVAILABLE:
        return
    if now - _last_poll_at < config.POWER_MONITOR_POLL_SECONDS:
        return
    _last_poll_at = now

    bits = _read_throttled()
    if bits is None:
        return

    stamp = time.strftime("%H:%M:%S")
    currently_under = bool(bits & 0x1)
    if currently_under and not _undervoltage_now:
        title, artist = get_now_playing()
        print(f"[POWER] {stamp} UNDER-VOLTAGE DETECTED (throttled=0x{bits:x}) "
              f"-- now playing: {title!r} by {artist!r}")
    elif not currently_under and _undervoltage_now:
        print(f"[POWER] {stamp} Under-voltage cleared (throttled=0x{bits:x}).")
    _undervoltage_now = currently_under

    if bool(bits & 0x10000) and not _ever_flagged:
        _ever_flagged = True
        print(f"[POWER] {stamp} First under-voltage event this session (throttled=0x{bits:x}).")
