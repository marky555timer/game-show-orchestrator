# Pi deployment files

Reference copies of what's actually live on the show Pi at
`/home/mark/game-show-orchestrator/` (`mark@<pi-ip>`, SSH key
`~/.ssh/trivia_pi` -- the Pi's DHCP-assigned IP drifts between sessions,
confirm the current one rather than trusting a stale note; it was
`192.168.1.34` as of the 2026-09-11 Simon hardware deploy). Not run from
here -- copy into place after cloning:

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

## Admin "SHUT DOWN PI" / "RESTART PI" (real poweroff/reboot, not just the app)

The web remote's "RESTART APP" button (Application section, top-level --
2026-09-21, replaces the old unconditional "SHUTDOWN APP") just exits and
relaunches the Python process (`os.execv`, see main.py's teardown block) --
no sudoers needed. The two buttons under Application > Administrator go
further and touch the Pi itself:

- "SHUT DOWN PI" (`/api/system/poweroff`) -- `sudo shutdown -h now`
- "RESTART PI" (`/api/system/reboot`) -- `sudo reboot`

Both run the same graceful app teardown first, then `main.py` shells out to
the command above. Each needs its own one-time passwordless sudoers entry
on the Pi, since the app runs as `mark` under the desktop autostart
session, not root:

```
sudo visudo -f /etc/sudoers.d/game-show-orchestrator
```
add both lines:
```
mark ALL=(root) NOPASSWD: /sbin/shutdown -h now
mark ALL=(root) NOPASSWD: /sbin/reboot
```
Without the matching line, that button's teardown still runs (cache save,
DMX blackout) but the final poweroff/reboot step fails and just logs a
line to startup.log instead of actually powering off/rebooting.

## Exterior shutdown/power button (GPIO3, no custom wiring code needed)

Raspberry Pi OS has a built-in clean-shutdown feature on GPIO3 (physical
pin 5) that needs no code and no extra components -- just a momentary
push button:

1. Wire a normally-open momentary push button between **physical pin 5**
   (GPIO3 / SCL) and **physical pin 6** (GND) on the 40-pin header --
   they're adjacent, so two short wires out to a panel-mount button on the
   box exterior is all it takes. No resistor needed (the overlay below
   enables GPIO3's internal pull-up); polarity doesn't matter since it's
   just a dry contact.
2. On the Pi, add this line to `/boot/firmware/config.txt` (older OS
   images: `/boot/config.txt`):
   ```
   dtoverlay=gpio-shutdown
   ```
3. `sudo reboot` once for the overlay to take effect.

After that, a single press cleanly shuts the Pi down (same as `shutdown -h
now` -- SIGTERM reaches this app first via `main.py`'s handler, so cache
save/DMX blackout/driver stop still run before power actually drops).
GPIO3 is wired to the SoC's wake circuit, so the same button also powers
the Pi back **on** with another press once it's fully off -- one button
covers both directions, matching the "push button on the exterior of the
box" ask. No relay, transistor, or debounce circuit required.

## Show shutdown button (GPIO26, app-driven, 2026-10-07)

Momentary push button between **physical pin 37** (GPIO26) and **physical
pin 39** (GND). Internal pull-up, no resistor. Hold ~1 s: instant blackout
(lights, red matrix text, sound), then the same graceful teardown + `sudo
shutdown -h now` as "SHUT DOWN PI" (needs that sudoers line above). Code:
`drivers/shutdown_button.py`, settings `SHUTDOWN_BUTTON_*` in config.py.
Chosen instead of the GPIO3 overlay above because the HiFiBerry DAC+ uses
GPIO2/3 (I2C).

## Simon hardware (physical buttons + LEDs, 2026-09-11)

Four arcade push-buttons and four LEDs wired directly to the Pi's 40-pin
GPIO header for the Milton Bradley "Simon" mini-game hardware bring-up
(drivers/simon_hardware.py backs the "Simon Hardware Test" section of the
web remote's Advanced panel -- this is bring-up/test wiring only, not yet
wired into actual gameplay, which still runs off the joystick's
simon_select_1..4 bindings). BCM numbering; pin map lives in config.py's
`SIMON_HW_BUTTON_PINS`/`SIMON_HW_LED_PINS`.

**Buttons** (momentary switch to GND, internal pull-up, active LOW). Red/blue
were corrected 2026-09-13 -- the leads were crossed on the header, so
pressing the physical Red button lit the Blue indicator and vice versa:

| Color  | BCM | Physical pin |
|--------|-----|--------------|
| Green  | 17  | 11           |
| Red    | 23  | 16           |
| Yellow | 22  | 15           |
| Blue   | 27  | 13           |

Common return: GND, physical pin 14.

**LEDs** (12V, each channel switched through a MOSFET -- GPIO only drives
the gate, never the LED directly). Corrected 2026-09-13 -- all four were
wired one color off in a single rotation (e.g. commanding Blue actually lit
the Green LED):

| Color  | BCM | Physical pin |
|--------|-----|--------------|
| Green  | 13  | 33           |
| Red    | 5   | 29           |
| Yellow | 6   | 31           |
| Blue   | 12  | 32           |

Common return: GND, physical pin 30.

Requires `rpi-lgpio` (in requirements-linux.txt), NOT the classic
`RPi.GPIO` package -- this rig is a **Raspberry Pi 5**, whose RP1 I/O chip
the legacy `RPi.GPIO` can't address at all (`RuntimeError: Cannot
determine SOC peripheral base address` on `GPIO.setup()`, confirmed
2026-09-11 deploy). `rpi-lgpio` is a drop-in replacement exposing the same
`RPi.GPIO` import name/API on top of the `lgpio` backend the Pi 5 needs.
Building it from source (no prebuilt wheel for this arm64/Python 3.13
combo) needs two system packages first: `sudo apt-get install -y swig
liblgpio-dev`. `drivers/simon_hardware.py`'s own `import RPi.GPIO as GPIO`
line doesn't change either way -- no-ops cleanly with a log line on
anything else (the Windows dev machine included).

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

- **Lenovo practice box (2026-11-26):** same script and service, the
  `x86_64-unknown-linux-gnu` build of wifi-connect. Files staged in `~/wifi-setup`
  on the Lenovo; `sudo bash ~/wifi-setup/install_wifi_provision.sh` does all the
  root steps above (binary + UI, script, service, enable).

## Practice rig: second Pi on an HDMI TV (2026-11-11)

A Pi with no game board attached that rehearses the show: usual audio (over
HDMI) and web remote, plus the whole board drawn on the TV -- LED panels,
marquee bulbs, outline strip, the four button lights. Built on a Pi 3 B
(`trivia2`, 1 GB).

- Switched on by `practice_rig.json` next to `config.py` (not in git):
  `{"fps": 20, "tv_mode": "1280x720"}`. Delete the file and the Pi behaves
  like a normal rig again. See config.py's PRACTICE RIG section.
- `graphics/rig_view.py` is the TV window. It runs as its own process,
  started and fed by `graphics/rig_feed.py` over localhost UDP, because on a
  Pi 3 every screen update costs 10-30 ms and stalled the 40 fps show loop
  when drawn in-process. `tv_mode` drops the TV to 720p for the same reason.
- The outline strip is a stand-in: WLED's effects run on the real board.
  It is held at one steady colour (the look's main colour), never blinking
  or moving, with the effect name printed under the board. DMX lights are
  not drawn.
- No arcade buttons are wired, so the web remote has **Arcade Buttons**
  (`/buttons`, in the menu): four pads that press, hold and combine like the
  real ones, and glow when the button's light is lit.
- The GPIO kill / blackout / recovery buttons are disabled in practice mode.
- Setup beyond the normal steps above: `wpctl set-default <HDMI sink>` for
  audio, `raspi-config nonint do_blanking 1`, WiFi power-save off, and
  `cloudflared` for the tunnel. `rpi-lgpio` is not needed (the venv is made
  with `--system-site-packages`).
- With no `failover_role.json` it acts as the primary and points the QR
  redirect at itself. Switch it off, or give it a backup role, before the
  main rig goes back on.
- A weak power supply halves a Pi 3's speed: `vcgencmd get_throttled` should
  read `0x0`, and `vcgencmd measure_clock arm` 1200 MHz under load, not 600.

### Practice rig on a PC: Lenovo ThinkCentre M700 Tiny (2026-11-11)

Replaced the Pi 3 the same day: i3-6100T, 16 GB, SSD, Debian 13 + Xfce (X11),
hostname `TriviaLenovo`. Same files and `practice_rig.json` (`{"fps": 30}`,
no `tv_mode` -- it draws 1080p without trouble). Differences from a Pi:

- `start.sh` detects X11 (no `wayland-0` socket): sets `DISPLAY`, turns
  screen blanking off, and switches PulseAudio to the HDMI/DisplayPort
  output that has a screen on it.
- Autologin: `/etc/lightdm/lightdm.conf.d/50-autologin.conf`. `light-locker`
  removed; Xfce power manager set to never blank.
- No GPIO, no `vcgencmd`: the Simon hardware and power monitor report
  unavailable, which is harmless.
- `cloudflared` from the amd64 .deb.
