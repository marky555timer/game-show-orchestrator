import os
import glob
import math
import array
import random
import threading
import pygame

import config
from state import state
from audio.dj_engine import RESERVED_CHANNEL_COUNT

# Initialize Pygame audio mixer. buffer=8192 (2048 -> 4096 -> 8192, 2026-08-08
# -- a consistent brief dropout kept landing exactly when a background track
# decode starts even after the first buffer bump, so doubling again; a
# bigger buffer gives SDL's output callback more already-mixed audio queued
# up to ride out a CPU-contention gap without an audible hole). Paired with
# audio/dj_engine.py's _lower_current_thread_priority(), which attacks the
# same problem from the OS scheduler side. This show has no live-timing
# requirement that needs low latency, so there's no real cost to a larger
# buffer.
pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=8192)

reverb_enabled = False

def stop_previous_audio():
    """Fades out whatever SFX is currently playing before a new one starts
    (buzzer/ding/coin/etc share the auto-assigned channel pool, so without
    this a rapid double-press could layer two copies of the same sound).

    Deliberately per-channel, not the blanket pygame.mixer.fadeout() this
    used to be (2026-08-09 fix): that call fades out ALL channels including
    the DJ engine's reserved deck1/deck2/sweeper/announcement channels
    (audio/dj_engine.py channels 0-3), which is exactly why the quiz "ding"
    was cutting the music out until the next track change -- fadeout()
    doesn't know or care that those channels are reserved, reservation only
    protects against being auto-*assigned*, not against a blanket call like
    this one. Skipping channels < RESERVED_CHANNEL_COUNT leaves the DJ
    engine's own volume/ramp state as the only thing that ever touches
    those channels."""
    for i in range(RESERVED_CHANNEL_COUNT, pygame.mixer.get_num_channels()):
        pygame.mixer.Channel(i).fadeout(250)

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

def load_sound_folder(dir_path, fallback_freq=440):
    """Preloads every .wav in dir_path as a Sound and returns the list, for
    features that play a random pick from a folder of variants (hour chime,
    applause). Falls back to a single-element list holding the standard
    synthesized fallback tone if the folder is missing or has no .wav files,
    so callers can always do random.choice(...) without a length check."""
    sounds = []
    if os.path.isdir(dir_path):
        for name in sorted(os.listdir(dir_path)):
            if name.lower().endswith(".wav"):
                path = os.path.join(dir_path, name)
                try:
                    sounds.append(pygame.mixer.Sound(path))
                    print(f"[AUDIO] Preloaded {path}")
                except Exception as e:
                    print(f"[AUDIO ERROR] Failed to load {path}: {e}")
    else:
        print(f"[AUDIO ERROR] {dir_path} not found on disk -- using fallback tone")

    if not sounds:
        sounds.append(load_sound(dir_path, fallback_freq=fallback_freq))
    return sounds

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

# Load sound assets. config.resource_path() resolves correctly in dev,
# a --onedir .exe build, and a --onefile build alike (see config.py).
raw_buzzer = load_sound(config.resource_path("audio", "sound_effects", "buzzer.wav"), fallback_freq=120)
raw_ding = load_sound(config.resource_path("audio", "sound_effects", "dingSingle.wav"), fallback_freq=880)
raw_bigwin = load_sound(config.resource_path("audio", "sound_effects", "bigWin.wav"), fallback_freq=523)
raw_coin = load_sound(config.resource_path("audio", "sound_effects", "mario_coin.wav"), fallback_sound_fn=generate_coin_sound)
raw_clear = generate_low_clear_sound()
raw_buzz_short = load_sound(config.resource_path("audio", "sound_effects", "buzzShort.wav"), fallback_sound_fn=generate_low_beep_sound)

# Westminster "Bat Clock" top-of-hour chime (drivers/westminster_engine.py).
# A folder of bell samples, not one fixed file -- pick_random_chime() below
# picks one each time the chime fires.
hour_chime_sounds = load_sound_folder(config.HOUR_CLOCK_BELL_DIR, fallback_freq=660)

def pick_random_chime():
    return random.choice(hour_chime_sounds)

# Win sequence applause (drivers/win_sequence_engine.py) -- same
# folder-of-variants/random-pick pattern as the hour chime above.
applause_sounds = load_sound_folder(config.APPLAUSE_DIR, fallback_freq=523)

def pick_random_applause():
    return random.choice(applause_sounds)

# Trivia Night show-flow bookends (drivers/show_engine.py) -- fixed single
# files, not a folder-of-variants pool like the two above.
show_start_sound = load_sound(config.SHOW_START_MUSIC_PATH, fallback_freq=440)
show_end_sound = load_sound(config.SHOW_END_MUSIC_PATH, fallback_freq=440)

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

_game_music_deck = []        # shuffled remaining track paths for the current cycle
_last_game_music_played = None


def _pick_game_music():
    """Shuffled-deck selection, not independent random.choice() -- every
    track in config.GAME_MUSIC_DIR plays exactly once before any of them
    repeat, then the deck reshuffles, same "deal through a shuffled deck
    of cards" pattern already used for announcements (drivers/
    deck_orchestrator.py::_pick_announcement()), for the same reason:
    plain random re-selection can resurface the same track well before
    every other one has had a turn on a small pool. Returns None if the
    folder is empty."""
    global _game_music_deck, _last_game_music_played
    paths = glob.glob(os.path.join(config.GAME_MUSIC_DIR, "*.wav"))
    if not paths:
        return None

    # Drop any stale entries left over from files removed/renamed since
    # the deck was last built.
    _game_music_deck = [p for p in _game_music_deck if p in paths]

    if not _game_music_deck:
        _game_music_deck = list(paths)
        random.shuffle(_game_music_deck)
        # Avoid dealing the track that just finished the previous cycle
        # right back out as the first card of the new one.
        if len(_game_music_deck) > 1 and _game_music_deck[-1] == _last_game_music_played:
            _game_music_deck[-1], _game_music_deck[0] = _game_music_deck[0], _game_music_deck[-1]

    choice = _game_music_deck.pop()
    _last_game_music_played = choice
    return choice


def play_random_game_music():
    """Price Game Mode entry (drivers/price_game_engine.py): deals the next
    card from the game-music deck (see _pick_game_music()) and plays it
    exactly once (loops=0) -- it does NOT loop, full stop. A missing/empty
    folder is logged and skipped rather than crashing the round. Idempotent
    -- ignored if the bed is already playing, so a duplicate entry call can
    never stack a second play on top.

    History: this went back and forth between loops=-1 and loops=0 more
    than once. loops=-1 kept producing an audibly looping/repeating bed in
    a live show, which reads as broken software, not a lighting/audio
    choice -- not acceptable regardless of how it's mitigated. Settled on
    loops=0 for good: if the round outlasts a single playthrough,
    drivers/price_game_engine.py's _update_price_game_audio() notices via
    is_game_music_playing() and restores the deck to normal volume right
    away instead of leaving it ducked to silence -- so the bed never loops,
    and the room is never left in true dead air either."""
    global _game_music_active, _game_music_kill_timer
    with _game_music_lock:
        if _game_music_active:
            print("[AUDIO] Price Game music already active -- ignoring duplicate start request.")
            return
        if _game_music_kill_timer is not None:
            _game_music_kill_timer.cancel()
            _game_music_kill_timer = None
            # Force a clean stop right now rather than trusting the previous
            # round's fadeout() to have already fully resolved on its own
            # (2026-08-09 fix -- this is what was causing the bed to come
            # back looping on the 2nd+ Price Game round: pygame.mixer.music
            # is a single stream, and load()+play() on top of one that's
            # still mid-fade-out doesn't reliably reset SDL's internal fade/
            # loop state, only ever showing up once there WAS a previous
            # round to inherit stale state from -- exactly the "2nd and
            # subsequent plays" pattern).
            try:
                pygame.mixer.music.stop()
                pygame.mixer.music.unload()  # see fade_out_game_music()'s _kill() for why
            except Exception:
                pass

        path = _pick_game_music()
        if path is None:
            print(f"[AUDIO ERROR] No .wav files found in {config.GAME_MUSIC_DIR} -- "
                  f"Price Game music skipped")
            return
        try:
            pygame.mixer.music.load(path)
            # 2026-08-12 fix: this bed plays on pygame.mixer.music, a
            # separate stream from the 4 reserved Channels audio/dj_engine.py
            # scales by the admin's master volume (state.music_volume) --
            # nothing here ever touched pygame.mixer.music's own volume, so
            # it always played at whatever it was last left at (effectively
            # full blast) no matter how far down the show's volume was
            # turned. Set explicitly at play time, then kept live-synced by
            # sync_game_music_volume() (called every frame from
            # drivers/price_game_engine.py::update() while the bed is up)
            # so a volume nudge mid-round is felt immediately, same as every
            # other channel in the show.
            pygame.mixer.music.set_volume(state.music_volume / 100.0)
            pygame.mixer.music.play(loops=0)
            _game_music_active = True
            print(f"[AUDIO] Price Game background music -> {path} (plays once, no loop, "
                  f"volume={state.music_volume}%)")
        except Exception as e:
            print(f"[AUDIO ERROR] Failed to play Price Game music {path}: {e}")


def sync_game_music_volume():
    """Re-applies state.music_volume to the game-music bed -- called every
    frame from drivers/price_game_engine.py::update() while the bed is
    active, so an admin volume nudge mid-round is heard immediately instead
    of only taking effect on the NEXT Price Game round. No-op (and safe to
    call unconditionally) whenever the bed isn't currently playing."""
    if not _game_music_active:
        return
    try:
        pygame.mixer.music.set_volume(state.music_volume / 100.0)
    except Exception:
        pass


def is_game_music_playing():
    """True while the Price Game background bed is still audibly playing.
    _game_music_active alone can't answer this now that the bed plays
    loops=0 (once, no repeat) -- that flag only tracks whether
    fade_out_game_music() has been explicitly called, not whether the
    single playthrough has simply finished on its own. drivers/
    price_game_engine.py polls this to notice a naturally-finished bed and
    restore the ducked deck early instead of leaving it silent for
    whatever's left of the round."""
    return _game_music_active and pygame.mixer.music.get_busy()


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
                # unload() (2026-08-12 reinforcement): stop() alone doesn't
                # always fully release pygame.mixer.music's single SDL
                # stream -- the documented root cause of the bed "repeating
                # itself" on the 2nd+ Price Game round this session is that
                # a subsequent load()+play() on top of a stream whose fade/
                # loop state hasn't been FULLY reset can revive stale
                # state. unload() explicitly frees the currently loaded
                # music, forcing a clean slate for the next load().
                pygame.mixer.music.unload()
            except Exception:
                pass

        timer = threading.Timer(max(0.0, fade_ms) / 1000.0 + 0.05, _kill)
        timer.daemon = True
        timer.start()
        _game_music_kill_timer = timer


# ------------------------------------------------------------
# AUTO-DJ: STATION ANNOUNCEMENT VOICE-OVERS -- MOVED
# ------------------------------------------------------------
# play_station_announcement()/stop_station_announcement()/reset_announcement_volume()
# retired 2026-08-07: superseded by audio/dj_engine.py's play_sweeper_and_announcement()
# (full sweeper + VO + deck-duck/swell choreography) and preview_announcement()/
# stop_announcement() (standalone preview + instant mute), called via
# drivers/deck_orchestrator.py. See drivers/auto_dj_engine.py.


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
    drivers/space_invaders_engine.py::exit_space_invaders): hard-stops just
    the arcade SFX (coin/tick/laser/explosion) with no fade -- unlike
    stop_previous_audio()'s 250ms fadeout, the exit spec calls for an
    instant cut. Deliberately NOT pygame.mixer.stop(): that stops every
    channel globally, including audio/dj_engine.py's reserved deck1/deck2
    Channel objects, which was silencing the currently playing track the
    instant Space Invaders was exited."""
    for sound in (raw_coin, si_tick_sound, si_laser_sound, si_explosion_sound):
        sound.stop()


def play_processed_sound(sound_asset, volume=1.0):
    """Plays audio asset through the active reverb DSP filter if enabled, at
    `volume` (0.0-1.0, a call site's own RELATIVE level -- e.g. Space
    Invaders balances its own effects against each other at 0.5-0.9) scaled
    by the admin's master volume (state.music_volume, 0-100).

    2026-08-12 fix: every game SFX (ding, bigwin, buzzer, coin chime, clear,
    applause, the Westminster chime...) plays via this one function, but
    NONE of them go through audio/dj_engine.py's 4 reserved Channels --
    Sound.play() below grabs whatever free channel pygame's own pool hands
    it, which set_master_volume() never touches. So every one of these
    played at its call site's fixed volume (usually a flat 1.0) no matter
    how far down the show's volume was turned, same root cause as the Price
    Game music bed (audio_engine.py::play_random_game_music/
    sync_game_music_volume). Scaling here, in the one shared chokepoint,
    fixes all of them at once instead of touching every call site.

    Wrapped in try/except so a mixer failure (e.g. exhausted channels) can
    never break game-state logic."""
    try:
        target = sound_asset
        if reverb_enabled:
            target = apply_reverb_to_sound(sound_asset)
        master = state.music_volume / 100.0
        final_volume = max(0.0, min(1.0, volume)) * master
        # Set on the CHANNEL returned by play(), not on the Sound before
        # playing it -- confirmed by direct test that pygame-ce hands back
        # a channel already reset to full volume regardless of whatever the
        # Sound's own set_volume() was called with beforehand, which is why
        # this scaling silently had no audible effect until moved here.
        channel = target.play()
        if channel is not None:
            channel.set_volume(final_volume)
        return channel
    except Exception as e:
        print(f"[AUDIO ERROR] Failed to play sound: {e}")
        return None