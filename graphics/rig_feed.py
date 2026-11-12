# graphics/rig_feed.py
"""Show-app side of the practice rig's TV view (2026-11-11, config.PRACTICE_RIG).

Starts graphics/rig_view.py as its own process and sends it what to draw,
config.RIG_VIEW_FPS times a second, as one small UDP datagram on localhost:
the LED matrix (one bit per LED, the same "any red = lit" rule
drivers/led_bridge.py uses), the marquee's pixel buffer, the outline strip's
current look and the four button lights. Sending costs well under a
millisecond; the drawing and the slow screen updates happen in the other
process, so the show loop never waits for the TV.

graphics/matrix_canvas.py::render_led_grid() calls send() once per frame.
"""
import atexit
import os
import socket
import subprocess
import sys
import time

import numpy as np
import pygame

import config
from drivers import accent_engine, simon_hardware, wled_engine
from graphics import rig_view

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
_sock.setblocking(False)
_addr = ("127.0.0.1", config.RIG_VIEW_PORT)
_proc = None
_spawned_at = -1e9
_last_send = 0.0


def _ensure_viewer(mono):
    """Starts the TV window process, and again if it ever dies (at most every 10 s)."""
    global _proc, _spawned_at
    if _proc is not None and _proc.poll() is None:
        return
    if mono - _spawned_at < 10.0:
        return
    _spawned_at = mono
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.bind(_addr)
    except OSError:
        return   # a window from before an in-place app restart is still up and listening
    finally:
        probe.close()
    try:
        _proc = subprocess.Popen([sys.executable, "-u", "-m", "graphics.rig_view"], cwd=_ROOT)
        print(f"[RIG VIEW] Practice-board window started (pid {_proc.pid}).")
    except Exception as e:
        print(f"[RIG VIEW] Could not start the practice-board window: {e}")


def _stop_viewer():
    if _proc is not None and _proc.poll() is None:
        _proc.terminate()


atexit.register(_stop_viewer)


def send(matrix_surface):
    global _last_send
    mono = time.monotonic()
    if mono - _last_send < 1.0 / max(1, config.RIG_VIEW_FPS) - 0.004:
        return
    _last_send = mono
    _ensure_viewer(mono)
    red = pygame.surfarray.pixels_red(matrix_surface)
    lit = np.asarray(red) > 0
    del red
    try:
        _sock.sendto(rig_view.pack(lit, wled_engine._pixels, accent_engine.view_state(),
                                   simon_hardware.led_states()), _addr)
    except OSError:
        pass   # window not up yet / buffer full: the next frame replaces this one
