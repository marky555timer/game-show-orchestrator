# Changelog

Versioning: `0.MINOR.PATCH` until the first paid gig, which is released as `1.0.0`.
MINOR = new mode/feature/subsystem, PATCH = fixes and tuning.
Source of truth: the `VERSION` file.

## 0.11.0 - 2026-11-12

- Practice rig: a computer with no game board attached rehearses the show on
  an HDMI TV. `graphics/rig_view.py` draws the whole board full-screen -- the
  six LED panels, the 102 marquee bulbs at their measured places, the outline
  strip (an approximation: WLED's own effects run on the real board) and the
  four button lights -- in its own process, fed by `graphics/rig_feed.py`.
  Switched on by `practice_rig.json` (not in git). Runs on a Pi 3 (720p) and
  on a Lenovo M700 Tiny with Debian + Xfce (1080p); see pi_deploy/README.md.
- Remote: new Arcade Buttons page (`/buttons`) presses, holds and combines
  the four panel buttons from a phone, and shows which button lights are lit.
- `start.sh` also runs on an X11 desktop: no screen blanking, and sound goes
  to the HDMI/DisplayPort output that has a screen on it.
- Decode worker frees each decoded song after handing it over (~120 MB less
  memory held between tracks).

## 0.10.0 - 2026-11-11

- Backup-Pi failover: both Pis check in with triviacade.com/play/failover.php,
  which tracks which one owns the show and its latest position (show, song,
  players, scores, round, questions asked). The backup's remote shows the
  owner's position and a TAKE CONTROL button that restores players/scores and
  resumes on the next song; a Pi that loses ownership stands down. Role comes
  from `failover_role.json` (not in git). No internet = no change.
- Auto-advance linger slider on the remote: wait 0-100% of the time left in
  the song after the quiz finishes before advancing (saved across restarts).
- New show now forgets asked artists/questions, so a restarted show no longer
  skips the "Who is this?" teaser or reuses last run's question queue.

## 0.9.1 - 2026-11-06

- Marquee "awaiting answer" look: clear brightness pop on the first beat of
  each bar (track tempo), plus a white diagonal glint across all four answer
  panels together every 4 bars.
- USB dropout fix: panel + marquee links ride out write stalls up to 3s,
  dead ports close off the main loop (was a 5s freeze), each board's proven
  USB socket is remembered so no driver grabs another's board, and stall logs
  note the last relay switch (strobe correlation).

## 0.9.0 - 2026-11-06

First tagged release. Show rig running end to end on the Pi.

- Game modes: trivia, jukebox, Dance Medley (beat-matched hooks, vocal-drop ending,
  "This is <artist>" round), Simon, Oz story song with six-panel tornado.
- Panel Show Maker + saved shows, web Show Designer, question audit page.
- Lighting: marquee, outline/accent WLED presets, DMX moving-head "robot face",
  strobe, honk (duck call), green-light engine.
- Hardware: kill/blackout buttons, relay board, three-board USB-serial identification,
  clock guard (NTP step after boot), net-health engine.
- Panel themes: Technical Dreams, Love, 50s-90s decades, Dark; hook words on panels 3-6.
- Add Tracks round 1 + round 2 tooling (levelling, tags, cues, questions, hook words).
- Infra: version stamp (`VERSION`, `config.APP_VERSION`, logged at boot),
  generated caches and OCR cache removed from git.
