# graphics/tv_live.py
"""Live board feed for the Remote page's TV preview (2026-11-28).

The Remote page's TV button opens the Show Designer's preview page in live mode
(web/static/show_sim.html?live=1), which draws the cabinet itself -- LED panels,
marquee bulbs, the four button lights -- from this feed instead of a video
picture.

graphics/matrix_canvas.py::render_led_grid() calls capture() every frame; it does
work only while someone is watching (web/remote_server.py's /api/tv/frames asked
in the last few seconds), at most FPS times a second. Frames are kept, stamped
with the time they were shown, for KEEP_SECONDS: the page fetches them in batches
a few times a second and plays them back smoothly, a few seconds late so the
picture lines up with the show's audio stream (which runs behind the room).

One frame = the matrix one bit per LED, row by row (64 x MATRIX_HEIGHT), then the
marquee's RGB bytes, then one byte of button lights (bit 0 green, 1 red, 2 yellow,
3 blue).
"""
import collections
import threading
import time

import numpy as np
import pygame

import config

FPS = 25
WATCH_SECONDS = 5.0
KEEP_SECONDS = 12.0
_COLORS = ("green", "red", "yellow", "blue")

_lock = threading.Lock()
_frames = collections.deque()          # (seq, monotonic time, bytes) -- only frames that differ
_state = {"seq": 0, "last": b"", "at": 0.0}
_watch = {"at": 0.0}


def watching():
    _watch["at"] = time.monotonic()


def capture(matrix_surface):
    now = time.monotonic()
    if now - _watch["at"] > WATCH_SECONDS or now - _state["at"] < 1.0 / FPS - 0.004:
        return
    _state["at"] = now
    try:
        red = pygame.surfarray.pixels_red(matrix_surface)
        lit = (np.asarray(red) > 0).T          # surfarray is (x, y): rows first for the browser
        del red
        from drivers import wled_engine, simon_hardware
        leds = simon_hardware.led_states()
        btn = sum(1 << i for i, c in enumerate(_COLORS) if leds.get(c))
        data = np.packbits(lit).tobytes() + bytes(wled_engine._pixels) + bytes([btn])
    except Exception:
        return
    if data == _state["last"]:
        return
    with _lock:
        _state["last"] = data
        _state["seq"] += 1
        _frames.append((_state["seq"], now, data))
        while _frames and now - _frames[0][1] > KEEP_SECONDS:
            _frames.popleft()


def since(seq):
    """(now, frames newer than `seq`) -- if `seq` is unknown/too old, the last frame before the window
    too, so the page always has something to show."""
    with _lock:
        fr = list(_frames)
        cur = (_state["seq"], _state["at"], _state["last"])
    new = [f for f in fr if f[0] > seq]
    if not new and cur[2] and seq < cur[0]:
        new = [cur]                  # nothing changed for a while: the board as it is
    return time.monotonic(), new


def layout():
    """What the browser needs to read a frame."""
    return {"width": config.MATRIX_WIDTH, "height": config.MATRIX_HEIGHT,
            "rows": {str(k): v[1] for k, v in config.PANELS.items()},
            "marquee_leds": config.MARQUEE_TOTAL_LEDS,
            "marquee_start": {k: v["start"] for k, v in config.MARQUEE_SEGMENTS.items()}}
