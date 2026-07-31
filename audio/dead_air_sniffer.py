import threading
import time

import numpy as np
import sounddevice as sd

import config

# ==========================================
# PIONEER DDJ-FLX4 USB AUDIO DEAD-AIR SNIFFER
# ==========================================
# Passive hardware failsafe for Auto-DJ (drivers/auto_dj_engine.py). Runs a
# daemon thread that repeatedly records short buffers straight off the
# FLX4's USB audio input and tracks how long the RMS level has stayed below
# config.AUTODJ_DEAD_AIR_RMS_THRESHOLD. auto_dj_engine polls is_dead_air()
# once per frame -- this module never triggers a track transition itself,
# it only reports what the hardware is doing.

_thread = None
_stop_event = threading.Event()
_lock = threading.Lock()

_silent_since = None
_dead_air = False


def _find_flx4_device_index():
    """Scans sounddevice.query_devices() for an input device whose name
    contains config.AUTODJ_DEAD_AIR_DEVICE_NAME_HINT. Returns None (rather
    than raising) if the FLX4 isn't plugged in / enumerated yet -- the
    sniffer thread just exits and the failsafe is silently unavailable
    rather than crashing the show."""
    try:
        devices = sd.query_devices()
    except Exception as e:
        print(f"[DEAD-AIR SNIFFER] Failed to query audio devices: {e}")
        return None

    hint = config.AUTODJ_DEAD_AIR_DEVICE_NAME_HINT.lower()
    for index, dev in enumerate(devices):
        if dev.get("max_input_channels", 0) > 0 and hint in dev.get("name", "").lower():
            print(f"[DEAD-AIR SNIFFER] Found FLX4 input device #{index}: {dev['name']!r}")
            return index

    print(f"[DEAD-AIR SNIFFER] No input device matching {config.AUTODJ_DEAD_AIR_DEVICE_NAME_HINT!r} "
          f"found -- FLX4 dead-air failsafe disabled.")
    return None


def _sample_rms(device_index):
    """Blocking-records one short stereo buffer from device_index and
    returns its RMS level. Never raises -- a capture failure returns None
    so a transient device hiccup can't be mistaken for silence."""
    frames = int(config.AUTODJ_DEAD_AIR_SAMPLE_RATE * config.AUTODJ_DEAD_AIR_SAMPLE_SECONDS)
    try:
        buf = sd.rec(frames, samplerate=config.AUTODJ_DEAD_AIR_SAMPLE_RATE, channels=2,
                      dtype="float32", device=device_index, blocking=True)
    except Exception as e:
        print(f"[DEAD-AIR SNIFFER] Audio sample failed: {e}")
        return None
    return float(np.sqrt(np.mean(np.square(buf))))


def _loop():
    global _silent_since, _dead_air

    device_index = _find_flx4_device_index()
    if device_index is None:
        return

    while not _stop_event.is_set():
        rms = _sample_rms(device_index)
        now = time.time()

        with _lock:
            if rms is None:
                _silent_since = None
                _dead_air = False
                continue

            if rms < config.AUTODJ_DEAD_AIR_RMS_THRESHOLD:
                if _silent_since is None:
                    _silent_since = now
                silent_for = now - _silent_since
            else:
                _silent_since = None
                silent_for = 0.0

            _dead_air = silent_for >= config.AUTODJ_DEAD_AIR_REQUIRED_SILENT_SECONDS


def start():
    """Starts the background FLX4 sniffer thread. Safe to call more than
    once -- a no-op if a thread is already running."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, name="flx4-dead-air-sniffer", daemon=True)
    _thread.start()


def stop():
    _stop_event.set()


def is_dead_air():
    """True once the FLX4 input has read below
    config.AUTODJ_DEAD_AIR_RMS_THRESHOLD continuously for
    config.AUTODJ_DEAD_AIR_REQUIRED_SILENT_SECONDS. Always False if the
    FLX4 was never found."""
    with _lock:
        return _dead_air


def acknowledge_dead_air():
    """Called by auto_dj_engine immediately after acting on a dead-air
    reading: resets the silence timer so the residual quiet during the
    just-armed crossfade can't immediately read as a second dead-air
    period."""
    global _silent_since, _dead_air
    with _lock:
        _silent_since = None
        _dead_air = False
