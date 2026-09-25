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
    if _APLAY is None:
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


def cleanup():
    """Called from main.py's shutdown teardown, alongside the other
    drivers' own cleanup() calls -- kills whatever SFX playback is still
    in flight so a graceful shutdown doesn't leave an orphaned aplay
    holding the (exclusive) device for the next run to collide with. See
    _current_proc's docstring for what happens without this."""
    with _lock:
        proc = _current_proc
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass


_SHOW_START_POLL_S = 0.1  # how often to check for an interrupt -- see _startup_once() below


def _startup_once():
    """Plays config.SFX_STARTUP_SOUND once on the SFX amp at app launch,
    then polls state.show_phase every _SHOW_START_POLL_S while it's still
    playing and interrupts it immediately -- kills the aplay child outright
    rather than letting it finish -- the instant any show start actually
    fires. "Any show start" covers both real triggers that move show_phase
    off its "setup" default, the one condition this checks: a normal start
    (drivers/show_engine.py::enter_dark(), the operator's "Start Game" /
    scheduled-countdown-reaching-zero path) and an auto start (that same
    module's _enter_unattended_autoplay(), the unattended-idle-timeout
    autoplay) -- both flip show_phase away from "setup" already, so no
    separate hook into either path is needed here, just the poll.

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
    proc = play(config.SFX_STARTUP_SOUND, block=False)
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
