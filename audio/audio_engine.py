import os
import math
import array
import pygame

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

def load_sound(filepath, fallback_freq=440):
    """Loads a WAV file from disk or generates a fallback sine wave if file is missing."""
    if os.path.exists(filepath):
        try:
            return pygame.mixer.Sound(filepath)
        except Exception as e:
            print(f"[AUDIO ERROR] Failed to load {filepath}: {e}")
    
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
raw_clear = generate_low_clear_sound()
raw_coin = generate_coin_sound()
raw_low_beep = generate_low_beep_sound()

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