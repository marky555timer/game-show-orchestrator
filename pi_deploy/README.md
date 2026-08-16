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

## WiFi fallback hotspot (2026-08-16)

Fully independent of the show app -- if the Pi has no working WiFi at boot,
it opens an open "TriviaRig-Setup" hotspot with a captive portal so the
operator can hand it new credentials from their phone (same UX as hotel
WiFi). Live-tested end to end on the show Pi.

- `wifi_provision.sh` -- installed to `/usr/local/bin/wifi_provision.sh`
  (`chmod 755`). Waits up to 20s for NetworkManager's own auto-connect; if
  nothing connects, loops launching `wifi-connect` until it succeeds, then
  reboots automatically (no in-portal "tap to reboot" step is possible --
  the AP tears down as part of every connection attempt, killing the
  phone's link to the portal before a confirmation page could ever be
  served).
- `wifi-provision.service` -- installed to
  `/etc/systemd/system/wifi-provision.service`, then
  `systemctl daemon-reload && systemctl enable wifi-provision.service`.
  **Enabling only wires it up for the *next* boot** -- it does not
  retroactively start on the boot it was enabled during.
- `wifi-connect` binary + UI (NOT tracked here, downloaded directly) --
  balena's [wifi-connect](https://github.com/balena-os/wifi-connect),
  v4.11.84, `aarch64-unknown-linux-gnu` build:
  ```
  curl -sL -o wifi-connect.tar.gz https://github.com/balena-os/wifi-connect/releases/download/v4.11.84/wifi-connect-aarch64-unknown-linux-gnu.tar.gz
  curl -sL -o wifi-connect-ui.tar.gz https://github.com/balena-os/wifi-connect/releases/download/v4.11.84/wifi-connect-ui.tar.gz
  sudo tar -xzf wifi-connect.tar.gz -C /usr/local/bin/
  sudo mkdir -p /usr/local/share/wifi-connect && sudo tar -xzf wifi-connect-ui.tar.gz -C /usr/local/share/wifi-connect/
  ```
