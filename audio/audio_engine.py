import os
import math
import array
import random
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
def play_random_game_music():
    """Price Game Mode entry (drivers/price_game_engine.py): randomly picks
    one of the 3 background tracks and loops it until fade_out_game_music()
    is called. A missing file is logged and skipped rather than crashing
    the round."""
    path = random.choice(config.GAME_MUSIC_PATHS)
    try:
        if not os.path.exists(path):
            print(f"[AUDIO ERROR] {path} not found on disk -- Price Game music skipped")
            return
        pygame.mixer.music.load(path)
        pygame.mixer.music.play(loops=-1)
        print(f"[AUDIO] Price Game background music -> {path}")
    except Exception as e:
        print(f"[AUDIO ERROR] Failed to play Price Game music {path}: {e}")


def fade_out_game_music(fade_ms=1000):
    """Smoothly fades out and stops the Price Game background bed."""
    try:
        pygame.mixer.music.fadeout(fade_ms)
    except Exception as e:
        print(f"[AUDIO ERROR] Failed to fade out Price Game music: {e}")


# ------------------------------------------------------------
# AUTO-DJ: STATION ANNOUNCEMENT VOICE-OVERS (audio/announcements/)
# ------------------------------------------------------------
_announcement_cache = {}    # filename -> Sound
_announcement_history = []  # recently-played filenames, oldest first

def play_station_announcement():
    """Auto-DJ voice-over transition (drivers/auto_dj_engine.py): randomly
    picks a .wav from config.ANNOUNCEMENTS_DIR that hasn't played in the
    last config.ANNOUNCEMENT_HISTORY_SIZE plays, and plays it on its own
    Sound channel -- independent of stop_previous_audio()'s SFX fadeout, so
    a quiz buzzer/ding elsewhere can never cut it off. Returns the clip's
    duration in seconds so the caller can schedule the overlapping track
    transition 2s before it ends -- 0.0 if no announcement could be played."""
    global _announcement_history
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
        sound.play()
        duration = sound.get_length()
        print(f"[ANNOUNCEMENT] Playing {choice!r} ({duration:.1f}s)")
        return duration
    except Exception as e:
        print(f"[ANNOUNCEMENT ERROR] Failed to play station announcement: {e}")
        return 0.0


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