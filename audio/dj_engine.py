"""Radio-style DJ playback engine: two music decks plus a sweeper and an
announcement channel, all mixed in real time by SDL_mixer (via pygame.mixer)
-- the same mixer audio_engine.py already uses for game SFX, so this reuses
proven infrastructure instead of hand-rolling a new audio backend.

Research note (2026-08-06, see chat): pygame.mixer/SDL_mixer natively supports
multiple simultaneous channels with independent per-channel volume, which is
exactly the primitive this engine needs (sweeper + VO + 2 decks at once).
Raspberry Pi forum reports of pop/crackle under multi-channel load trace to
two root causes, both addressed here: (1) too-small mixer buffers -- fixed by
audio_engine.py's buffer=2048 init -- and (2) sample-rate mismatches between
source files and the mixer's init rate. This engine assumes a 44100Hz mixer
(matching audio_engine.py's init); if the eventual music library has tracks
at other rates, they should be normalized at prep time, not on the fly.

No custom ring buffer or pre-decode plumbing needed: pygame.mixer.Sound(path)
already decodes the whole file into memory at construction time (verified:
pygame-ce 2.5.7 loads MP3s directly, no ffmpeg/pydub dependency), and SDL_mixer's
own C-level engine owns the real-time output callback. "Pre-decode ahead of
time" just means: construct the Sound object during the prior track's runway,
not at the moment you need to play it.

Deck/channel model: pygame.mixer.set_reserved(4) carves out channels 0-3 so
audio_engine.py's SFX (which uses pygame's auto-assigned channel pool for
sound.play()) can never collide with a DJ channel mid-crossfade.
"""
import glob
import os
import random
import struct
import subprocess
import sys
import threading
import time

import pygame

MIN_FADE_SECONDS = 0.1  # spec: fades are never a slam, always >= 0.1s
SWEEPER_OVERLAP_SECONDS = 0.5  # default: announcement starts ~0.5s into the sweeper
SWEEPER_DUCK_LEVEL = 0.25  # sweeper's own self-fade once the VO comes in
DECK_DUCK_LEVEL = 0.25  # incoming deck's bed level under the announcement
RAMP_TICK_SECONDS = 0.03  # ~33Hz volume update rate -- smooth without being wasteful

CHAN_DECK1 = 0
CHAN_DECK2 = 1
CHAN_SWEEPER = 2
CHAN_ANNOUNCEMENT = 3
# audio_engine.py's stop_previous_audio() reads this to know which low
# channels to leave alone -- pygame.mixer.set_reserved() only protects a
# channel from auto-assignment, it does NOT protect it from a blanket
# pygame.mixer.fadeout()/stop() call, which is exactly what was silencing
# the music every time the quiz "ding" played (2026-08-09).
RESERVED_CHANNEL_COUNT = 4


def _ensure_mixer():
    """Reuses audio_engine.py's mixer init if it already ran (normal case --
    audio_engine is imported first at app startup); falls back to initializing
    it here so this module also works stand-alone (e.g. the demo script).
    Kept in sync with audio_engine.py's buffer size -- see that file for why."""
    if pygame.mixer.get_init() is None:
        pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=8192)
    pygame.mixer.set_reserved(RESERVED_CHANNEL_COUNT)


def _lower_current_thread_priority():
    """Best-effort: drops the CALLING thread's OS scheduling priority so a
    background track decode competes less aggressively with the real-time
    audio callback/ramp-scheduler threads for CPU time (2026-08-08 -- the
    buffer-size bump alone didn't fully clear a dropout landing exactly when
    a decode thread starts, so this attacks the same problem from the OS
    scheduler side instead). Windows-only (ctypes call to SetThreadPriority);
    silently a no-op elsewhere, same "best effort, never block on it" pattern
    as graphics/matrix_canvas.py's always-on-top pin. Call this as the very
    first line inside a background decode thread's target function."""
    try:
        import ctypes
        THREAD_PRIORITY_BELOW_NORMAL = -1
        kernel32 = ctypes.windll.kernel32
        kernel32.SetThreadPriority(kernel32.GetCurrentThread(), THREAD_PRIORITY_BELOW_NORMAL)
    except Exception:
        pass


class DecodeProcess:
    """Manages a standalone decode-worker subprocess (decode_worker.py in
    this same folder) so track decoding happens in a genuinely separate OS
    process, not just a background thread -- 2026-08-08, the next lever
    after buffer size and thread priority both measurably helped without
    fully clearing a dropout landing right when a decode starts. A separate
    process cannot compete with the real-time audio callback/ramp-scheduler
    threads for CPU scheduling at all, which a thread in the same process
    always can to some degree regardless of priority hints.

    See decode_worker.py's own docstring for why this is subprocess.Popen
    running a standalone script, not multiprocessing.Process -- short
    version: this codebase runs real side effects at import time in several
    places by design, and Windows' multiprocessing spawn mode would replay
    all of that inside the child.

    Falls back to in-process decode (DJEngine.load, via load_out_of_process
    below) if the worker process ever fails to start or dies mid-session,
    rather than hanging -- a dead worker degrades, it doesn't break playback."""

    def __init__(self):
        self._lock = threading.Lock()
        self._process = None
        self._start()

    def _start(self):
        worker_path = os.path.join(os.path.dirname(__file__), "decode_worker.py")
        try:
            self._process = subprocess.Popen(
                [sys.executable, worker_path],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            print("[DECODE PROCESS] Worker process started.")
        except Exception as e:
            print(f"[DECODE PROCESS] Failed to start worker process -- decode will run "
                  f"in-process instead: {e}")
            self._process = None

    @staticmethod
    def _read_exact(stream, n):
        buf = b""
        while len(buf) < n:
            chunk = stream.read(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    def decode(self, path):
        """Blocking -- call from a background thread, never the main loop.
        Returns raw PCM bytes decoded entirely in the worker process, or
        None if the worker is unavailable/errors (caller falls back to
        in-process decode in that case, see load_out_of_process)."""
        if self._process is None or self._process.poll() is not None:
            return None
        with self._lock:
            try:
                path_bytes = path.encode("utf-8")
                self._process.stdin.write(struct.pack(">I", len(path_bytes)) + path_bytes)
                self._process.stdin.flush()

                status_byte = self._read_exact(self._process.stdout, 1)
                if status_byte is None:
                    return None
                length_bytes = self._read_exact(self._process.stdout, 4)
                if length_bytes is None:
                    return None
                (length,) = struct.unpack(">I", length_bytes)
                payload = self._read_exact(self._process.stdout, length)
                if payload is None:
                    return None
                if status_byte[0] != 0:
                    print(f"[DECODE PROCESS] Worker failed to decode {path}: "
                          f"{payload.decode('utf-8', 'replace')}")
                    return None
                return payload
            except Exception as e:
                print(f"[DECODE PROCESS] IPC error decoding {path}: {e}")
                return None

    def stop(self):
        if self._process is not None:
            try:
                self._process.terminate()
            except Exception:
                pass


def load_out_of_process(path, decode_process):
    """Decodes `path` in the separate worker process and reconstructs a
    Sound from the raw PCM it returns; transparently falls back to
    DJEngine.load(path) (in-process) if the worker is unavailable or fails,
    so a dead worker degrades playback loading instead of breaking it."""
    raw = decode_process.decode(path)
    if raw is None:
        return DJEngine.load(path)
    return pygame.mixer.Sound(buffer=raw)


class _VolumeRamp:
    """One in-flight linear volume ramp on a single channel. Progress is
    computed from wall-clock time each tick rather than step-counted, so a
    slow tick (GC pause, CPU spike) still lands on the correct volume instead
    of drifting -- the ramp is self-correcting, not step-accumulating.

    Interpolates in LOGICAL volume (start_vol/end_vol, e.g. the choreography's
    0%/50%/100% duck levels) and multiplies by scale_fn() fresh every tick to
    get the actual value written to the channel -- not a fixed multiplier
    baked in at creation time. That matters for deck channels: it means a
    master-volume change mid-fade is reflected immediately, and it avoids the
    old bug where scaling by a *stale* pre-computed value at 0% master volume
    permanently lost track of what 100% would have been (0 * anything is
    still 0 -- see DJEngine.current_logical_volume for the actual fix)."""

    def __init__(self, channel, start_vol, end_vol, duration, scale_fn=None):
        self.channel = channel
        self.start_vol = start_vol
        self.end_vol = end_vol
        self.duration = max(duration, MIN_FADE_SECONDS)
        self.started_at = time.monotonic()
        self.scale_fn = scale_fn or (lambda: 1.0)
        self.current_logical = start_vol

    def apply(self):
        """Returns True while still in flight, False once complete (and sets
        the final exact volume on completion so float drift never leaves a
        channel a hair off its target)."""
        elapsed = time.monotonic() - self.started_at
        progress = min(1.0, elapsed / self.duration)
        self.current_logical = self.start_vol + (self.end_vol - self.start_vol) * progress
        actual = self.current_logical * self.scale_fn()
        self.channel.set_volume(max(0.0, min(1.0, actual)))
        return progress < 1.0


class DJEngine:
    """Owns the 4 reserved channels and the background thread that advances
    every in-flight volume ramp. One instance per process -- construct it
    once at app startup, same lifetime as the mixer itself."""

    def __init__(self):
        _ensure_mixer()
        self._channels = {
            "deck1": pygame.mixer.Channel(CHAN_DECK1),
            "deck2": pygame.mixer.Channel(CHAN_DECK2),
            "sweeper": pygame.mixer.Channel(CHAN_SWEEPER),
            "announcement": pygame.mixer.Channel(CHAN_ANNOUNCEMENT),
        }
        self._ramps = {}  # channel name -> _VolumeRamp
        self._ramps_lock = threading.Lock()
        self._running = True
        self._ramp_thread = threading.Thread(target=self._ramp_loop, daemon=True)
        self._ramp_thread.start()
        # Master volume (2026-08-08, extended to sweeper/announcement
        # 2026-08-09): a final multiplier applied to ALL FOUR channels --
        # the native replacement for the old MIDI CC#11/CC#12 "channel
        # fader" pair. Sweeper/announcement used to always start at a flat
        # 1.0 regardless of this, so turning the show's volume down did
        # nothing to how loud a transition's VO blasted in -- now every
        # channel this class plays respects the same master level.
        self._master_volume = 1.0
        # Each channel's own volume target as the crossfade/duck/sweeper
        # choreography sees it (0.0-1.0), BEFORE master volume is applied --
        # tracked separately from whatever's actually written to the channel
        # so set_master_volume() always has a real number to rescale from,
        # even if a channel is currently at 0% master (see _VolumeRamp
        # docstring for the bug this fixes).
        self._logical_volume = {"deck1": 1.0, "deck2": 1.0, "sweeper": 1.0, "announcement": 1.0}

    def _scale_for(self, channel_name):
        return self._master_volume if channel_name in self._logical_volume else 1.0

    def current_logical_volume(self, channel_name):
        with self._ramps_lock:
            ramp = self._ramps.get(channel_name)
            if ramp is not None:
                return ramp.current_logical
        return self._logical_volume.get(channel_name, 1.0)

    def set_master_volume(self, pct):
        """0-100. Rescales every channel's CURRENT audible volume
        immediately (not just future plays/ramps), so a manual nudge is
        felt right away instead of waiting for the next transition/sweeper."""
        self._master_volume = max(0.0, min(1.0, pct / 100.0))
        for name in self._logical_volume:
            logical = self.current_logical_volume(name)
            self._channels[name].set_volume(logical * self._master_volume)

    def _play_at_full(self, channel_name, sound, fade_ms):
        """Starts `sound` on `channel_name` at its own full logical volume
        (1.0), scaled by whatever the master volume currently is -- the
        sweeper/announcement equivalent of play_deck(volume=1.0). Replaces
        raw set_volume(1.0) + play() call sites so a turned-down show
        doesn't get a full-blast sweeper/VO on top of quiet music."""
        self._logical_volume[channel_name] = 1.0
        channel = self._channels[channel_name]
        channel.set_volume(1.0 * self._scale_for(channel_name))
        channel.play(sound, fade_ms=fade_ms)

    # ------------------------------------------------------------
    # Loading -- construction IS the decode. Call this during the prior
    # track's runway so play() is just handing an already-decoded buffer
    # to SDL_mixer, not decoding under time pressure.
    # ------------------------------------------------------------
    @staticmethod
    def load(path):
        return pygame.mixer.Sound(path)

    # ------------------------------------------------------------
    # Ramp scheduling
    # ------------------------------------------------------------
    def _ramp_loop(self):
        while self._running:
            with self._ramps_lock:
                done = [name for name, ramp in self._ramps.items() if not ramp.apply()]
                for name in done:
                    ramp = self._ramps.pop(name)
                    if name in self._logical_volume:
                        self._logical_volume[name] = ramp.end_vol
            time.sleep(RAMP_TICK_SECONDS)

    def ramp_volume(self, channel_name, target_vol, duration):
        """Smoothly ramps `channel_name` to `target_vol` (LOGICAL volume --
        master volume is applied on top automatically and continuously, not
        baked in at creation time) over `duration` seconds (floored to
        MIN_FADE_SECONDS -- see module docstring: fades are never a slam).
        Replaces any ramp already in flight on that channel, starting from
        its current logical volume so a re-target never pops."""
        channel = self._channels[channel_name]
        is_scaled = channel_name in self._logical_volume
        start_logical = self.current_logical_volume(channel_name) if is_scaled else channel.get_volume()
        scale_fn = (lambda: self._master_volume) if is_scaled else None
        with self._ramps_lock:
            self._ramps[channel_name] = _VolumeRamp(channel, start_logical, target_vol, duration, scale_fn)

    def stop(self):
        self._running = False

    def stop_decks(self):
        """Hard-stops both deck channels outright (drivers/show_engine.py's
        outro sequence, after the deck's fade-to-0 has finished) -- a fader
        at 0% still leaves the underlying Sound technically playing, just
        inaudible, which is fine for a normal duck/restore but not for a
        clean handoff into different music: this actually stops it, so
        nothing is left running underneath (or to pop back in if the
        fader's later restored for a new show) once ShowEnd.mp3 starts."""
        self._channels["deck1"].stop()
        self._channels["deck2"].stop()

    # ------------------------------------------------------------
    # Deck playback
    # ------------------------------------------------------------
    def play_deck(self, deck_name, sound, volume=1.0):
        channel = self._channels[deck_name]
        if deck_name in self._logical_volume:
            self._logical_volume[deck_name] = volume
        channel.set_volume(volume * self._scale_for(deck_name))
        # fade_ms, not a bare play() -- starting a PCM buffer abruptly at
        # non-zero volume clicks/pops if the first sample isn't near a zero
        # crossing, audio-engineering 101 and independent of any Python-side
        # timing. Same "never a slam" floor as every ramp in this module
        # (2026-08-07: this was the actual cause of the audible glitch on
        # "next" -- see play_sweeper_and_announcement below, which had the
        # same gap on the sweeper/announcement starts).
        channel.play(sound, fade_ms=int(MIN_FADE_SECONDS * 1000))

    def crossfade_decks(self, from_deck, to_deck, duration):
        """Plain radio-style crossfade, no sweeper/announcement -- the
        baseline transition when auto-DJ just needs to move to the next
        track with nothing to say."""
        self.ramp_volume(from_deck, 0.0, duration)
        self.ramp_volume(to_deck, 1.0, duration)

    # ------------------------------------------------------------
    # Sweeper -> announcement -> crossfade sequence
    # ------------------------------------------------------------
    def play_sweeper_and_announcement(
        self, sweeper_sound, announcement_sound, outgoing_deck, incoming_deck,
        overlap_seconds=SWEEPER_OVERLAP_SECONDS, crossfade_seconds=MIN_FADE_SECONDS,
    ):
        """Radio liner-and-sweeper sequence:

          T+0            sweeper starts at full volume
          T+overlap      sweeper self-fades to SWEEPER_DUCK_LEVEL (25%)
                          while the announcement starts; outgoing
                          deck crossfades to 0%, incoming deck fades in to
                          DECK_DUCK_LEVEL (ducked bed under the VO)
          T+overlap+75%  incoming deck fades from ducked level up to 100%,
          of announcement timed to land exactly as the announcement ends

        The sweeper is NOT synchronized to the rest of this timeline beyond
        its one self-fade -- if it's longer than the announcement it simply
        plays out naturally on top of everything else; the deck timings
        below are driven entirely by the announcement's own length, per
        spec ("if sweeper > announcement length, allow sweeper to naturally
        finish over top all, same behavior continues").

        `overlap_seconds` is clamped to the sweeper's actual length in case
        a short sweeper file is shorter than the configured overlap.
        """
        overlap = min(overlap_seconds, sweeper_sound.get_length())
        announcement_length = announcement_sound.get_length()

        self._play_at_full("sweeper", sweeper_sound, int(MIN_FADE_SECONDS * 1000))

        def _start_announcement():
            self.ramp_volume("sweeper", SWEEPER_DUCK_LEVEL, MIN_FADE_SECONDS)
            self._play_at_full("announcement", announcement_sound, int(MIN_FADE_SECONDS * 1000))
            self.ramp_volume(outgoing_deck, 0.0, crossfade_seconds)
            self.ramp_volume(incoming_deck, DECK_DUCK_LEVEL, crossfade_seconds)

            swell_delay = announcement_length * 0.75
            swell_duration = max(MIN_FADE_SECONDS, announcement_length - swell_delay)
            timer = threading.Timer(
                swell_delay, self.ramp_volume,
                args=(incoming_deck, 1.0, swell_duration),
            )
            timer.daemon = True
            timer.start()

        if overlap <= 0:
            _start_announcement()
        else:
            timer = threading.Timer(overlap, _start_announcement)
            timer.daemon = True
            timer.start()

    def play_sweeper_only(self, sweeper_sound, outgoing_deck, incoming_deck, crossfade_seconds):
        """Sweeper "whoosh" over a plain crossfade -- no announcement VO.
        Used for the Trivia Night show flow's first-song handoff
        (drivers/deck_orchestrator.py::trigger_sweeper_only_track_move()),
        where the sweeper's attention-grabbing transition is still wanted
        but a spoken announcement would step on the live host's own
        introduction. Deliberately simpler than play_sweeper_and_
        announcement() above -- that method's whole duck/crossfade/swell
        timeline is built around an announcement's length, which doesn't
        exist here; this is just a sweeper at full volume overlapping a
        normal crossfade, timed to the sweeper's own length instead."""
        self._play_at_full("sweeper", sweeper_sound, int(MIN_FADE_SECONDS * 1000))
        self.crossfade_decks(outgoing_deck, incoming_deck, crossfade_seconds)

    def preview_announcement(self, sweeper_sound, announcement_sound,
                              overlap_seconds=SWEEPER_OVERLAP_SECONDS):
        """Sweeper + announcement only, no deck involvement -- for previewing
        what an announcement sounds like on demand without touching whatever
        is actually playing on deck1/deck2. Replaces the old audio_engine.py
        play_station_announcement() "testing mode" preview (see
        drivers/auto_dj_engine.py::notify_manual_track_move, 2026-08-07)."""
        overlap = min(overlap_seconds, sweeper_sound.get_length())
        self._play_at_full("sweeper", sweeper_sound, int(MIN_FADE_SECONDS * 1000))

        def _start_announcement():
            self.ramp_volume("sweeper", SWEEPER_DUCK_LEVEL, MIN_FADE_SECONDS)
            self._play_at_full("announcement", announcement_sound, int(MIN_FADE_SECONDS * 1000))

        if overlap <= 0:
            _start_announcement()
        else:
            timer = threading.Timer(overlap, _start_announcement)
            timer.daemon = True
            timer.start()

    def stop_announcement(self):
        """Instant mute -- replaces audio_engine.py's stop_station_announcement()
        for the Auto-Voice-OFF hard cutoff (drivers/auto_dj_engine.py::
        toggle_auto_announce). Safe to call even if nothing is playing."""
        self._channels["announcement"].stop()
        self._channels["sweeper"].stop()


# ------------------------------------------------------------
# Demo / manual smoke test. Run directly (not imported) to actually hear the
# sequence against real library files -- this is the "confidence" check,
# not just the theory: `python audio/dj_engine.py` from the project root.
# ------------------------------------------------------------
if __name__ == "__main__":
    _ensure_mixer()
    engine = DJEngine()

    music_dir = os.path.join(os.path.dirname(__file__), "music")
    sweeper_dir = os.path.join(os.path.dirname(__file__), "sweepers")
    announcement_dir = os.path.join(os.path.dirname(__file__), "announcements")
    tracks = glob.glob(os.path.join(music_dir, "*.mp3"))
    sweepers = glob.glob(os.path.join(sweeper_dir, "*.wav"))
    # Prefer the converted .wav announcements (precise timing, no MP3 encoder-
    # padding) -- see 2026-08-07 chat. Falls back to .mp3 if none exist yet.
    announcements = glob.glob(os.path.join(announcement_dir, "*.wav")) or \
        glob.glob(os.path.join(announcement_dir, "*.mp3"))

    if len(tracks) < 2 or not sweepers or not announcements:
        print("Need >=2 files in audio/music/*.mp3, >=1 in audio/sweepers/*.wav, "
              "and >=1 in audio/announcements/ to run this demo.")
        raise SystemExit(1)

    random.shuffle(tracks)
    track1, track2 = tracks[0], tracks[1]
    sweeper_path = random.choice(sweepers)
    announcement_path = random.choice(announcements)

    print(f"Deck1: {os.path.basename(track1)}")
    print(f"Deck2: {os.path.basename(track2)}")
    print(f"Sweeper: {os.path.basename(sweeper_path)}")
    print(f"Announcement: {os.path.basename(announcement_path)}")

    sound1 = engine.load(track1)
    sound2 = engine.load(track2)
    sweeper_sound = engine.load(sweeper_path)
    announcement_sound = engine.load(announcement_path)

    engine.play_deck("deck1", sound1, volume=1.0)
    print("Deck1 playing. Waiting 8s before triggering the sweeper/announcement "
          "transition into Deck2...")
    time.sleep(8)

    engine.play_sweeper_and_announcement(
        sweeper_sound, announcement_sound,
        outgoing_deck="deck1", incoming_deck="deck2",
    )
    engine.play_deck("deck2", sound2, volume=0.0)

    print("Transition fired. Listening for 20s -- confirm: no pops/clicks, "
          "sweeper audible under the announcement, Deck2 ducked then swelling "
          "back to full by the announcement's end, Deck1 gone.")
    time.sleep(20)
    engine.stop()
