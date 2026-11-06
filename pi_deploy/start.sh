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
# Wait (max 45s) for NTP: the Pi has no RTC battery, so it boots with the
# clock from its last shutdown and NTP steps it hours forward once WiFi is
# up -- a step mid-song made Auto-DJ skip the track (2026-10-26). If the
# network is down this gives up and starts anyway; drivers/clock_guard.py
# absorbs a later step.
for _i in $(seq 1 45); do
    [ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" = "yes" ] && break
    sleep 1
done
echo "[START] $(date '+%F %T') clock synced=$(timedatectl show -p NTPSynchronized --value 2>/dev/null) after ${_i}s wait" >> /home/mark/game-show-orchestrator/startup.log
# -u (unbuffered stdout/stderr): without it, output redirected to a file
# (not a terminal) is fully block-buffered, so prints -- including the
# "[BUTTON] Raw Button Pressed" diagnostic line -- can sit unflushed in
# memory for a long time before ever reaching startup.log, even though the
# action itself already fired. Confirmed 2026-08-14 debugging a button
# remap: the log showed nothing for several real button presses even
# though one of them visibly triggered Price Game.
exec .venv/bin/python -u main.py >> /home/mark/game-show-orchestrator/startup.log 2>&1
