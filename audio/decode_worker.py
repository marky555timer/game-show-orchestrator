"""Standalone decode worker -- runs as its own OS process, launched by
audio/dj_engine.py via subprocess.Popen (deliberately NOT multiprocessing.
Process -- see below). Track decoding happens entirely in this separate
process now, not just a background thread, so it cannot compete with the
main process's real-time audio callback/ramp-scheduler threads for CPU
scheduling at all -- full OS-level isolation, the thing thread-priority
tricks could only approximate (2026-08-08: buffer size + thread priority
both measurably helped but didn't fully clear a dropout landing right when
a decode starts; this is the next lever up).

Why subprocess.Popen and not multiprocessing.Process: this codebase runs
real side effects at import time in several places by design (e.g.
web/remote_server.py's own docstring: "Self-starts at import time... main.py
just needs `from web import remote_server` for the side effect"). On
Windows, multiprocessing's spawn start method re-imports whatever script was
originally run as __main__ in every child process to rebuild enough state to
unpickle the target -- which would replay ALL of those import-time side
effects (a second web server trying to bind the same port, a second
pygame.mixer.init(), a second decode worker spawning a third...) inside the
"worker" process. subprocess.Popen runs this file as a genuinely independent
interpreter that only ever imports what THIS file imports, so none of that
can happen.

Never touches the real audio device (SDL_AUDIODRIVER=dummy) -- it only needs
pygame.mixer.Sound() for its decode logic, the exact same code path
DJEngine.load() uses in-process (so behavior/format matches exactly), it
just never opens real hardware, so it can't conflict with the main
process's already-open device either.

Wire protocol over stdin/stdout, binary and explicitly length-framed (using
.buffer, not the text-mode streams, so Windows can't mangle raw PCM bytes
that happen to contain newline-like byte sequences):
  request  (parent -> worker): 4-byte big-endian length, then that many
           UTF-8 bytes -- the file path to decode.
  response (worker -> parent): 1 status byte (0 = ok, 1 = error), then a
           4-byte big-endian length, then that many bytes -- raw PCM
           (status 0) or a UTF-8 error message (status 1).
One request in flight at a time -- this show decodes roughly one track
every few minutes, nothing here needs concurrency.
"""
import os
import struct
import sys

os.environ["SDL_AUDIODRIVER"] = "dummy"
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

import pygame
import pygame.sndarray

MIXER_FREQUENCY = 44100
MIXER_SIZE = -16
MIXER_CHANNELS = 2


def _read_exact(stream, n):
    buf = b""
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            return None  # parent closed the pipe -- exit quietly
        buf += chunk
    return buf


def main():
    pygame.mixer.init(frequency=MIXER_FREQUENCY, size=MIXER_SIZE, channels=MIXER_CHANNELS, buffer=512)
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer

    while True:
        header = _read_exact(stdin, 4)
        if header is None:
            return
        (length,) = struct.unpack(">I", header)
        path_bytes = _read_exact(stdin, length)
        if path_bytes is None:
            return
        path = path_bytes.decode("utf-8")

        try:
            sound = pygame.mixer.Sound(path)
            raw = pygame.sndarray.array(sound).tobytes()
            stdout.write(b"\x00" + struct.pack(">I", len(raw)) + raw)
        except Exception as e:
            msg = str(e).encode("utf-8")
            stdout.write(b"\x01" + struct.pack(">I", len(msg)) + msg)
        stdout.flush()


if __name__ == "__main__":
    main()
