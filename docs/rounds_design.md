# Rounds, game types and the night's progression -- design (approved 2026-11-19)

**Build status:** step 1 (round model, trivia rounds, admin Rounds card) and step 2 (Show Designer round list with goal / songs / wheel-opening, song-coverage warning) built 2026-11-19; step 3 ("Wait, how much?" = one local-bank price question per song, N questions; Fastest Finger = one Dance Medley of N clips with its TRUE/FALSE game, falls back to trivia if no medley is possible; both have no goal, most points wins; per-song "Who is this?" questions are off in them) built 2026-11-20; step 4 (drivers/solo_challenge.py: admin team + game picker, all or nothing +/-1500 on the night score floored at 0, phone banner, Simon wins at 10 cleared rounds; launchable any idle moment, and a `solo_challenge` round ends on its result) built 2026-11-20. Test feedback 2026-11-20: Fastest Finger intro (board wipe, fastestFinger.wav, outer-edge chase, WHO HAS / THE FASTEST / FINGER? on cue, holds for red / NEXT TRACK / 10 s), winner announced before the next song; "Wait, how much?" = up to 10 price questions back to back on ONE held song, first to the goal or most points, first-right bonus; night totals on phones at intermission. Step 5 Cliffhanger built 2026-11-20 (drivers/cliffhanger_engine.py; host types the guess, year pre-filled from track_cache.json and editable; 10 spaces stretched over the art's 0-25 ruler so over 10 = off the cliff edge; TV art animated in graphics/rig_view.py from cliffhanger_bg.png + cliffhanger_climber.png cut from the original; LED panels show song/guess/year/total; marquee chases while climbing / on a win, dark on a fall; DMX not wired). Revised 2026-11-20: 25 spaces on the art's own 0-25 ruler (number dots removed), 5 songs, climb music loops, plain crossfades (no sweepers / announcements), next song starts by itself 4 s after a climb, simple host page /cliff (year box, SUBMIT, BACK; never shows the answer). "Wait, how much?" round (revised again same day): the game music is a ~5 s stinger per question, then the playlist song comes back; the playlist plays on (songs change only at their end, plain crossfade, no sweepers / announcements / wheel spins); strobe intro only on the first question; phones show "Question N of 10 coming up" between questions. The round opens with its own intro like Fastest Finger: board wipe, waitHowMuch.wav, outer-edge chase, WAIT, / HOW / MUCH? on cue (config.WHM_INTRO_WORDS, times estimated from the wav), same red / NEXT TRACK / 10 s hold; it replaces the first question's strobe and stinger. 2026-11-21: "Wait, how much?" price answers pay 3x (300 quick -> 75) so the round can be won in ~10 questions; Cliffhanger shows the song + artist on the answer screen and on /cliff (never the year); decades are declared per round, not per show (round "decade": its wheel spin lands there, its songs come from it, other-decade playlist songs are skipped, library fills in; a show without rounds keeps its old LOW / HIGH range). 2026-11-22: the designer offers Tipsy Simon and Cliffhanger as their own round types (`simon` / `cliffhanger`, run as a `solo_challenge` round with state.round_solo_game fixed; old `solo_challenge` rounds still let the host pick). A Cliffhanger round opens straight into a pregame: "Cliffhanger!" alone on panels 1+2 with a white movie chase on the title's marquee bulbs (rest dark; the TV board shows the same), and the admin page jumps to /cliff's pregame panel -- FADE MUSIC (volume to 1/4) / RAISE VOLUME (back), team (lowest night score first, preselected, points shown) and BEGIN GAME (volume back, the first quiz song plays, WHAT / YEAR / DID / THIS / SONG / DEBUT? wipes across the six panels for 8 s, /cliff turns into the guess screen; the TV holds the board 4 s, then fades to the mountain). "Wait, how much?": with a round decade, a new song from it crossfades in as the jingle ends; a NEXT TRACK during the round is a seamless crossfade (no GET READY / VO / sweeper / board, marquee or DMX transition), like its end-of-song changes. The night ending (last round done, outro, after-show music) stops every question; phones keep Final Scores + the share link through the after-show music. All on the Lenovo only. The triviacade.com web designer copy is not republished yet (it doesn't know rounds; a sync from it keeps the local round list).

Decisions are from Mark (2026-11-19); **OPEN** marks what still needs an answer.

## The idea

A night is an ordered list of **rounds** (not one endless trivia game). Each round has a type, its own
goal/length, its own winner, and ends in an **intermission** before the next round. Points are tracked
twice: **round score** (resets each round) and **night score** (accumulates; ALL rounds count).

## Round types

| Type | What it is | Ends when | Winner |
|---|---|---|---|
| `trivia` (standard) | Today's music-trivia game. Optional wheel spin opens it ("Spin the Wheel": one decade spin to get people thinking; no more spins until the round ends). | someone reaches the goal (default 1500) **or** the round's songs run out | goal reached = that team; songs ran out = highest raw round points |
| `wait_how_much` | Existing Price Game ("Wait, how much?") as its own round | its question count | highest round points |
| `fastest_finger` | Existing Dance Medley mode, scripted in as a round | its clip count | highest round points |
| `solo_challenge` (Simon, Cliffhanger, later Space Invaders) | One player takes over the device for their team. **All or nothing**: win = +1500 to the team's night score, lose = -1500. Typically played late in the night by a "desperate" player. | the challenge ends | the challenging team (win or lose) |

- "Spin the Wheel" is **not** its own round type: it is the `wheel_opening` option on a trivia round.
- Space Invaders is built but is **left out of the default rotation**; the solo-challenge type leaves the door open (a new challenge is one more entry in a registry).

## Trivia round length and the song safety margin

A trivia round needs enough songs to reach the goal. Plan: estimate songs needed from the goal and the
typical points per question, add **3-5 spare songs**, and queue that many. If the queue still runs out first, the
round is called: round end -> intermission -> next round, winner on raw round points.
(Estimate formula to be tuned from real nights; the 3-5 is a setting, default 4.)

## Scoring model

- Per team: `round_score` (reset at each round start), `night_score` (never reset).
- Every point earned goes to both. Round wins/solo results post to `night_score` directly.
- Solo challenge: team picked by the host (below). Win: `night_score += 1500`. Loss: `night_score -= 1500`
  A night score **floors at 0** (decided).
- Phones show: the round leaderboard during a round, the night total in the recap and at the end; the
  round winner is declared at each round's end (board + phones + intermission screen).
- The existing per-team stats (correct/asked, streak, results page) keep working; the results archive gains a per-round breakdown.

## Where it lives

- **Show Designer / saved shows** get an ordered **round list** per show: type + options (goal, wheel opening yes/no, question count, safety songs, decade range). Saved with the show (`shows.json`), editable on the designer page; the panel menu runs a show's rounds in order.
- Existing show settings (win score, intermission minutes, decade range, exclusions) become per-round options with show-level defaults.
- Admin page: a "Rounds" card -- current round, next round, **START NEXT ROUND**, **SKIP / END ROUND**, and the solo-challenge launcher.
- Intermission already exists (5 min, music + stats screens); it now also announces the round winner and names the next round.

## Solo challenge: host team picker

A simple admin screen: big team buttons (names/avatars/scores), tap the team the player represents, confirm
the stakes ("+/-1500"), then the game starts. Phones show "<Team> is taking the challenge!" and the result.

### Simon challenge
Existing Simon engine, player-controlled on the panel buttons. **Win = reach round 10** -> +1500, else -1500
(fail ends it at once).

### Cliffhanger challenge (Price-Is-Right style)
- Three songs. Each: the player hears the song and guesses its **year** (host enters the guess; year comes from the song's tag).
- The climber moves up **1 space per year wrong**, cumulative across the three songs, **10 spaces total**.
  The climber only moves up.
- **Total years off over 10 -> he falls** (lose, -1500). **10 or fewer after the third song -> win** (+1500). Years off add up across the three songs (decided). Stakes are 1500 either way (decided).
- Physical game first. For the HDMI board the art in `graphics/art/cliff_hangers_by_wheelgenius_decxya5-fullview.png`
  is animated: the climber starts at the ruler's 0 and walks up the yellow ruler (marks 0-10), about **2 s per year** of movement, easing between marks.
- Sound: **`gameMusic/cliffhanger.wav` plays while the climber is moving up** (Mark, 2026-11-19: that is the climbing song; `yodel.wav` is not used); on a fall **`sound_effects/wilhelm.wav`**
  (climber tumbles off the slope); on a win **`sound_effects/bigWin.wav`**. (Keep `cliffhanger.wav` in `audio/gameMusic/` -- it is the game's music.)
- LED panels show the year guessed / correct year / spaces moved / total so far; marquee + DMX celebrate or go dark on a fall.

## Attention Shoppers (DROPPED 2026-11-20 -- kept for reference only)

- Songs carry a new tag: **`elevator_of`** = the original song (title + artist). Covers sit in the normal library (`music_metadata.csv` gets the column; the File Manager gets an Elevator filter/edit).
- Questions: **first = the song name** (choices are song titles), then the artist; later questions reuse trivia already written for the **original** song.
- Never in normal trivia rotation (an elevator cover would give away / confuse the original); only inside this round type.
- First batch is about **10 songs** and none exist yet: a spreadsheet column (`elevator_of`) plus a small edit field in the File Manager is enough; no big tagging tool.

## Build order (each step shippable on its own)

1. **Round model**: `state` round fields, per-team round/night scores, round list in a show, round end -> intermission -> next, winner declaration. Trivia only at first.
2. **Admin "Rounds" card + designer round list.**
3. **Fastest Finger + Wait How Much scripted in** as rounds (their engines already exist).
4. **Solo-challenge shell**: team picker, +/-1500 stakes, phone messaging. Simon as the first challenge.
5. **Cliffhanger** (engine, HDMI animation, sounds, LED/marquee/DMX).
6. ~~Attention Shoppers~~ -- dropped 2026-11-20 (operator: it's the same game as "Wait, how much?").
7. Space Invaders registered as a challenge (later, when you decide).

## Open questions

- None blocking. Assumed (say if wrong): the host enters the year guess on the admin page; the three Cliffhanger song years come from the songs' year tags (songs without a year tag are skipped).
