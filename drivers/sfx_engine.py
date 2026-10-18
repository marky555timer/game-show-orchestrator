"""drivers/sfx_engine.py
Standalone sound-effects engine (2026-09-20) -- plays short .wav clips
through a SEPARATE physical amplifier from the DJ music path, via a
dedicated `aplay` subprocess targeting a specific ALSA card, rather than
pygame.mixer. pygame.mixer/SDL_mixer is a process-wide singleton bound to
one output device (the HiFiBerry DAC+ HAT that carries the actual DJ
music -- see audio/audio_engine.py, the existing SFX chokepoint for
buzzer/win/loss sounds etc.) -- there is no way to open a second,
independently-addressed output device through it in the same process, so
this intentionally never touches pygame.mixer at all. A previous attempt
at a second audio device in this project (drivers/dead_air_sniffer.py,
now removed) used the `sounddevice` library and was dropped specifically
because it conflicted with Rekordbox's exclusive grip on the DJ
controller's own ASIO driver -- going through a plain OS-level `aplay`
subprocess instead sidesteps that whole class of problem.

Targets config.SFX_ALSA_CARD_NAME ("Device", the ALSA id for the "USB
Audio Device" C-Media DAC -- addressed by name, not numeric card index,
since indices aren't stable across boots; see that constant's own comment
for the 2026-09-21 incident that proved it and for why this doesn't use
ALSA's `dmix` for software mixing) over plain `plughw` (exclusive
access).

No-op everywhere (with a one-line log) if `aplay` isn't on PATH, so this
stays safe to import from the Windows dev machine."""
import shutil
import subprocess
import threading
import time

import config
from state import state

_APLAY = shutil.which("aplay")
if _APLAY is None:
    print("[SFX] 'aplay' not found on PATH -- sound-effects engine will no-op "
          "(expected on the Windows dev machine).")

_DEVICE = f"plughw:CARD={config.SFX_ALSA_CARD_NAME},DEV=0"

# Tracks the in-flight aplay child (if any) so cleanup() can actually kill
# it. Confirmed live 2026-09-20: killing this app's own main.py (SIGTERM,
# same as every normal restart) does NOT kill an in-flight aplay child --
# subprocess.Popen children aren't tied to the parent's lifetime on Linux
# by default -- so a restart landing mid-playthrough left an orphaned
# aplay still holding the (exclusive, plughw) device for up to a full clip
# length, which made the OLD looping version of _startup_once() below
# (back when it retried repeatedly) spin through thousands of near-instant
# "Device or resource busy" failures a second for as long as the orphan
# persisted -- one run logged 13,345 "playthroughs" in under 5 minutes for
# a 60-second clip. _kill_stray_aplay() below is the other half of that
# original fix -- still needed even now that startup only ever plays once,
# since an orphan from a still-uncleaned prior crash could otherwise block
# this run's own single attempt.
_current_proc = None
_lock = threading.Lock()


def _kill_stray_aplay():
    """Best-effort cleanup of any aplay process left over from a previous
    run of this app that got killed mid-playthrough (see _current_proc's
    docstring) -- run once before playing the startup sound, as a second
    line of defense beyond cleanup() below for whatever killed the
    previous run without going through this app's own shutdown path
    (e.g. `kill -9`, a crash, a power cycle mid-clip)."""
    try:
        subprocess.run(["pkill", "-f", f"aplay.*{_DEVICE}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def play(path, *, block=False):
    """Fire-and-forget playback of one .wav file on the dedicated SFX
    amplifier. `block=True` waits for playback to finish; ordinary
    event-triggered SFX should leave this False so it never holds up
    whatever's calling it. Returns the subprocess.Popen (or None if aplay
    isn't available), mainly so callers/tests can check .poll()/.returncode."""
    global _current_proc
    if _APLAY is None or state.blackout_active:
        return None
    proc = subprocess.Popen(
        [_APLAY, "-q", "-D", _DEVICE, path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    with _lock:
        _current_proc = proc
    if block:
        proc.wait()
    return proc


_LEFT_CACHE = {}
_hw_level = None  # what we last set the card's "Speaker" control to


def _set_speaker(level):
    """Sets the USB card's hardware 'Speaker' control ('100%' or a raw step
    like '14'; the card has 0-37 steps, 0 dB at 37). Skipped if already set."""
    global _hw_level
    if _hw_level == level:
        return
    _hw_level = level
    try:
        subprocess.run(["amixer", "-q", "-c", config.SFX_ALSA_CARD_NAME, "sset", "Speaker", level, "unmute"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
    except Exception:
        pass


def _max_volume_once():
    """Forces the USB card's hardware volume to 100% (game SFX / Simon)."""
    _set_speaker("100%")


def _left_only(path):
    """Cached copy of `path` mixed down and panned FULL LEFT (right channel
    silent) -- aplay has no pan control, so it's baked into the file. Falls
    back to the original path on any error."""
    if path in _LEFT_CACHE:
        return _LEFT_CACHE[path]
    out = path
    try:
        import os, tempfile, wave
        import numpy as np
        with wave.open(path, "rb") as w:
            ch, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.readframes(w.getnframes())
        if width == 2:
            a = np.frombuffer(frames, dtype="<i2").astype(np.int32).reshape(-1, ch)
            left = np.clip(a.sum(axis=1), -32768, 32767).astype("<i2")
            st = np.zeros((len(left), 2), dtype="<i2")
            st[:, 0] = left
            d = os.path.join(tempfile.gettempdir(), "sfx_left")
            os.makedirs(d, exist_ok=True)
            out = os.path.join(d, f"{abs(hash(path))}_{os.path.basename(path)}")
            with wave.open(out, "wb") as o:
                o.setnchannels(2); o.setsampwidth(2); o.setframerate(rate)
                o.writeframes(st.tobytes())
    except Exception as e:
        print(f"[SFX] left-pan render failed for {path}: {e}")
        out = path
    _LEFT_CACHE[path] = out
    return out


_SCALED_CACHE = {}


def _scaled(path, gain):
    """Cached copy of a 16-bit .wav at `gain` x amplitude (aplay has no
    volume control). Falls back to the original path on any error."""
    key = (path, gain)
    if key in _SCALED_CACHE:
        return _SCALED_CACHE[key]
    out = path
    try:
        import os, tempfile, wave
        import numpy as np
        with wave.open(path, "rb") as w:
            ch, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.readframes(w.getnframes())
        if width == 2:
            a = np.frombuffer(frames, dtype="<i2").astype(np.float32) * gain
            d = os.path.join(tempfile.gettempdir(), "sfx_scaled")
            os.makedirs(d, exist_ok=True)
            out = os.path.join(d, f"{int(gain * 100)}_{os.path.basename(path)}")
            with wave.open(out, "wb") as o:
                o.setnchannels(ch); o.setsampwidth(2); o.setframerate(rate)
                o.writeframes(np.clip(a, -32768, 32767).astype("<i2").tobytes())
    except Exception as e:
        print(f"[SFX] volume scale failed for {path}: {e}")
        out = path
    _SCALED_CACHE[key] = out
    return out


_loop_gen = 0  # bumped by every play_replacing()/stop_current(); a loop thread exits when its token goes stale


def stop_current():
    """Cuts whatever this engine is playing right now (no-op if nothing)."""
    global _loop_gen
    with _lock:
        _loop_gen += 1
        proc = _current_proc
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=1.0)  # the plughw device is exclusive -- must be released before the next open
        except Exception:
            pass


def is_busy():
    """True while the clip started by play()/play_replacing() is still playing."""
    with _lock:
        proc = _current_proc
    return proc is not None and proc.poll() is None


def play_replacing(path, *, loop=False, then=()):
    """Plays `path` on the SFX amp, cutting off whatever was playing first
    (the device is exclusive, so overlapping clips would fail "busy" --
    last sound wins, which is what Simon's rapid presses want). `loop=True`
    repeats the clip until the next play_replacing()/stop_current() call
    (Simon's held-button tone). Returns False if aplay isn't available
    (Windows dev) so the caller can fall back to pygame."""
    global _loop_gen
    if _APLAY is None:
        return False
    stop_current()
    if state.blackout_active:
        return True
    _max_volume_once()
    path = _left_only(path)
    then = tuple(_left_only(t) for t in then)
    with _lock:
        token = _loop_gen
    if not loop and not then:
        play(path)
        return True
    if not loop:
        def _chain():
            for p in (path, *then):
                with _lock:
                    if token != _loop_gen:
                        return
                proc = play(p)
                if proc is None:
                    return
                proc.wait()
        threading.Thread(target=_chain, daemon=True, name="sfx-chain").start()
        return True

    def _run():
        while True:
            with _lock:
                if token != _loop_gen:
                    return
            proc = play(path)
            if proc is None:
                return
            proc.wait()
            if proc.returncode != 0:
                return  # don't spin on a failing device

    threading.Thread(target=_run, daemon=True, name="sfx-loop").start()
    return True


def cleanup():
    """Called from main.py's shutdown teardown, alongside the other
    drivers' own cleanup() calls -- kills whatever SFX playback is still
    in flight so a graceful shutdown doesn't leave an orphaned aplay
    holding the (exclusive) device for the next run to collide with. See
    _current_proc's docstring for what happens without this."""
    stop_current()


_SHOW_START_POLL_S = 0.1  # how often to check for an interrupt -- see _startup_once() below


def _startup_once():
    """Plays config.SFX_STARTUP_SOUND once on the SFX amp at app launch,
    then polls state.show_phase every _SHOW_START_POLL_S while it's still
    playing and interrupts it immediately -- kills the aplay child outright
    rather than letting it finish -- the instant any show start actually
    fires. "Any show start" = show_phase leaving its "setup" (panel menu)
    default, the one condition this checks: drivers/show_engine.py::
    start_show() from the panel menu, its hands-off timer, or the web remote
    -- so no separate hook into any of those paths is needed, just the poll.

    2026-09-20 redesign: previously looped the clip for as long as "setup"
    lasted and let an in-flight playthrough finish naturally once the
    phase changed instead of cutting it off -- replaced per explicit
    request ("play once" + "the instant any show starts, interrupt and
    stop playing").

    Started as a daemon thread at import time (see the bottom of this
    module), and this module is imported as literally the first line in
    main.py after the SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS env var --
    before drivers.midi_driver's pygame.midi.init(), before deck_
    orchestrator's pygame.mixer.init(), before graphics.matrix_canvas
    creates the LED display window. Satisfies "play a sound effect before
    the displays come online" by construction: nothing else has run yet
    when this thread starts."""
    if _APLAY is None:
        return
    _kill_stray_aplay()
    print(f"[SFX] Playing startup sound once on ALSA card '{config.SFX_ALSA_CARD_NAME}'.")
    # The card's hardware volume sits at max for game SFX; the startup sound
    # keeps its original quiet hardware step (14/37 = -23 dB, what it always
    # played at) and is scaled by SFX_STARTUP_VOLUME on top of that.
    _set_speaker(config.SFX_STARTUP_HW_STEP)
    proc = play(_scaled(config.SFX_STARTUP_SOUND, config.SFX_STARTUP_VOLUME), block=False)
    if proc is None:
        return
    while proc.poll() is None:
        if state.show_phase != "setup":
            proc.terminate()
            proc.wait()  # reap it -- terminate() alone leaves a zombie entry until something waits on it
            print("[SFX] Show started -- interrupting startup sound.")
            return
        time.sleep(_SHOW_START_POLL_S)
    if proc.returncode == 0:
        print("[SFX] Startup sound finished on its own.")
    else:
        print(f"[SFX] Startup sound exited with an error (code {proc.returncode}).")


threading.Thread(target=_startup_once, daemon=True, name="sfx-startup").start()
