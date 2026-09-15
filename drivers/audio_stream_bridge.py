# drivers/audio_stream_bridge.py
"""Live audio broadcast to phones (2026-08-17): loopback-captures whatever
is actually coming out of the Pi's speakers/PA -- music, sweepers, VOs,
SFX, all of it -- and re-encodes it to MP3 on the fly so a connected phone
can listen in over the web remote, gated behind the normally-unchecked
"Listen to the show" toggle (web/static/play.html, /api/audio/stream in
web/remote_server.py). This taps the system's actual audio output rather
than re-playing source files from the deck engine, so crossfades/ducking/
announcement overlays are already baked into the captured signal exactly
as the room hears them -- no need to duplicate audio/dj_engine.py's mixing
logic here.

One shared ffmpeg encoder process feeds every connected listener (not one
per phone) -- fanned out via a broadcast list of per-client queues below,
so N phones listening costs one loopback capture + one MP3 encode, not N.
The encoder only runs while at least one phone is actually listening
(started lazily on the first subscriber, killed once the last one
disconnects), so an idle show isn't burning Pi CPU/power on a stream
nobody's using.

Linux/PipeWire only -- see available(). The show Pi doesn't have
pulseaudio-utils installed (no `pactl`), just the native PipeWire tools, so
the default sink is discovered via `wpctl status`'s "Default Configured
Devices" line rather than `pactl get-default-sink`; ffmpeg itself still
captures via its built-in `-f pulse` input against that sink's
`<node.name>.monitor`, since the Pi's PipeWire runs the pipewire-pulse
compatibility protocol regardless of whether the `pactl` CLI is present
(confirmed live 2026-08-17 -- pygame/SDL2's own audio output connects
through that exact same protocol, client.api "pipewire-pulse" in `pw-cli
ls Node`). No-op on anything else, same "safe to call regardless of host"
convention as drivers/power_monitor.py and drivers/bluetooth_engine.py."""
import queue
import re
import shutil
import subprocess
import sys
import threading

_CHUNK_SIZE = 4096
_QUEUE_MAXSIZE = 64  # a few seconds of buffered MP3 per listener before old audio drops
_DEFAULT_SINK_RE = re.compile(r"Audio/Sink\s+(\S+)")

_lock = threading.Lock()
_proc = None
_reader_thread = None
_subscribers = []  # list[queue.Queue] -- one per connected phone


def available():
    return (
        sys.platform.startswith("linux")
        and shutil.which("ffmpeg") is not None
        and shutil.which("wpctl") is not None
    )


def _monitor_source():
    """Pulse-compatible monitor source name for the current default sink --
    i.e. "everything currently being played" -- or None if it can't be
    determined (wpctl missing/failing, no default sink configured)."""
    try:
        out = subprocess.run(
            ["wpctl", "status"],
            capture_output=True, text=True, timeout=3.0,
        ).stdout
    except Exception:
        return None
    match = _DEFAULT_SINK_RE.search(out)
    return f"{match.group(1)}.monitor" if match else None


def _reader_loop(proc):
    """Background-thread body for the lifetime of one ffmpeg process: reads
    encoded MP3 bytes as they're produced and fans each chunk out to every
    currently-subscribed listener. A slow/stalled listener (its queue full)
    drops its own oldest buffered chunk rather than ever blocking the
    shared encoder for everyone else."""
    try:
        while True:
            chunk = proc.stdout.read(_CHUNK_SIZE)
            if not chunk:
                break
            with _lock:
                subs = list(_subscribers)
            for q in subs:
                try:
                    q.put_nowait(chunk)
                except queue.Full:
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        q.put_nowait(chunk)
                    except queue.Full:
                        pass
    finally:
        with _lock:
            subs = list(_subscribers)
        for q in subs:
            try:
                q.put_nowait(None)  # sentinel: encoder died/stopped, close this listener out
            except queue.Full:
                pass


def _ensure_encoder_running():
    """Starts the shared loopback-capture/encode process if it isn't
    already running. Caller must hold _lock."""
    global _proc, _reader_thread
    if _proc is not None and _proc.poll() is None:
        return True

    source = _monitor_source()
    if not source:
        return False

    _proc = subprocess.Popen(
        [
            "ffmpeg", "-loglevel", "error",
            "-f", "pulse", "-i", source,
            "-ac", "2", "-ar", "44100",
            "-f", "mp3", "-b:a", "128k",
            "-",
        ],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    _reader_thread = threading.Thread(target=_reader_loop, args=(_proc,), daemon=True)
    _reader_thread.start()
    return True


def subscribe():
    """Registers a new listener, (re)starting the shared encoder if this is
    the first one. Returns a queue.Queue of MP3 byte chunks (a None item
    means the stream ended -- stop reading), or None if streaming isn't
    available on this host. Always pair with unsubscribe() in a finally
    block so the encoder gets torn down once nobody's listening."""
    if not available():
        return None
    with _lock:
        if not _ensure_encoder_running():
            return None
        q = queue.Queue(maxsize=_QUEUE_MAXSIZE)
        _subscribers.append(q)
        return q


def unsubscribe(q):
    """Drops a listener; kills the shared encoder once nobody's left
    listening so an idle show doesn't keep capturing/encoding audio nobody
    is receiving."""
    global _proc
    with _lock:
        if q in _subscribers:
            _subscribers.remove(q)
        if not _subscribers and _proc is not None:
            proc, _proc = _proc, None
            proc.kill()
            proc.wait()  # reap it now -- kill() alone leaves a zombie until waited on
