#!/bin/bash
# Auto-start wrapper for Game Show Orchestrator (Trivia Night rig).
# Launched via ~/.config/autostart/ once the desktop session comes up.
#
# Root cause of the autostart hang (found 2026-08-14): the hang wasn't in
# pygame.mixer.init() (audio) at all -- it was in `import pygame` itself,
# before pygame even prints its version banner. Exporting DISPLAY=:0
# (needed to get *some* video driver working under earlier attempts) made
# SDL2 probe an X11/XWayland path that stalls indefinitely on this labwc
# session. Forcing SDL_VIDEODRIVER=wayland directly and NOT setting DISPLAY
# makes `import pygame` + pygame.init() complete in ~0.07s every time.
export XDG_RUNTIME_DIR=/run/user/1000
export WAYLAND_DISPLAY=wayland-0
export SDL_VIDEODRIVER=wayland
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus

sleep 5
cd /home/mark/game-show-orchestrator
# -u (unbuffered stdout/stderr): without it, output redirected to a file
# (not a terminal) is fully block-buffered, so prints -- including the
# "[BUTTON] Raw Button Pressed" diagnostic line -- can sit unflushed in
# memory for a long time before ever reaching startup.log, even though the
# action itself already fired. Confirmed 2026-08-14 debugging a button
# remap: the log showed nothing for several real button presses even
# though one of them visibly triggered Price Game.
exec .venv/bin/python -u main.py >> /home/mark/game-show-orchestrator/startup.log 2>&1
