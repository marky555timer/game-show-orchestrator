# Mark's to-do list

Things only Mark can do (content, hardware, decisions). Claude adds items here when they come up.

## Content

- [ ] **Replace the medley sweepers with less aggressive, less cartoony sound effects** (added 2026-10-06).
  The medley now plays a sweeper at EVERY hook change, so the current set
  (punchy `hb-` hits, Superfriends-style `st-` stars, `sweep`/`sweeper` whooshes)
  gets heard a lot and reads as too much. Drop new ones in `audio/MedleySweepers/`.
  Note: the file-name prefix picks the light transition that goes with each sound
  (`SWEEPER_TRANSITION_STYLES` in `config.py`: `hb` = flash, `st` = sparkle wipe,
  `sweep` = wipe/diagonal/curtain, `sweeper` = center-out/edges-in/pixel), so either
  name new files with one of those prefixes or ask Claude to add a new class.

## Songs (Add Tracks round 1, ingested 2026-10-13)

- [ ] **Review the questions made for the new songs** (added 2026-10-14). Use the `/questions` audit page.
- [ ] **Edit the new songs for medley capability, on the beat** (added 2026-10-14). Use the `/medley-edit` clip editor to set hook clips that land on the beat.

## Hardware

- [ ] **Rig status OLED + current sensor** (added 2026-10-14). Code is written (`drivers/status_display_engine.py`, config `STATUS_DISPLAY_*`) but undeployed and untested.
  - Buy: 128x64 I2C OLED (check SSD1306 vs SH1106), INA219/INA226 current sensor (Hall-effect or external shunt if measuring the whole rig).
  - Wire both to I2C bus 1 (header pins 3/5, plus 3.3V and GND); run `i2cdetect -y 1`.
  - On the Pi: `pip install luma.oled smbus2`, enable I2C (`sudo raspi-config nonint do_i2c 0`).
  - Set the real battery Wh and empty/full volts in `config.py` (currently placeholders).
  - Shows amps, battery time, IP, uptime, temp; blinking NO TUNNEL warning after 90 s uptime.
  - Planned (not yet coded): show "CHG" instead of a battery time when current is negative (trickle-charging in BLACKOUT); needs a bidirectional INA226 wired so charge current reads negative.

## Other open items (collected from earlier work, 2026-10-14)

- [ ] **Try the new show open on the Pi** (added 2026-10-14). Deployed but never watched on the real rig: Puddles outline, DMX white chase, decade wheel, any-button skip, and the song cued from the landed decade (needs real tags).
- [ ] **Judge the intro theme's volume on the rig** (added 2026-10-14). By measurement it's not quieter than the songs; tell Claude which track it sounded quieter than.
- [ ] **Wire the marquee button to GPIO4** (pending since 2026-10-03).
- [ ] **Try the BLACKOUT + MUTE admin button by eye** (built 2026-10-06, never tried).
- [ ] **Run a ~1 hour soak test before drilling the box**. Peak CPU was 62.0 C sealed with no throttling.
- [ ] **Triviacade.com DNS fails on the Pi**, so it can't reach the redirector (seen 2026-10-14). Check the router or DNS settings.
- [ ] **Finish the outline board WiFi shakedown** (RF-quiet rig, pending since 2026-10-03).
