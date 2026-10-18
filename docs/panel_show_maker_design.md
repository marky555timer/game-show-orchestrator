# Panel Show Maker -- design decisions (locked)

Set up and start a show from the arcade buttons and LED panels alone, no phone.
Decisions from Mark's sketches and Q&A, 2026-10-16/17. Open items are marked
**OPEN**.

**Built 2026-10-17** (Mark: "begin"): drivers/panel_menu.py + graphics/menu_pages.py
(menu), drivers/show_library.py (shows.json), drivers/show_runner.py (playback /
recording), drivers/show_bed.py (intro loop, wheel theme, stabs),
drivers/show_engine.py (rewritten), web/static/designer.html (+ /api/shows/*).
Build choices not spelled out above/below:
- "Verified decade" = the song has a decade tag (Mark: the auto-tagging handles
  it); a decade set by hand on the designer page counts too.
- Network page shows at least 3s; blue = SKIP. Arcade button LEDs light for the
  buttons that have an action on the current page.
- The wheel spins for one pass of wheelTheme.wav (5.2s) and always stops by
  itself for now; a spin whose song's decade isn't on the 60s-90s wheel is
  skipped (the designer page warns).
- Intro NO: no theme -- straight to the wheel (if the playlist opens with a
  spin) or the first song.
- Hands-off with no saves: a default show is made (SAVED AS blinks), then runs.
- Encore after the background music is jukebox style (blue = TRIVIA
  QUESTION?) and is not recorded. Admin remote: BACK TO SHOW MENU = full reset.
- The web Setup page now picks a saved show; its game settings save into it.

## Screens

- **Pages show on LED panels 1+2 only** (top row), one page at a time.
- **Panels 3-6 show only button actions**, each tinted its button's colour
  (green / red / yellow / blue), in the same 2x2 layout as the buttons. A
  button with no action on a page shows a blank panel.

## Boot sequence

1. **Network page** -- replaces today's Setup screen entirely (the AUTO
   countdown, DMX/joystick status chips, "START SHOW NOW?" confirm, blue =
   gamepad reconnect, red = start now, and the unattended jukebox autoplay all
   go away -- no remnants). Shows WiFi network name, IP address, tunnel status.
   Moves on the moment all three are good, or after 30s regardless.
2. **Load Show** -- only if saved shows exist. Banner: `LOAD SHOW` / show name.
   Buttons: `<` / `>` browse saves (`<` blank on the first one), `NEW` = go to
   the designer, `NEXT` = run this saved show straight away (no designer pages).
   List sorted most recently played first.
3. **Designer pages**: INTRO? `<YES>` -> QUESTIONS `<NO>` -> LOW DECADE `<50s>`
   -> HIGH DECADE `<1980s>` -> START (press blue).
4. **Blink** `SAVED AS <NAME>` x4, then -- only if the network is still down --
   `NETWORK STILL DOWN` x2, then hold **BLACK**.
5. **BLACK -> show**: auto-advances into the show only if the boot has been
   hands-off; otherwise waits for any button.

## Buttons (menu only)

- Green / Red = change the value on the page (`<` / `>`).
- Yellow / Blue = previous / next page.
- Long press = 1 second, acts on release (never also fires the short press).
  - Yellow long = back to page 1.
  - Blue long = GO NOW (start with current settings).
  - Green long = diagnostic screen (network info + boards / DMX / joystick).
- The menu owns the buttons while it is up. **No long-press or menu action
  ever fires during a show.**

## Hands-off rule

- "Touched" = any physical button press, or the admin logged in on the
  remote. Player phones connecting do NOT count.
- Untouched for 90s from boot -> go straight into the **last played** saved
  show. The point: never feel rushed to log in before doing granular setup.
- No saved show exists yet -> build one from the designer defaults, name it,
  and run it (recorded as it plays), so "last saved show" always exists after
  the first boot.

## Saved shows

- Name: random adjective + noun, max 10 characters, re-rolled if taken.
  Renamable on the admin page (`Show name: [RedLime]`).
- Loading and running a show keeps its name. Changing any designer setting
  makes a new show with a new name.
- Mid-show deviations (early skips, off-script songs) are ignored -- the save
  stays exactly as recorded.
- A song in a save whose file has gone missing is skipped, not stalled on.
- Saves are managed on the Show Designer web page (below).

## Show Designer web page (playlist maker) -- round 2, 2026-10-17

- **Desktop-first page.** Phone layout is not a goal (playlist building on a
  phone is too fidgety).
- **One saved-show format.** A saved show = an ordered playlist. Shows recorded
  from the panel appear here as editable playlists; the panel's Load Show list
  shows these same playlists.
- **Load / New / Save**; edits autosave in place (AJAX, like the other pages)
  and keep the show's name. **Duplicate** button makes a variant with a new
  name. (Panel-designer changes still always make a new name.)
- **Rename** (`Show name: [RedLime]`), **Delete** with a confirm. List sorted
  newest first. No cap.
- **Each song entry** holds the song plus its ending: play full / advance after
  quiz / cut at mm:ss. Recorded rehearsals fill these in and they stay editable.
- **Insert wheel spin**: a spin item between songs, rigged to land on the decade
  of the song right after it.
- **Songs without a verified date**: shown greyed out in the song picker, with
  quick decade chips (50s / 60s / 70s / 80s / 90s / 00s ...) to tag the decade
  right there. A decade set by hand counts as verified.
- **Running time**: the page shows a running total from song lengths / cut
  points, to fit a contracted set length.
- **Playlist runs out -> the show stops** (outro). Encore = the secondary
  button press after the outro (as discussed).

## Intro + music bed (round 2)

- The intro wheel is simply a wheel-spin item at the top of the playlist.
- **Operator present** (boot was touched): `introTheme.wav` plays long enough
  for the host's intro; a button press then starts the wheel (if the playlist
  opens with a spin) or the first song. Never auto-advances.
- **Hands-off boot**: auto-advance is allowed; the intro theme stops after
  about 10 seconds.
- **With a wheel**: introTheme -> `wheelTheme.wav` while it spins -> when it
  stops, a decade stab, e.g. `80s-v1.wav` (root name = the decade; any number
  of variants `-v1`, `-v2` ...; pick at random but never the one played last;
  a single file just plays). The next song starts **1 second before the stab
  ends**.
- **No wheel**: `LetsPlayStab.wav`, then dovetail into the first song, using
  the same rule (song starts 1s before the stab ends).

### Round 3 answers (2026-10-17)

- **Intro loop (hard-coded for now)**: introTheme loops from 15.549s (loop in)
  to 41.875s (loop out), ducked to 40% under the host. When we go to the
  music, it comes back up to full level fast.
  **OPEN:** the introTheme.wav on disk (Oct 17 09:18) is only 38.972s long,
  so the 41.875s loop-out point is past the end of the file. Needs a re-export
  or corrected numbers.
- **wheelTheme.wav**: made by Mark (5.226s). It loops while the wheel spins
  and cuts hard on the decade stab.
- **Decade stabs**: they live in `audio/gameMusic/` (first one: `80s-v1.wav`,
  6.611s). Root names are `50s` `60s` `70s` `80s` `90s` `2000s`.
- **Hands-off boot**: after about 10s of intro, the wheel appears and
  **stops on its own** (more interesting than skipping it).
- **introEndStab.wav is retired.**
- **Decade chips**: no "unsure / skip" chip; a song must be given a decade.
- **Display rule (general)**: clear the panel buffer before any wipe-in, so
  nothing stale shows through. Seen: "READY FOR DECK 1" -- the placeholder
  title in state.py (deck1_track / deck2_track defaults).

## Decades

- The LOW/HIGH range applies to the whole night's music.
- LOW can never pass HIGH (the values push each other).
- Songs without a verified release date are NOT played in a show.
  **OPEN:** today's tags don't record whether a decade is verified -- see notes.

## After the show (outro)

- The rig becomes a background music player: easy-listening, low-energy songs.
- A panel button press then means someone wants to play: pick the energy up
  and start a new on-the-fly show (like today's behaviour). It does NOT return
  to the Load Show / Network start pages.
- Full reset lives on the admin remote (being redesigned -- Mark will sketch).

## Not handled, on purpose

- Power-loss recovery: the whole rig is battery powered and a drop-out would
  end the night anyway.

## Later (not this phase)

- Wheel redesign: the "wheel" likely becomes a **slot machine** (Mark,
  2026-10-17) -- any number of decades; appears every three songs already
  spinning; a button press stops it; new sound effects. Until then the
  four-slot wheel (60s-90s) is the limit.
- Admin remote redesign.

## Revision 2026-10-17 (after first evaluation)

- Panels 3-6 show plain words instead of `<` / `>`: LOAD SHOW green PREV / red MORE;
  INTRO? and QUESTIONS green YES / red NO; decades green OLDER / red NEWER. Value lines
  lose their arrows (`90s`, `YES`).
- A button with nothing to do stays dark with its light off: PREV on the first show, MORE
  on the last, YES while YES is chosen (and NO while NO is), OLDER at the 1950s, NEWER at
  the 2020s.
- LOW pushes HIGH up; HIGH stops at LOW (OLDER goes dark there) instead of pushing LOW down.
- During playback with questions off, blue -> "TRIVIA QUESTION?" -> green YES turns them on
  (existing jukebox path, confirmed working inside a saved show).
