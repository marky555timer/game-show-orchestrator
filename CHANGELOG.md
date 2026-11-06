# Changelog

Versioning: `0.MINOR.PATCH` until the first paid gig, which is released as `1.0.0`.
MINOR = new mode/feature/subsystem, PATCH = fixes and tuning.
Source of truth: the `VERSION` file.

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
