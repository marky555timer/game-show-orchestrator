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

- [ ] **TOMORROW (2026-11-19): dig the Letotech USB-RS485 DMX cable out of the garage** (added 2026-11-18). Open-DMX mode is already deployed on the Lenovo (`DMX_MODE = "auto"`, drivers/dmx_driver.py). Plug the cable into the Lenovo, XLR into the wireless DMX transmitter (check connector gender; gender changer if needed), power the 10 Rockville uplights at addresses 1, 17, 33... and look for `[DMX OPEN] Successfully connected` in the app log. Untested on real hardware: check break timing / flicker.

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
- [x] **Wire the marquee recovery button to GPIO4** (dropped 2026-11-17: not needed, the rig has run several hours with no marquee trouble).
  - The software (`drivers/recovery_button.py`, `RECOVERY_BUTTON_*` in `config.py`) stays installed and does nothing unless a button is wired to GPIO4 (pin 7) and GND (pin 6 or 9). The old auto-reset in `wled_engine.py` stays off on purpose.
- [x] **Try the BLACKOUT + MUTE admin button by eye** (built 2026-10-06; done, per Mark 2026-11-17).
- [ ] **Run a ~1 hour soak test before drilling the box**. Peak CPU was 62.0 C sealed with no throttling.
- [ ] **Triviacade.com DNS fails on the Pi**, so it can't reach the redirector (seen 2026-10-14). Check the router or DNS settings.
- [ ] **Finish the outline board WiFi shakedown** (RF-quiet rig, pending since 2026-10-03).

## When the rig is back (~2026-12-02)

- [ ] **Remind Claude to deploy the 2026-11 phone features to the rig** (added 2026-11-16). Everything below is only on the Lenovo practice box; the rig has none of it.
  - Features: song tags/intermission stats, team names + emoji avatars, speed-scaled points (win score 1000), streaks, "tonight" recap + Final Scores, category rounds, and persistent results (session id, `results/<id>.json`, `/play?r=<id>`).
  - Before deploying: diff the rig's copies of every changed file first, and pull the rig's data back to the PC (never overwrite the rig's questions/tags from the PC). Merge the rig's real tags into the PC and Lenovo copies.
  - The rig's `track_cache.json` needs the clipped-answer repair.
  - Changed files: config.py, state.py, play.html, index.html, setup.html, remote_server.py, remote_auth.py, gamepad.py, deck_orchestrator.py, show_engine.py, show_library.py, failover_engine.py, medley_game.py, matrix_canvas.py, plus new drivers player_profile.py, category_round_engine.py, results_archive.py.
  - Also copy `audio/gameMusic/` decade stabs (50s-2010s `-v*.wav`, 19 files; 70s-v3 was updated) to the rig. They are on the Lenovo already (2026-11-16). No 2020s stabs exist yet, and the 2010s has only one variant.
  - Also deploy the Show Designer song filters/sorting: `web/static/designer.html` and `drivers/web_sync.py` (added 2026-11-16, on the Lenovo already). The rig's own real tags (explicit especially) are what make the filters meaningful.
  - Also deploy the intermission leaderboard (added 2026-11-17, on the Lenovo already): phones show the last round's final scores during intermission. Files: `state.py` (`last_round_board`), `drivers/win_sequence_engine.py` (snapshot before scores reset), `drivers/show_engine.py` (reset), `web/remote_server.py` (`last_round_board`/`last_round_number` in the question data), `web/static/play.html`. Never seen on a real phone yet; try it with a short round after deploying.
  - Also replace the "How many Grammys..." questions on the rig (added 2026-11-17; done on the PC and the Lenovo). Stop the app, then run `python3 tools/replace_grammy_count_questions.py` on the rig (it backs up `track_cache.json` first and swaps in 54 band/news questions by song). Any Grammy-count question on a song it has no replacement for is reported, not touched; ask Claude to write those. Also copy `fallback_questions.json` (the Beyonce Grammy question was swapped). The replacements show amber/"generated" on the `/questions` audit page for review.
  - Also deploy the "nothing over a question" fix (added 2026-11-17, on the Lenovo): `graphics/matrix_canvas.py` (hook words stay off while a question or the Mystery teaser is up) and `drivers/mystery_band_engine.py` (the teaser waits until an on-screen question finishes).
  - Also deploy the competitor-inspired phone/TV touches (added 2026-11-17, on the Lenovo, needs a restart): named streak call-outs with a full-screen "3 in a row" takeover + smaller bottom banner for 4+ (both with a ding), a rotating lobby poll and the no-scroll "question mode" layout (2026-11-18; slim logo strip, one status row, choices share the leftover height) in `web/static/play.html`, `web/remote_server.py` (`/api/player/poll-vote`, `lobby_poll` in the question data), `config.py` (`LOBBY_POLLS`, `LOBBY_POLL_MINUTES`, `SOCIAL_HANDLE`), and the "JOIN: triviacade.com/play" strip on the practice TV board (`graphics/rig_view.py`; the rig itself never renders the board). Set `SOCIAL_HANDLE` in `config.py` once the Triviacade social handle exists.
  - Also deploy the win-sequence fix (added 2026-11-18, on the Lenovo): `drivers/auto_dj_engine.py` stops the "quiz done, advance early" while a win sequence or intermission is running, so a round win on a song's last question no longer pulls in the next song and a frozen question.
  - Also deploy the accented-letter fix (added 2026-11-18, on the Lenovo): `graphics/text_render.py` draws ö/ü/é etc. as o/u/e on the LED panels and TV board (was blank, so "Mötley Crüe" showed as "M tley Cr e"). Also in this batch: `config.py`/`web/remote_server.py` lobby polls marked once ("Guilty pleasure?" shows once per app run).
  - Also deploy the intermission "Next game begins after this song" pulse (added 2026-11-18, on the Lenovo): `web/remote_server.py` (`intermission_finishing`) and `web/static/play.html`.
  - Also deploy team reclaim + host merge (added 2026-11-18, on the Lenovo; the server part needs a restart): the phone sign-up screen lists teams nobody is using ("Already playing tonight? Tap your team") and offers a same-name team back; the admin page has a "Teams (merge / remove)" panel. Files: `config.py` (`RECLAIM_AWAY_SECONDS`), `web/remote_server.py` (`/api/player/unclaimed`, `/api/player/claim`, `/api/teams`, `/api/teams/merge`, `/api/teams/remove`), `web/static/play.html`, `web/static/index.html`.
  - Also deploy the practice-TV star-filter flares (added 2026-11-18, on the Lenovo; practice board only, the rig never renders the board): `graphics/rig_view.py` (flares over bright marquee bulbs, plus the JOIN strip no longer clipped by the label) and `config.py` (`RIG_FLARES_*`). Board process only: kill `graphics.rig_view` and the app respawns it, no app restart. Rules (2026-11-18): random twinkles on song screens only; none while a question is up, except an even sweep in the white wash when it opens. The exact "question up" flag comes from `graphics/rig_feed.py` (needs an app restart; until then the board infers it from the steady-white outline strip). Also in this batch: `graphics/love_theme.py` and `graphics/technical_dreams.py` no longer deal the star animations (moon-and-stars, starfield). Tune or switch off with `RIG_FLARES_*`.
  - Optional: `drivers/audio_stream_bridge.py` got a `pactl` fallback for the phone "Listen to the show" toggle (2026-11-16, fixes the Lenovo, which has no `wpctl`). Harmless on the rig; copy it or leave it. After deploying, tick "Listen to the show" on a phone while a song plays to confirm the rig still streams.

## Bigger ideas (wider scope)

- [ ] **Persistent team identity across restarts, nights and venues** (added 2026-11-18, from the Hurex stranded-profile incident).
  - Why: a phone keeps its team id in `localStorage` per web address, so every tunnel-address change (any app restart) strands every phone. Reclaim + host merge (done 2026-11-18) are the emergency net; this is the real fix and the base for returning-player features.
  - Step 1, survive address changes: make the `triviacade.com/play/` redirect a small page that keeps the phone's id on `triviacade.com` (which never changes) and passes it to the current tunnel as `?pid=`; the tunnel page writes it back through a hidden `triviacade.com` iframe/postMessage. Failover's `?pid=` hand-off already exists (`play.html`, `_lib.php`).
  - Step 2, stable address: a named Cloudflare tunnel on a domain moved to Cloudflare DNS keeps one address across restarts, so saved identities just survive. It also fixes the admin-address problem and suits the multi-rig plan.
  - Step 3, wider features on the same id: welcome-back greeting ("4th visit, best finish 2nd"), season/monthly standings fed by `results/<id>.json`, cross-venue play, regular badges / loyalty perks, lifetime stats, host "who's here tonight". Opt-in only: team name, emoji and a random id; no phone number or email unless the player adds one.
