import os
import math
import array
import random
import threading
import pygame

import config

# Initialize Pygame audio mixer
pygame.mixer.init(frequency=44100, size=-16, channels=2)

reverb_enabled = False

def stop_previous_audio():
    pygame.mixer.fadeout(250)

def apply_reverb_to_sound(sound, delay_ms=45, decay=0.45, num_reflections=4):
    """Applies a custom delay/reflection DSP reverb filter to a Pygame Sound asset."""
    try:
        raw_bytes = sound.get_raw()
        samples = array.array('h', raw_bytes)
        sample_rate = 44100
        delay_samples = int(sample_rate * (delay_ms / 1000.0)) * 2
        tail_length = delay_samples * num_reflections
        output_samples = samples + array.array('h', [0] * tail_length)
        
        total_len = len(samples)
        for r in range(1, num_reflections + 1):
            offset = delay_samples * r
            mult = decay ** r
            for i in range(total_len):
                idx = i + offset
                if idx < len(output_samples):
                    val = output_samples[idx] + int(samples[i] * mult)
                    output_samples[idx] = max(-32767, min(32767, val))
                    
        return pygame.mixer.Sound(buffer=output_samples)
    except Exception as e:
        print(f"[REVERB ERROR] Could not apply DSP filter: {e}")
        return sound

def load_sound(filepath, fallback_freq=440, fallback_sound_fn=None):
    """Loads a WAV file from disk, explicitly logging success/failure.
    Falls back to `fallback_sound_fn()` if given (e.g. a synthesized
    stand-in), otherwise a plain sine-wave tone -- either way, a missing
    or corrupt asset can never crash startup or leave a call site with
    no Sound object to play()."""
    if os.path.exists(filepath):
        try:
            sound = pygame.mixer.Sound(filepath)
            print(f"[AUDIO] Preloaded {filepath}")
            return sound
        except Exception as e:
            print(f"[AUDIO ERROR] Failed to load {filepath}: {e}")
    else:
        print(f"[AUDIO ERROR] {filepath} not found on disk -- using fallback tone")

    if fallback_sound_fn is not None:
        return fallback_sound_fn()

    sample_rate = 44100
    n_samples = int(sample_rate * 0.4)
    buf = array.array('h', [0] * (n_samples * 2))
    for i in range(n_samples):
        t = float(i) / sample_rate
        val = int(32767.0 * math.sin(2.0 * math.pi * fallback_freq * t))
        fade = max(0.0, 1.0 - (i / n_samples))
        val = int(val * fade)
        buf[i * 2] = val
        buf[i * 2 + 1] = val
    return pygame.mixer.Sound(buf)

def generate_low_clear_sound():
    """Generates a low-frequency synth tone for clear/mode switches."""
    sample_rate = 44100
    n_samples = int(sample_rate * 0.30)
    buf = array.array('h', [0] * (n_samples * 2))
    for i in range(n_samples):
        t = float(i) / sample_rate
        val = int(25000.0 * math.sin(2.0 * math.pi * 90 * t))
        fade = max(0.0, 1.0 - (i / n_samples))
        val = int(val * fade)
        buf[i * 2] = val
        buf[i * 2 + 1] = val
    return pygame.mixer.Sound(buf)

def generate_coin_sound():
    """Generates a two-note 'coin' chime (quick low->high blip) for a
    successful quiz-question fetch -- no .wav asset needed."""
    sample_rate = 44100
    notes = [(988.0, 0.09), (1319.0, 0.16)]  # (freq, duration) -- classic coin interval
    buf = array.array('h')
    for freq, dur in notes:
        n_samples = int(sample_rate * dur)
        for i in range(n_samples):
            t = float(i) / sample_rate
            fade = max(0.0, 1.0 - (i / n_samples))
            val = int(28000.0 * math.sin(2.0 * math.pi * freq * t) * fade)
            buf.append(val)
            buf.append(val)
    return pygame.mixer.Sound(buffer=buf)

def generate_low_beep_sound():
    """Generates a low, flat 'no credits' beep -- distinct from the softer
    generate_low_clear_sound() tone (harsher, no fade-in warmth)."""
    sample_rate = 44100
    n_samples = int(sample_rate * 0.35)
    buf = array.array('h', [0] * (n_samples * 2))
    for i in range(n_samples):
        t = float(i) / sample_rate
        val = int(24000.0 * math.sin(2.0 * math.pi * 110 * t))
        fade = max(0.0, 1.0 - (i / n_samples) ** 2)
        val = int(val * fade)
        buf[i * 2] = val
        buf[i * 2 + 1] = val
    return pygame.mixer.Sound(buf)

# Load sound assets
raw_buzzer = load_sound("audio/buzzer.wav", fallback_freq=120)
raw_ding = load_sound("audio/dingSingle.wav", fallback_freq=880)
raw_bigwin = load_sound("audio/bigWin.wav", fallback_freq=523)
raw_coin = load_sound("audio/mario_coin.wav", fallback_sound_fn=generate_coin_sound)
raw_clear = generate_low_clear_sound()
raw_buzz_short = load_sound("audio/buzzShort.wav", fallback_sound_fn=generate_low_beep_sound)

# ------------------------------------------------------------
# PRICE GAME MODE: background music bed
# ------------------------------------------------------------
# Played via pygame.mixer.music (a single dedicated background stream)
# rather than a Sound/Channel like every other asset above -- that keeps it
# immune to stop_previous_audio()'s channel fadeout(250) and to any
# buzzer/ding/coin SFX stealing a channel, so a quiz-answer sound during the
# round can never cut the bed off early.
#
# Tween-loop bug fix: this bed used to be able to fade out, get its channel
# faders reset, then tween back down and repeat -- because nothing stopped
# a stray second start/fade call from reviving a stream that had already
# been faded. _game_music_active is now the single source of truth for
# "is the bed currently playing": play_random_game_music() is a no-op while
# it's already True, and fade_out_game_music() is a no-op once it's already
# False, so the start/stop pair can only ever fire once per round.
_game_music_active = False
_game_music_kill_timer = None
_game_music_lock = threading.Lock()


def play_random_game_music():
    """Price Game Mode entry (drivers/price_game_engine.py): randomly picks
    one of the 3 background tracks and loops it until fade_out_game_music()
    is called. A missing file is logged and skipped rather than crashing
    the round. Idempotent -- ignored if the bed is already playing, so a
    duplicate entry call can never stack a second loop on top."""
    global _game_music_active, _game_music_kill_timer
    with _game_music_lock:
        if _game_music_active:
            print("[AUDIO] Price Game music already active -- ignoring duplicate start request.")
            return
        if _game_music_kill_timer is not None:
            _game_music_kill_timer.cancel()
            _game_music_kill_timer = None

        path = random.choice(config.GAME_MUSIC_PATHS)
        try:
            if not os.path.exists(path):
                print(f"[AUDIO ERROR] {path} not found on disk -- Price Game music skipped")
                return
            pygame.mixer.music.load(path)
            pygame.mixer.music.play(loops=-1)
            _game_music_active = True
            print(f"[AUDIO] Price Game background music -> {path}")
        except Exception as e:
            print(f"[AUDIO ERROR] Failed to play Price Game music {path}: {e}")


def fade_out_game_music(fade_ms=1000):
    """Smoothly fades out and stops the Price Game background bed exactly
    once. A background timer explicitly kills the mixer.music stream
    fade_ms after this fires (rather than trusting pygame's own fadeout
    bookkeeping alone), guaranteeing the playback thread/stream is fully
    stopped and can't be left in a state where a stray re-trigger revives
    it into another duck/tween cycle."""
    global _game_music_active, _game_music_kill_timer
    with _game_music_lock:
        if not _game_music_active:
            return  # already faded/stopped -- nothing to unhook
        _game_music_active = False

        try:
            pygame.mixer.music.fadeout(fade_ms)
        except Exception as e:
            print(f"[AUDIO ERROR] Failed to fade out Price Game music: {e}")

        def _kill():
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass

        timer = threading.Timer(max(0.0, fade_ms) / 1000.0 + 0.05, _kill)
        timer.daemon = True
        timer.start()
        _game_music_kill_timer = timer


# ------------------------------------------------------------
# AUTO-DJ: STATION ANNOUNCEMENT VOICE-OVERS (audio/announcements/)
# ------------------------------------------------------------
_announcement_cache = {}    # filename -> Sound
_announcement_history = []  # recently-played filenames, oldest first
_announcement_channel = None  # Channel currently playing an announcement, or None

def play_station_announcement():
    """Auto-DJ voice-over transition (drivers/auto_dj_engine.py): randomly
    picks a .wav from config.ANNOUNCEMENTS_DIR that hasn't played in the
    last config.ANNOUNCEMENT_HISTORY_SIZE plays, and plays it on its own
    Sound channel -- independent of stop_previous_audio()'s SFX fadeout, so
    a quiz buzzer/ding elsewhere can never cut it off. Returns the clip's
    duration in seconds so the caller can schedule the overlapping track
    transition 2s before it ends -- 0.0 if no announcement could be played."""
    global _announcement_history, _announcement_channel
    try:
        if not os.path.isdir(config.ANNOUNCEMENTS_DIR):
            print(f"[ANNOUNCEMENT ERROR] {config.ANNOUNCEMENTS_DIR} not found on disk")
            return 0.0

        files = sorted(f for f in os.listdir(config.ANNOUNCEMENTS_DIR) if f.lower().endswith(".wav"))
        if not files:
            print(f"[ANNOUNCEMENT] No .wav files in {config.ANNOUNCEMENTS_DIR}")
            return 0.0

        candidates = [f for f in files if f not in _announcement_history] or files
        choice = random.choice(candidates)

        sound = _announcement_cache.get(choice)
        if sound is None:
            sound = pygame.mixer.Sound(os.path.join(config.ANNOUNCEMENTS_DIR, choice))
            _announcement_cache[choice] = sound

        _announcement_history.append(choice)
        del _announcement_history[:-config.ANNOUNCEMENT_HISTORY_SIZE]

        sound.set_volume(1.0)
        _announcement_channel = sound.play()
        duration = sound.get_length()
        print(f"[ANNOUNCEMENT] Playing {choice!r} ({duration:.1f}s)")
        return duration
    except Exception as e:
        print(f"[ANNOUNCEMENT ERROR] Failed to play station announcement: {e}")
        return 0.0


def stop_station_announcement():
    """Auto-Voice OFF instant mute (Gamepad Btn1,
    drivers/auto_dj_engine.py::toggle_auto_announce): immediately kills
    whatever announcement clip is currently playing, if any. Safe to call
    even if nothing is playing."""
    global _announcement_channel
    if _announcement_channel is not None:
        try:
            _announcement_channel.stop()
        except Exception as e:
            print(f"[ANNOUNCEMENT ERROR] Failed to stop announcement: {e}")
        _announcement_channel = None


def reset_announcement_volume():
    """Auto-Voice ON (Gamepad Btn1, drivers/auto_dj_engine.py::
    toggle_auto_announce): resets every cached announcement Sound back to
    full volume (1.0), guaranteeing a prior mute/duck can never leave the
    next playback muted or at zero volume."""
    for sound in _announcement_cache.values():
        try:
            sound.set_volume(1.0)
        except Exception as e:
            print(f"[ANNOUNCEMENT ERROR] Failed to reset announcement volume: {e}")


# ------------------------------------------------------------
# SPACE INVADERS MINI-GAME: SYNTHESIZED ARCADE SFX
# ------------------------------------------------------------
def generate_invader_tick_sound():
    """Short low blip for each Space Invaders formation step."""
    sample_rate = 44100
    n_samples = int(sample_rate * 0.06)
    buf = array.array('h', [0] * (n_samples * 2))
    for i in range(n_samples):
        t = float(i) / sample_rate
        val = int(20000.0 * math.sin(2.0 * math.pi * 100 * t))
        fade = max(0.0, 1.0 - (i / n_samples))
        val = int(val * fade)
        buf[i * 2] = val
        buf[i * 2 + 1] = val
    return pygame.mixer.Sound(buf)


def generate_laser_sound():
    """Quick descending-pitch 'pew' for the player cannon's fired shot."""
    sample_rate = 44100
    duration = 0.12
    n_samples = int(sample_rate * duration)
    buf = array.array('h', [0] * (n_samples * 2))
    start_freq, end_freq = 1400.0, 300.0
    for i in range(n_samples):
        t = float(i) / sample_rate
        frac = i / n_samples
        freq = start_freq + (end_freq - start_freq) * frac
        fade = max(0.0, 1.0 - frac)
        val = int(22000.0 * math.sin(2.0 * math.pi * freq * t) * fade)
        buf[i * 2] = val
        buf[i * 2 + 1] = val
    return pygame.mixer.Sound(buf)


def generate_explosion_sound():
    """Short filtered-noise burst for a destroyed invader."""
    sample_rate = 44100
    n_samples = int(sample_rate * 0.18)
    buf = array.array('h', [0] * (n_samples * 2))
    for i in range(n_samples):
        fade = max(0.0, 1.0 - (i / n_samples))
        val = int(random.uniform(-1, 1) * 26000 * fade)
        buf[i * 2] = val
        buf[i * 2 + 1] = val
    return pygame.mixer.Sound(buf)


si_tick_sound = generate_invader_tick_sound()
si_laser_sound = generate_laser_sound()
si_explosion_sound = generate_explosion_sound()


def stop_all_arcade_audio():
    """Space Invaders IMMEDIATE exit (Btn7/Btn8,
    drivers/space_invaders_engine.py::exit_space_invaders): hard-stops every
    active Sound channel with no fade -- unlike stop_previous_audio()'s
    250ms fadeout, the exit spec calls for an instant cut. pygame.mixer.music
    (the Price Game bed) is a separate stream and is untouched."""
    pygame.mixer.stop()


def play_processed_sound(sound_asset, volume=1.0):
    """Plays audio asset through the active reverb DSP filter if enabled,
    at the given volume (0.0-1.0, always applied explicitly so no call site
    leaks its volume into the next). Wrapped in try/except so a mixer
    failure (e.g. exhausted channels) can never break game-state logic."""
    try:
        target = sound_asset
        if reverb_enabled:
            target = apply_reverb_to_sound(sound_asset)
        target.set_volume(max(0.0, min(1.0, volume)))
        return target.play()
    except Exception as e:
        print(f"[AUDIO ERROR] Failed to play sound: {e}")
        return None