# Pi deployment files

Reference copies of what's actually live on the show Pi at
`/home/mark/game-show-orchestrator/` (`mark@192.168.1.26`, SSH key
`~/.ssh/trivia_pi`). Not run from here -- copy into place after cloning:

- `requirements-linux.txt` -- same as the root `requirements.txt` but swaps
  `pywin32` for `pyserial` and drops other Windows-only deps. Install into
  a venv: `python3 -m venv .venv && .venv/bin/pip install -r pi_deploy/requirements-linux.txt`
- `start.sh` -- autostart/launch wrapper. Sets `SDL_VIDEODRIVER=wayland`
  and deliberately does NOT set `DISPLAY` (see the comment in the file --
  setting it hangs `import pygame` on this labwc/Wayland session). Copy to
  `~/game-show-orchestrator/start.sh` and `chmod +x` it.
- `game-show-orchestrator.desktop` -- launcher entry. Copy to BOTH
  `~/.config/autostart/` (auto-launch on login) and `~/Desktop/` (manual
  double-click icon -- also needs `gio set <path> metadata::trusted true`
  or pcmanfm shows an "untrusted launcher" prompt instead of running it).

`config.py`'s `ENTTEC_PORT` is also patched on the Pi to a Linux serial
path (`/dev/ttyUSB0`) instead of the Windows `COM5` default -- that edit
lives directly in the Pi's own `config.py`, not tracked separately here.

Audio (`audio/music/`) is gitignored and lives on the device only --
transfer it separately (tar over ssh, or a USB drive), not via git.
