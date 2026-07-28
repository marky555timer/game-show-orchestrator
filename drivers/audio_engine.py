import os
import math
import array
import pygame

# Initialize pygame mixer
pygame.mixer.init(frequency=44100, size=-16, channels=2)

reverb_enabled = False

def stop_previous_audio():
    pygame.mixer.fadeout(250)

def apply_reverb_to_sound(sound, delay_ms=45, decay=0.45, num_reflections=4):
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

# Load sound assets
raw_buzzer = load_sound("audio/buzzer.wav", fallback_freq=120)
raw_ding = load_sound("audio/dingSingle.wav", fallback_freq=880)
raw_bigwin = load_sound("audio/bigWin.wav", fallback_freq=523)
raw_clear = generate_low_clear_sound()

def play_processed_sound(sound_asset):
    if reverb_enabled:
        processed = apply_reverb_to_sound(sound_asset)
        return processed.play()
    return sound_asset.play()