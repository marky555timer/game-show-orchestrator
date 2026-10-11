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
