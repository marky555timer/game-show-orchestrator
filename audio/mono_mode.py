"""audio/mono_mode.py
Mono music option (2026-10-25): with one speaker, stereo separation made some
tracks sound wrong. When on, music is downmixed to (L+R)/2 and sent to both
channels as it is decoded -- DJ tracks (dj_engine.load_out_of_process) and
Dance Medley buffers (medley_engine). Sweepers, announcements and sound
effects are left alone. Applies from the next decode; the track already
playing keeps its stereo until it ends.

Persisted in audio_settings.json ({"mono": bool}); toggled from the admin
remote (web/remote_server.py /api/audio/mono).
"""
import json
import os
import threading

import numpy as np
import pygame

import config

_FILE = config.resource_path("audio_settings.json")
_lock = threading.Lock()
_mono = None  # None until first read


def enabled():
    global _mono
    if _mono is None:
        try:
            with open(_FILE, encoding="utf-8") as f:
                _mono = bool(json.load(f).get("mono", False))
        except (OSError, ValueError):
            _mono = False
    return _mono


def set_enabled(on):
    global _mono
    with _lock:
        _mono = bool(on)
        tmp = _FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"mono": _mono}, f)
            os.replace(tmp, _FILE)
        except OSError as e:
            print(f"[MONO] Could not save {_FILE}: {e}")
    print(f"[MONO] Music mono {'ON' if _mono else 'OFF'}.")
    return _mono


def apply_raw(raw):
    """Interleaved 16-bit stereo PCM bytes -> same bytes with L=R=(L+R)/2.
    Returns the input untouched when mono is off or the buffer isn't whole
    stereo frames."""
    if not enabled() or len(raw) % 4:
        return raw
    pcm = np.frombuffer(raw, dtype=np.int16).reshape(-1, 2)
    mid = ((pcm[:, 0].astype(np.int32) + pcm[:, 1]) >> 1).astype(np.int16)
    return np.repeat(mid[:, None], 2, axis=1).tobytes()


def apply_sound(sound):
    """pygame Sound -> downmixed Sound (or the same one when off / not the
    mixer's 16-bit stereo format)."""
    if not enabled():
        return sound
    init = pygame.mixer.get_init()
    if not init or init[1] != -16 or init[2] != 2:
        return sound
    return pygame.mixer.Sound(buffer=apply_raw(sound.get_raw()))
