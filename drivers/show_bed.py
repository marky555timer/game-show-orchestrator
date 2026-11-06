"""drivers/show_bed.py
The show-open music bed (2026-10-17, docs/panel_show_maker_design.md):

  introTheme.wav  plays from the top; from config.SHOW_INTRO_LOOP_IN_SECONDS it
                  loops (loop in -> loop out) ducked to SHOW_INTRO_DUCK_LEVEL
                  (from SHOW_INTRO_DUCK_DELAY_SECONDS past loop-in) under the host, until the show moves on. Moving on swells it
                  back to full fast, then it fades out under what comes next.
  wheelTheme.wav  loops while a wheel spins, cut hard the moment it stops.
  decade stab     e.g. 80s-v1.wav / 80s-v2.wav ... in audio/gameMusic (root =
                  the decade: 50s 60s 70s 80s 90s 2000s 2010s 2020s); a random
                  variant, never the one played last for that decade.
                  LetsPlayStab.wav stands in when a decade has no stab.
  LetsPlayStab    the no-wheel bridge into the first song.

drivers/show_engine.py starts the next song config.SHOW_STAB_LEAD_SECONDS
before a stab ends. Everything plays on pygame's shared SFX channels, scaled by
state.music_volume, and is protected from audio_engine.stop_previous_audio().
update(now) is pumped every frame from show_engine.update().
"""
import glob
import os
import random
import time

import pygame

import config
from state import state
from audio import audio_engine

_intro_head = None   # 0 -> loop-in slice of introTheme.wav
_intro_loop = None   # loop-in -> loop-out slice
_intro_ch = None
_intro_started_at = 0.0
_intro_duck_armed = False
# volume ramp for the intro: level goes start -> end over [t0, t0 + dur]
_intro_ramp = (1.0, 1.0, 0.0, 0.0)
_intro_stop_at = 0.0

_wheel_sound = None
_wheel_ch = None
_stab_cache = {}
_last_stab = {}      # decade root -> path played last
_stab_ch = None


def _master():
    return max(0.0, min(1.0, state.music_volume / 100.0))


def _slice(sound, start_s, end_s):
    freq, fmt, chans = pygame.mixer.get_init()
    frame = chans * (abs(fmt) // 8)
    raw = sound.get_raw()
    a = int(start_s * freq) * frame
    b = len(raw) if end_s is None else min(len(raw), int(end_s * freq) * frame)
    return pygame.mixer.Sound(buffer=raw[a:b])


def _build_intro():
    global _intro_head, _intro_loop
    if _intro_head is not None:
        return
    full = audio_engine.show_intro_sound
    loop_in = config.SHOW_INTRO_LOOP_IN_SECONDS
    loop_out = min(config.SHOW_INTRO_LOOP_OUT_SECONDS, full.get_length())
    if loop_out - loop_in < 2.0:
        loop_in, loop_out = 0.0, full.get_length()   # file too short for the loop points
    _intro_head = _slice(full, 0.0, loop_in) if loop_in > 0 else None
    _intro_loop = _slice(full, loop_in, loop_out)
    audio_engine.protect_sound(_intro_head)
    audio_engine.protect_sound(_intro_loop)


def _load(path):
    snd = _stab_cache.get(path)
    if snd is None and os.path.isfile(path):
        try:
            snd = pygame.mixer.Sound(path)
            audio_engine.protect_sound(snd)
            _stab_cache[path] = snd
        except Exception as e:
            print(f"[SHOW BED] Could not load {path}: {e}")
    return snd


# ----------------------------------------------------------------- intro
def start_intro():
    global _intro_ch, _intro_started_at, _intro_duck_armed, _intro_ramp, _intro_stop_at
    _build_intro()
    stop_all()
    first = _intro_head or _intro_loop
    _intro_ch = first.play()
    if _intro_ch is None:
        print("[SHOW BED] No free channel for the intro theme.")
        return
    if _intro_head is not None:
        _intro_ch.queue(_intro_loop)
    _intro_ch.set_volume(_master())
    _intro_started_at = time.time()
    _intro_duck_armed = True
    _intro_ramp = (1.0, 1.0, 0.0, 0.0)
    _intro_stop_at = 0.0


def intro_elapsed():
    return time.time() - _intro_started_at if _intro_ch is not None else 0.0


def _ramp_to(level, seconds, now):
    global _intro_ramp
    _intro_ramp = (_intro_level(now), level, now, max(0.01, seconds))


def _intro_level(now):
    a, b, t0, dur = _intro_ramp
    if dur <= 0 or now >= t0 + dur:
        return b
    if now <= t0:
        return a
    return a + (b - a) * (now - t0) / dur


def release_intro(swell=True):
    """The show is moving on: swell the intro back to full fast (it may be
    ducked under the host), then fade it out. Returns the seconds until the
    next element should start (the swell)."""
    global _intro_duck_armed, _intro_stop_at, _intro_ramp
    if _intro_ch is None:
        return 0.0
    now = time.time()
    _intro_duck_armed = False
    swell_s = config.SHOW_INTRO_SWELL_SECONDS if swell else 0.0
    if swell_s:
        _ramp_to(1.0, swell_s, now)
    # after the swell: fade out under whatever starts next
    fade = config.SHOW_INTRO_RELEASE_FADE_SECONDS
    _intro_stop_at = now + swell_s + fade
    _pending_fade[0] = now + swell_s
    return swell_s


_pending_fade = [0.0]   # when the post-swell fade-out begins


# ---------------------------------------------------------------- wheel
def start_wheel_theme():
    global _wheel_sound, _wheel_ch
    if _wheel_sound is None:
        _wheel_sound = _load(config.SHOW_WHEEL_THEME_PATH)
    if _wheel_sound is None:
        return
    _wheel_ch = _wheel_sound.play(loops=-1)
    if _wheel_ch is not None:
        _wheel_ch.set_volume(_master())


def stop_wheel_theme():
    global _wheel_ch
    if _wheel_ch is not None:
        _wheel_ch.stop()   # hard cut on the stab
        _wheel_ch = None


def wheel_theme_seconds():
    global _wheel_sound
    if _wheel_sound is None:
        _wheel_sound = _load(config.SHOW_WHEEL_THEME_PATH)
    return _wheel_sound.get_length() if _wheel_sound is not None else 0.0


# ---------------------------------------------------------------- stabs
def stab_root(decade):
    """"1980s" -> "80s"; "2000s" stays "2000s"."""
    from drivers.show_library import decade_label
    return decade_label(decade)


def _stab_paths(root):
    pat = os.path.join(config.GAME_MUSIC_DIR, f"{glob.escape(root)}-v*.wav")
    paths = glob.glob(pat)
    single = os.path.join(config.GAME_MUSIC_DIR, f"{root}.wav")
    if os.path.isfile(single):
        paths.append(single)
    return sorted(paths)


def has_stab(decade):
    return bool(_stab_paths(stab_root(decade)))


def _play_one(path):
    global _stab_ch
    snd = _load(path)
    if snd is None:
        return 0.0
    _stab_ch = snd.play()
    if _stab_ch is not None:
        _stab_ch.set_volume(_master())
    return snd.get_length()


def play_decade_stab(decade):
    """Plays a stab for `decade` (falls back to LetsPlayStab). Returns its length."""
    root = stab_root(decade)
    paths = _stab_paths(root)
    if not paths:
        print(f"[SHOW BED] No stab for {root} -- playing LetsPlayStab instead.")
        return play_lets_play()
    choices = [p for p in paths if p != _last_stab.get(root)] or paths
    path = random.choice(choices)
    _last_stab[root] = path
    print(f"[SHOW BED] Decade stab {os.path.basename(path)}")
    return _play_one(path)


def play_lets_play():
    return _play_one(config.SHOW_LETS_PLAY_STAB_PATH)


# ------------------------------------------------------------- general
def stop_all(fade_ms=0):
    global _intro_ch, _stab_ch, _intro_stop_at
    stop_wheel_theme()
    for ch in (_intro_ch, _stab_ch):
        if ch is not None:
            if fade_ms:
                ch.fadeout(fade_ms)
            else:
                ch.stop()
    _intro_ch = None
    _stab_ch = None
    _intro_stop_at = 0.0
    _pending_fade[0] = 0.0


def update(now):
    global _intro_ch, _intro_duck_armed, _intro_stop_at
    global _stab_ch, _wheel_ch
    # A finished sound's channel goes back to the shared SFX pool -- let go of it.
    if _intro_ch is not None and _intro_ch.get_sound() not in (_intro_head, _intro_loop):
        _intro_ch = None
    if _stab_ch is not None and _stab_ch.get_sound() not in _stab_cache.values():
        _stab_ch = None
    if _wheel_ch is not None and _wheel_ch.get_sound() is not _wheel_sound:
        _wheel_ch = None
    if _intro_ch is not None:
        # keep the loop slice queued so it repeats without a gap
        if _intro_ch.get_busy() and _intro_ch.get_queue() is None and _intro_stop_at == 0.0:
            _intro_ch.queue(_intro_loop)
        if _intro_duck_armed and now - _intro_started_at >= (config.SHOW_INTRO_LOOP_IN_SECONDS
                                                              + config.SHOW_INTRO_DUCK_DELAY_SECONDS):
            _intro_duck_armed = False
            _ramp_to(config.SHOW_INTRO_DUCK_LEVEL, config.SHOW_INTRO_DUCK_RAMP_SECONDS, now)
        if _pending_fade[0] and now >= _pending_fade[0]:
            _ramp_to(0.0, config.SHOW_INTRO_RELEASE_FADE_SECONDS, now)
            _pending_fade[0] = 0.0
        _intro_ch.set_volume(_master() * _intro_level(now))
        if _intro_stop_at and now >= _intro_stop_at:
            _intro_ch.stop()
            _intro_ch = None
            _intro_stop_at = 0.0
    if _wheel_ch is not None:
        _wheel_ch.set_volume(_master())
    if _stab_ch is not None:
        _stab_ch.set_volume(_master())
