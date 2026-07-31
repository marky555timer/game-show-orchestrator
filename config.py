import os

# ==========================================
# HARDWARE PORTS & CANVAS SETTINGS
# ==========================================
ENTTEC_PORT = "COM5"
MIDI_PORT_NAME = "Python_PMC_Port"

# ==========================================
# PHYSICAL PANEL LAYOUT
# ==========================================
# 6 independent 32x16 LED panels: 2 on top (combined into one 64x16
# logical surface), 4 spaced evenly underneath (128x16), centered under
# the top pair. Panel IDs match the physical button numbering used by
# the game-show console (Btn1..Btn6).
PANEL_W = 32
PANEL_H = 16
ROW_GAP = 4  # simulator-only visual seam between the two rows

BOTTOM_ROW_WIDTH = PANEL_W * 4
TOP_ROW_WIDTH = PANEL_W * 2
TOP_ROW_X_OFFSET = (BOTTOM_ROW_WIDTH - TOP_ROW_WIDTH) // 2
BOTTOM_ROW_Y = PANEL_H + ROW_GAP

PANELS = {
    1: (TOP_ROW_X_OFFSET, 0, PANEL_W, PANEL_H),
    2: (TOP_ROW_X_OFFSET + PANEL_W, 0, PANEL_W, PANEL_H),
    3: (0, BOTTOM_ROW_Y, PANEL_W, PANEL_H),
    4: (PANEL_W, BOTTOM_ROW_Y, PANEL_W, PANEL_H),
    5: (PANEL_W * 2, BOTTOM_ROW_Y, PANEL_W, PANEL_H),
    6: (PANEL_W * 3, BOTTOM_ROW_Y, PANEL_W, PANEL_H),
}

# Panels 1+2 treated as a single logical 64x16 surface for headline text.
TOP_COMBINED = (TOP_ROW_X_OFFSET, 0, TOP_ROW_WIDTH, PANEL_H)

# Bottom answer/animation panels, left-to-right.
BOTTOM_PANELS = [PANELS[3], PANELS[4], PANELS[5], PANELS[6]]

MATRIX_WIDTH = BOTTOM_ROW_WIDTH
MATRIX_HEIGHT = BOTTOM_ROW_Y + PANEL_H
# Dev-machine simulator window scale. Knocked down to ~1/3 of the original
# 10px-per-LED size (still an integer so the LED grid stays crisp) so the
# virtual matrix window doesn't dominate the dev display.
PIXEL_SCALE = 3
GAP = 1

WINDOW_W = MATRIX_WIDTH * PIXEL_SCALE
WINDOW_H = MATRIX_HEIGHT * PIXEL_SCALE

# ==========================================
# SCROLLING / MARQUEE TEXT ENGINE
# ==========================================
SCROLL_SPEED_PX_PER_SEC = 15.0
SCROLL_PAUSE_SECONDS = 1.0

# How long each "page" stays up on the DJ-mode top display before
# cycling to the next one.
TOP_CYCLE_TRACK_SECONDS = 7.0
TOP_CYCLE_FACTOID_SECONDS = 6.0

# Panels 1+2 are driven as one logical 64x16 surface dedicated to the
# artist/track headline -- they are never dealt an idle animation (only
# panels 3-6 are), so the pair always works together for maximum text width.
#
# Set this True to let the top pair ALSO cycle to a "did you know" factoid
# page every TOP_CYCLE_FACTOID_SECONDS. False keeps artist/track up
# permanently, which is what the live rig asked for -- flip it back to True
# if you'd rather have the factoid share the top display again.
TOP_SHOW_FACTOID_PAGE = False

# How long a volume adjustment holds panel 6's overlay before it
# rejoins the idle animation rotation.
VOLUME_OVERLAY_HOLD_SECONDS = 2.0

# How long panel 3's AI-pipeline status indicator (star burst on success,
# dancing cat on failure) holds after the status changes. Once it expires,
# panel 3 rejoins the random idle animation deal so all four bottom panels
# stay lively. The failure reason is also echoed loudly to the console, so
# nothing is lost when the indicator steps aside.
STATUS_PANEL_HOLD_SECONDS = 4.0

# TV-remote-style hold-to-repeat: holding the volume control fires one
# immediate step, waits INITIAL_DELAY, then keeps stepping every
# REPEAT_INTERVAL seconds until released.
VOLUME_HOLD_INITIAL_DELAY_SECONDS = 0.35
VOLUME_HOLD_REPEAT_INTERVAL_SECONDS = 0.10

# ==========================================
# AI TITLE CLEANUP (background OCR polish)
# ==========================================
# Set this in your environment before launching, e.g. (PowerShell):
#   $env:ANTHROPIC_API_KEY = "sk-ant-..."
# Never hardcode the key here directly.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# Runtime features (title cleanup, factoid/quiz generation) are cost-gated to
# Haiku -- this app never spends Sonnet-tier tokens on its own background
# calls. claude-3-5-haiku-20241022 was retired 2026-02-19; claude-haiku-4-5
# is its direct replacement.
AI_CLEANUP_MODEL = "claude-haiku-4-5"

# Master on/off switch. If False, the background AI cleanup worker never
# starts and everything falls back to the existing regex/XML sanitizer only.
AI_CLEANUP_ENABLED = bool(ANTHROPIC_API_KEY)

# Minimum seconds between AI cleanup requests, PER DECK. Prevents spamming
# the API during a fast-mixing set where OCR text is changing rapidly.
AI_CLEANUP_MIN_GAP_SECONDS = 4.0

# Hard timeout per API call so a slow/hung request can never back up the
# cleanup queue or bleed into the next track's cleanup attempt.
AI_CLEANUP_TIMEOUT_SECONDS = 2.5

# Where the on-disk cleanup cache lives (raw OCR string -> cleaned title/artist).
# Persists across runs so a track you've already cleaned up once doesn't cost
# another API call the next time it's cued up.
AI_CLEANUP_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_cleanup_cache.json")

# To force-clear the cache for testing, set this env var to match before
# launching, e.g. (PowerShell):
#   $env:CLEAR_AI_CACHE = "wipe-it"
# Change the string below to whatever secret you want.
AI_CLEANUP_CACHE_CLEAR_SECRET = "wipe-it"

# ==========================================
# AI TRACK FACTOIDS + QUIZ CONTENT
# ==========================================
# Reuses the same Anthropic API key/model as the title cleanup above -- both
# are pinned to AI_CLEANUP_MODEL (Haiku). Runtime question generation never
# spends Sonnet/Opus tokens.
FACTOID_AI_ENABLED = AI_CLEANUP_ENABLED
FACTOID_TIMEOUT_SECONDS = 6.0

# Negative-result cache TTLs, so a transient network hiccup can be
# retried later but an AI "I don't actually know this song" verdict
# doesn't get re-asked every time the track comes back up.
FACTOID_FAILURE_RETRY_SECONDS = 300        # network/timeout/parse errors
FACTOID_UNKNOWN_RETRY_SECONDS = 86400      # AI explicitly wasn't confident

# ==========================================
# TRACK QUESTION PRE-FETCH QUEUE (3-per-track background buffer)
# ==========================================
# As soon as a deck's track is confidently identified (no Btn6 press
# required), drivers/factoid_engine.py background-fetches quiz questions for
# it, one Haiku call at a time, until the local cache holds this many
# distinct questions. Once full, the cache is never re-queried for that
# track (replays cost nothing) until a question is consumed by gameplay.
TRACK_QUESTIONS_PER_TRACK = 3
TRACK_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "track_cache.json")

# ==========================================
# COLOR PALETTE
# ==========================================
RED_FULL = (255, 0, 0)
RED_DIM = (120, 0, 0)
RED_OFF = (20, 0, 0)
BLACK = (0, 0, 0)

# ==========================================
# QUIZ MODE: SELECT-THEN-GRADE FLOW
# ==========================================
# The physical LED matrix panels are red-only hardware -- unlike the DMX
# RGB par can (below), they cannot reproduce green/blue. All matrix-side
# quiz states (armed/winning/wrong/reveal) use RED_FULL/RED_DIM/BLACK
# only, distinguished by brightness and motion (pulsing/blinking)
# instead of hue.

# NOTE: Fixture 1 no longer changes color on answer-select (retired in favor
# of a strict win/pulsing-green -> loss/solid-red -> reset/black indicator
# lamp) -- "armed" feedback is now shown purely on the LED matrix panel via
# _draw_selected_panel(). See drivers/lighting_engine.py.

# Multi-question game loop: total seconds the scorecard (win/loss
# celebration + "SCORE: X/Y" page) holds after grading before either
# auto-advancing to the next pre-fetched question for the same track (if
# one is queued) or returning to DJ_MODE.
GAME_SCORECARD_HOLD_SECONDS = 5.0

# How long of that window is the win/loss celebration (matrix pulse + DMX)
# before it switches to the "SCORE: X/Y" stats page. The remainder of
# GAME_SCORECARD_HOLD_SECONDS is the stats page.
QUIZ_CELEBRATION_HOLD_SECONDS = 2.5
QUIZ_STATS_HOLD_SECONDS = GAME_SCORECARD_HOLD_SECONDS - QUIZ_CELEBRATION_HOLD_SECONDS

# ==========================================
# GAMEPAD BUTTON DEBOUNCE (Btns 5-8)
# ==========================================
BUTTON_DEBOUNCE_SECONDS = 0.15

# Btn6 (quiz-gate fetch trigger) launches an API call and plays audio on
# every accepted press -- a wider guard than the other buttons so a single
# physical press can never stack duplicate fetches/sounds.
QUIZ_GATE_DEBOUNCE_SECONDS = 0.4

# Btn6 now pulls instantly from the pre-fetched track_cache.json queue --
# no network call happens at press time. This timeout only covers the
# cold-start case (brand new track, cache still filling, or no internet): if
# the queue is still empty this many seconds after the press, force a local
# fallback_questions.json question in and enter GAME_MODE anyway so the DJ is
# never left hanging.
QUIZ_GATE_EMPTY_CACHE_TIMEOUT_SECONDS = 10.0

# ==========================================
# DMX: 11-FIXTURE / 176-CHANNEL RIG
# ==========================================
# Fixture 1 (ch 1-16): GenericRGB Par, WIN/LOSS indicator lamp only.
# Fixtures 2-11 (16ch each): Rockville MINIRF4 V2 uplighting.
DMX_FIXTURE_CHANNELS = 16
DMX_NUM_FIXTURES = 11
DMX_TOTAL_CHANNELS = DMX_FIXTURE_CHANNELS * DMX_NUM_FIXTURES  # 176

# Tap-tempo: rolling window of taps used to compute the period, and the
# sane clamp range so a mis-tap can't produce a silly-fast/slow oscillation.
TEMPO_TAP_WINDOW = 4
TEMPO_PERIOD_MIN_SECONDS = 0.25
TEMPO_PERIOD_MAX_SECONDS = 2.0
TEMPO_PERIOD_DEFAULT_SECONDS = 0.6

# Tempo-tap visual feedback: red flash outline on panels 3-6 decays to 0
# over this long from the moment Btn5 is held.
TEMPO_FLASH_DECAY_SECONDS = 0.1

# ==========================================
# SPACE INVADERS MINI-GAME (DJ-mode dual-button Easter egg)
# ==========================================
# Entered from DJ_MODE by holding/pressing Gamepad Button 1 AND Button 3
# simultaneously (pygame JOYBUTTONDOWN indices 0 and 2); exited immediately
# via Button 7 or Button 8 (indices 6/7). See inputs/gamepad.py for the
# entry/exit + movement/fire input handling,
# drivers/space_invaders_engine.py for the game loop, and
# graphics/matrix_canvas.py::_render_space_invaders for rendering.
#
# The play field spans the FULL matrix canvas (MATRIX_WIDTH x
# MATRIX_HEIGHT, 128x36) rather than being confined to a single 32x16
# panel -- a proper full-width arcade screen reads far better on the rig
# than a game crammed into one panel's worth of pixels.
SI_ENTRY_BUTTONS = (0, 2)   # pygame JOYBUTTONDOWN indices for physical Btn1 + Btn3
SI_EXIT_BUTTONS = (6, 7)    # pygame indices for physical Btn7 / Btn8
SI_ENTRY_SOUND_VOLUME = 1.0

SI_INVADER_COLS = 6
SI_INVADER_ROWS = 3
SI_INVADER_SPACING_X = 16
SI_INVADER_SPACING_Y = 8
SI_INVADER_TOP_Y = 2
SI_INVADER_SIZE = 5                    # px, square sprite footprint
SI_INVADER_STEP_X = 2                  # px per formation step
SI_INVADER_DROP_Y = 3                  # px dropped when the formation hits an edge
SI_INVADER_TICK_START_SECONDS = 0.6    # step interval, full formation alive
SI_INVADER_TICK_MIN_SECONDS = 0.15     # step interval, last invader standing

SI_PLAYER_WIDTH = 5
SI_PLAYER_HEIGHT = 3
SI_PLAYER_Y_FROM_BOTTOM = 3            # px up from the bottom edge
SI_PLAYER_SPEED = 60.0                 # px/sec while held

SI_BULLET_SPEED = 90.0                 # px/sec, travels upward
SI_FIRE_COOLDOWN_SECONDS = 0.25
SI_EXPLOSION_HOLD_SECONDS = 0.2

# Amplified game feedback: the instant an answer is graded, ALL 10 uplight
# fixtures (2-11) snap to a brief solid green/red pulse alongside Fixture 1's
# own win/loss lamp, then fall back to the normal game chase -- see
# inputs/gamepad.py::trigger_big_win/trigger_loss and
# drivers/lighting_engine.py::_render_grade_flash.
DMX_GRADE_FLASH_SECONDS = 0.25

# DJ-mode uplighting: 8 themes + an "ALL LIGHTS OFF" stop in the cycle.
DJ_THEME_COUNT = 8
DJ_THEME_ALL_OFF_INDEX = DJ_THEME_COUNT  # index 8 == all lights off

# DJ-mode main theme color palette, cycled by Btn7.
DJ_COLOR_PALETTE = [
    (255, 0, 0),      # red
    (255, 120, 0),    # amber
    (255, 255, 0),    # yellow
    (0, 255, 60),      # green
    (0, 180, 255),    # cyan/blue
    (160, 0, 255),    # purple
    (255, 0, 160),    # magenta
]

# Game-mode chase pace (seconds per step across fixtures 2-11).
CHASE_PACE_MID_SECONDS = 0.12
CHASE_PACE_FAST_SECONDS = 0.05
CHASE_PACE_SLOW_SECONDS = 0.28

# ==========================================
# MIDI DECK-SWITCH / CROSSFADER AUTOMATION
# ==========================================
MIDI_CC_DECK1_NEXT = 0x0A  # 10
MIDI_CC_DECK1_BACK = 0x09  # 9
MIDI_CC_DECK2_NEXT = 0x08  # 8
MIDI_CC_DECK2_BACK = 0x07  # 7
MIDI_CC_CROSSFADER = 0x0D  # 13

# Time given for Rekordbox to load the searched track before the crossfader
# starts moving.
TRACK_LOAD_WAIT_SECONDS = 3.0
CROSSFADER_TWEEN_DURATION_SECONDS = 1.5

# ==========================================
# MIDI DECK-START SEQUENCE (Cue -> Play/Pause) -- Python_PMC_Port note map
# ==========================================
# Bug fix: TrackSearch (Next/Prev) can silently fail when the deck it's
# being sent to is paused/unstarted. drivers/deck_orchestrator.py primes
# the target deck with a strict Cue -> tick -> Play/Pause Note On/Off
# sequence on these PMC ports (Deck 1 on MIDI channel 1 / status 0x90,
# Deck 2 on channel 2 / status 0x91) before sending TrackSearch, so the
# deck is always in a responsive state.
#   Deck 1 Cue:        900C  (status 0x90, note 0x0C)
#   Deck 1 Play/Pause:  900B  (status 0x90, note 0x0B)
#   Deck 2 Cue:        910C  (status 0x91, note 0x0C)
#   Deck 2 Play/Pause:  910B  (status 0x91, note 0x0B)
MIDI_DECK_CUE_NOTE = {1: (0x90, 0x0C), 2: (0x91, 0x0C)}
MIDI_DECK_PLAY_NOTE = {1: (0x90, 0x0B), 2: (0x91, 0x0B)}

# Delay between the Cue send and the Play/Pause send in the deck-start
# sequence (the "small delay / frame tick" the hardware needs to register
# Cue before Play/Pause lands).
DECK_START_SEQUENCE_TICK_MS = 30

# ==========================================
# BRANDING TICKER (DJ mode, bottom panels)
# ==========================================
BRANDING_URL = "https://yannitellphotography.com/wp-content/dj/branding.txt"
BRANDING_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "branding_cache.txt")
BRANDING_FETCH_EVERY_N_DECK_CHANGES = 3
BRANDING_FETCH_TIMEOUT_SECONDS = 4.0
BRANDING_OVERLAY_INTERVAL_SECONDS = 20.0
BRANDING_OVERLAY_DURATION_SECONDS = 5.0

# Bottom row (panels 3-6) treated as one logical wide surface for the
# branding ticker, mirroring TOP_COMBINED for the top pair.
BOTTOM_COMBINED = (0, BOTTOM_ROW_Y, BOTTOM_ROW_WIDTH, PANEL_H)

# ==========================================
# OFFLINE QUIZ FALLBACK
# ==========================================
FALLBACK_QUESTIONS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fallback_questions.json")

# ==========================================
# 70s/80s "PRICE GAME" BONUS ROUND
# ==========================================
# Trigger: the AI-tagged "release_year" on a track's trivia questions (see
# drivers/factoid_engine.py's release_year JSON field) falling in this range
# arms a Btn6 override -- instead of a normal trivia question, Btn6 fires a
# DMX strobe + on-screen banner, then a decade-specific pricing trivia
# question (drivers/price_game_engine.py).
PRICE_GAME_MIN_YEAR = 1970
PRICE_GAME_MAX_YEAR = 1989
PRICE_GAME_STROBE_SECONDS = 1.5
PRICE_GAME_BANNER_SECONDS = 2.0
PRICE_GAME_QUESTION_TIMEOUT_SECONDS = 8.0

# ==========================================
# DJ MODE "MYSTERY BAND" TEASER
# ==========================================
# On a new track by an artist not yet asked about this session, panels 1+2
# hide the title behind a "Who is this?" teaser and panels 3-6 loop a
# question-mark/original-animation cycle for MYSTERY_REVEAL_TIMEOUT_SECONDS.
# If Game Mode isn't entered in that window, the title reveals with a slow
# invert-color blink for MYSTERY_REVEAL_BLINK_SECONDS before standard DJ
# mode resumes. See drivers/mystery_band_engine.py.
MYSTERY_REVEAL_TIMEOUT_SECONDS = 15.0
MYSTERY_QMARK_PHASE_SECONDS = 1.5
MYSTERY_REVEAL_BLINK_SECONDS = 3.0
MYSTERY_REVEAL_BLINK_PERIOD_SECONDS = 0.8

# Question-priority hierarchy for the questions queued after the forced
# "identify this band" question, once Game Mode is entered from a live
# Mystery Band window. Categories not listed here (e.g. career-stat,
# song-meaning) sort after all of these, in their original queue order.
MYSTERY_CATEGORY_PRIORITY = {"geography": 0, "date": 1, "true_false": 2, "real_name": 3}

# ==========================================
# PRICE GAME MODE: BACKGROUND MUSIC + MIDI FADER DUCK
# ==========================================
# On entering the 70s/80s Price Game (drivers/price_game_engine.py), a
# random background bed plays and the DJ controller's channel1/channel2
# faders (MIDI CC#11/CC#12 -- the same CCs handle_dj_volume() already
# drives) smoothly tween to 0%. This is an independent safety cap on top
# of the round's own state machine: whichever comes first -- the round
# naturally returning to DJ mode, an early Btn7 exit, or this timeout --
# fades the music back out and restores the faders to state.music_volume
# (the DJ's last stored volume, 100% by default at launch).
PRICE_GAME_AUDIO_MAX_SECONDS = 45.0
PRICE_GAME_AUDIO_TWEEN_SECONDS = 1.5
GAME_MUSIC_PATHS = ["audio/gameMusic1.wav", "audio/gameMusic2.wav", "audio/gameMusic3.wav"]

# ==========================================
# PRICE GAME MODE: MUSIC PLAYBACK LENGTH RULES
# ==========================================
# The first Price Game of the session plays the full background bed (see
# audio/gameMusic*.wav via play_random_game_music()). Every subsequent
# occurrence this session caps the bed at this many seconds -- it simply
# fades out early; the channel-fader duck/restore lifecycle around it is
# unaffected. See drivers/price_game_engine.py.
PRICE_GAME_MUSIC_REPEAT_CAP_SECONDS = 5.0

# The instant a Price Game question is graded (drivers/price_game_engine.py
# ::end_price_game_audio_on_answer, called from inputs/gamepad.py::
# grade_quiz_selection), the bed fades out fast and the channel faders tween
# back up quickly -- brisker than the normal PRICE_GAME_AUDIO_TWEEN_SECONDS
# duck/restore used on the intro/timeout/abort paths.
PRICE_GAME_BRISK_FADE_MS = 400
PRICE_GAME_BRISK_FADER_TWEEN_SECONDS = 0.6

# ==========================================
# PRICE GAME MODE: PRODUCT CATEGORY ROTATION (Haiku prompt)
# ==========================================
# Rotated round-robin (by session-wide Price Game occurrence count, see
# drivers/price_game_engine.py::start_price_game) so consecutive rounds
# don't keep landing on the same item type (e.g. cosmetics every time).
PRICE_GAME_CATEGORIES = [
    {
        "label": "Household Goods",
        "examples": "laundry detergent, paper towels, dish soap, trash bags, a light bulb",
    },
    {
        "label": "Pantry / Groceries",
        "examples": "a box of cereal, a can of coffee, a gallon of milk, a loaf of bread, a dozen eggs",
    },
    {
        "label": "Entertainment / Electronics",
        "examples": "an LP vinyl record, a blank cassette tape, a blank VHS tape, a Walkman, a pack of batteries",
    },
    {
        "label": "Apparel / Footwear",
        "examples": "a pair of name-brand sneakers, a pair of denim jeans, a t-shirt, a winter coat",
    },
]

# Once a specific product (e.g. "Gallon of Milk") has been asked about in a
# Price Game question, it's excluded from the prompt's candidate pool for
# this many subsequent trivia rounds (state.quiz_score_total-counted, i.e.
# any graded question -- not just Price Game rounds). See
# drivers/price_game_engine.py's product-history cooldown.
PRICE_GAME_PRODUCT_COOLDOWN_ROUNDS = 20

# ==========================================
# AUTO-DJ: TRACK-LENGTH AUTO-ADVANCE (Section 4)
# ==========================================
# Auto-DJ is on by default at launch; Gamepad Btn4 (inputs/gamepad.py,
# JOYBUTTONDOWN index 3, DJ mode only) toggles it. See drivers/auto_dj_engine.py.
AUTODJ_ENABLED_BY_DEFAULT = True

# Used when a track has no usable duration metadata (rekordbox.xml's
# TotalTime attribute, looked up via drivers/rekordbox_driver.py).
AUTODJ_DEFAULT_TRACK_SECONDS = 210.0  # 3.5 minutes

# A TotalTime value below this is treated as bogus/not-a-real-track
# metadata (e.g. a short SFX/jingle asset in the library) rather than
# trusted, and falls back to AUTODJ_DEFAULT_TRACK_SECONDS instead.
AUTODJ_MIN_PLAUSIBLE_DURATION_SECONDS = 30.0

# Auto-advance transition sequence (station announcement + deck-start MIDI
# + TrackSearch) starts arming this many seconds before the calculated/
# fallback end of the track -- see drivers/auto_dj_engine.py::update() and
# the "STATION ANNOUNCEMENT VOICE-OVERS" section below for the rest of the
# overlapping-transition timing.
AUTODJ_PRE_SWITCH_SECONDS = 15.0

# Once the station announcement voice-over starts, the actual track
# transition (deck-start MIDI sequence + TrackSearch) fires this many
# seconds before the announcement clip finishes -- the announcement bridges
# the end of the outgoing track into the start of the next one.
AUTODJ_ANNOUNCE_LEAD_SECONDS = 2.0

# How long the "AUTO ON" / "AUTO OFF" toggle confirmation overlays panel 3.
AUTODJ_TOGGLE_OVERLAY_SECONDS = 1.5

# ==========================================
# AUTO-DJ: PIXEL-SCANNING "DEAD AIR" FAILSAFE
# ==========================================
# Visual safety net in case rekordbox.xml metadata / the track timer both
# fail to catch an ending track: samples an on-screen strip to the right of
# the deck centerlines (the upcoming-track waveform preview). If every pixel
# in that strip is pure black (RGB sum == 0 -- no waveform rendering, i.e.
# nothing queued/loaded), and Auto-DJ hasn't already armed a transition,
# the track transition fires immediately rather than risk dead air. See
# drivers/auto_dj_engine.py::_dead_air_failsafe.
AUTODJ_DEAD_AIR_SCAN_RECT = {"left": 1000, "top": 175, "width": 1, "height": 75}
AUTODJ_DEAD_AIR_SCAN_INTERVAL_SECONDS = 0.25

# ==========================================
# AUTO-DJ: STATION ANNOUNCEMENT VOICE-OVERS
# ==========================================
# Auto-DJ track transitions overlay a randomly-selected station-announcement
# clip from this folder (see audio/audio_engine.py::play_station_announcement).
# A small history buffer avoids repeating the same clip back-to-back.
ANNOUNCEMENTS_DIR = "audio/announcements"
ANNOUNCEMENT_HISTORY_SIZE = 3

# ==========================================
# AUTO-ANNOUNCEMENT TOGGLE (Gamepad Btn1, DJ mode only)
# ==========================================
# On by default at launch; Gamepad Btn1 (inputs/gamepad.py, JOYBUTTONDOWN
# index 0, DJ mode only) toggles it. See drivers/auto_dj_engine.py.
AUTO_ANNOUNCE_ENABLED_BY_DEFAULT = True

# How long the "v ON" / "v OFF" toggle confirmation overlays panel 3.
AUTO_ANNOUNCE_TOGGLE_OVERLAY_SECONDS = 1.5

# ==========================================
# DJ BRANDING ASSEMBLY ANIMATION (top strip, panels 1+2)
# ==========================================
# Shares BRANDING_OVERLAY_INTERVAL_SECONDS/BRANDING_OVERLAY_DURATION_SECONDS
# above for when it fires -- this just controls how long the fly-in/settle
# takes before the assembled string holds steady for the rest of that window.
BRANDING_ASSEMBLY_DURATION_SECONDS = 0.8

# ==========================================
# BITMAP PIXEL FONT ENGINE (5x7 Grid)
# ==========================================
FONT_5X7 = {
    'A': ["01110","10001","10001","11111","10001","10001","10001"],
    'B': ["11110","10001","10001","11110","10001","10001","11110"],
    'C': ["01111","10000","10000","10000","10000","10000","01111"],
    'D': ["11110","10001","10001","10001","10001","10001","11110"],
    'E': ["11111","10000","10000","11110","10000","10000","11111"],
    'F': ["11111","10000","10000","11110","10000","10000","10000"],
    'G': ["01111","10000","10000","10011","10001","10001","01110"],
    'H': ["10001","10001","10001","11111","10001","10001","10001"],
    'I': ["01110","00100","00100","00100","00100","00100","01110"],
    'J': ["00001","00001","00001","00001","00001","10001","01110"],
    'K': ["10001","10010","10100","11000","10100","10010","10001"],
    'L': ["10000","10000","10000","10000","10000","10000","11111"],
    'M': ["10001","11011","10101","10001","10001","10001","10001"],
    'N': ["10001","11001","10101","10011","10001","10001","10001"],
    'O': ["01110","10001","10001","10001","10001","10001","01110"],
    'P': ["11110","10001","10001","11110","10000","10000","10000"],
    'Q': ["01110","10001","10001","10001","10101","10010","01101"],
    'R': ["11110","10001","10001","11110","10100","10010","10001"],
    'S': ["01111","10000","10000","01110","00001","00001","11110"],
    'T': ["11111","00100","00100","00100","00100","00100","00100"],
    'U': ["10001","10001","10001","10001","10001","10001","01110"],
    'V': ["10001","10001","10001","10001","10001","01010","00100"],
    'W': ["10001","10001","10001","10001","10101","11011","10001"],
    'X': ["10001","10001","01010","00100","01010","10001","10001"],
    'Y': ["10001","10001","01010","00100","00100","00100","00100"],
    'Z': ["11111","00001","00010","00100","01000","10000","11111"],

    # Lowercase -- narrower than uppercase where the letterform allows it
    # (i/j/l/f/k/r/t), which is the main lever for condensing pixel usage
    # since most real-world display text (track titles, factoids) is
    # naturally mixed/lower case rather than shouted uppercase.
    'a': ["00000","00000","01110","00001","01111","10001","01111"],
    'b': ["10000","10000","11110","10001","10001","10001","11110"],
    'c': ["00000","00000","01111","10000","10000","10000","01111"],
    'd': ["00001","00001","01111","10001","10001","10001","01111"],
    'e': ["00000","00000","01110","10001","11111","10000","01111"],
    'f': ["0011","0100","1110","0100","0100","0100","0100"],
    'g': ["00000","00000","01111","10001","10001","01111","00001"],
    'h': ["10000","10000","11110","10001","10001","10001","10001"],
    'i': ["01","00","01","01","01","01","01"],
    'j': ["001","000","001","001","001","101","011"],
    'k': ["1000","1000","1010","1100","1010","1010","1001"],
    'l': ["10","10","10","10","10","10","11"],
    'm': ["00000","00000","11010","10101","10101","10101","10101"],
    'n': ["00000","00000","10110","11001","10001","10001","10001"],
    'o': ["00000","00000","01110","10001","10001","10001","01110"],
    'p': ["00000","00000","11110","10001","10001","11110","10000"],
    'q': ["00000","00000","01111","10001","10001","01111","00001"],
    'r': ["0000","0000","1011","1100","1000","1000","1000"],
    's': ["00000","00000","01111","10000","01110","00001","11110"],
    't': ["0100","0100","1111","0100","0100","0100","0011"],
    'u': ["00000","00000","10001","10001","10001","10001","01111"],
    'v': ["00000","00000","10001","10001","10001","01010","00100"],
    'w': ["00000","00000","10001","10001","10101","10101","01010"],
    'x': ["00000","00000","10001","01010","00100","01010","10001"],
    'y': ["00000","00000","10001","10001","01111","00001","01110"],
    'z': ["00000","00000","11111","00010","00100","01000","11111"],

    '0': ["01110","10001","10011","10101","11001","10001","01110"],
    '1': ["00100","01100","00100","00100","00100","00100","01110"],
    '2': ["01110","10001","00001","00110","01000","10000","11111"],
    '3': ["11110","00001","00001","00110","00001","00001","11110"],
    '4': ["00010","00110","01010","10010","11111","00010","00010"],
    '5': ["11111","10000","10000","11110","00001","00001","11110"],
    '6': ["01110","10000","10000","11110","10001","10001","01110"],
    '7': ["11111","00001","00010","00100","01000","01000","01000"],
    '8': ["01110","10001","10001","01110","10001","10001","01110"],
    '9': ["01110","10001","10001","01111","00001","00001","01110"],
    ':': ["00000","00100","00100","00000","00100","00100","00000"],
    '!': ["00100","00100","00100","00100","00100","00000","00100"],
    '*': ["00000","10101","01110","11111","01110","10101","00000"],
    '#': ["01010","11111","01010","01010","11111","01010","00000"],
    ' ': ["000","000","000","000","000","000","000"],  # narrow -- avoids huge whitespace gaps
    '-': ["00000","00000","00000","11111","00000","00000","00000"],
    '>': ["10000","01000","00100","00010","00100","01000","10000"],
    '.': ["00000","00000","00000","00000","00000","01100","01100"],
    ',': ["00000","00000","00000","00000","00000","01100","01000"],
    "'": ["01100","01100","01000","00000","00000","00000","00000"],
    '?': ["01110","10001","00001","00110","00100","00000","00100"],
    '&': ["01100","10010","10100","01000","10101","10010","01101"],
    '(': ["00010","00100","01000","01000","01000","00100","00010"],
    ')': ["01000","00100","00010","00010","00010","00100","01000"],
    '/': ["00001","00010","00010","00100","01000","01000","10000"],
    '%': ["11001","11010","00010","00100","01000","01011","10011"],
}
