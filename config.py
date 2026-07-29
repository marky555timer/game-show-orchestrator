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
PIXEL_SCALE = 10
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
AI_CLEANUP_MODEL = "claude-sonnet-5"

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
# Reuses the same Anthropic API key/model as the title cleanup above.
# One AI call per confidently-identified track produces both the DJ-mode
# "did you know" factoid AND the quiz-mode question/answers, so it never
# costs more than one request per track. Results are cached to disk
# forever (factoids don't go stale) so a replayed track costs nothing.
FACTOID_AI_ENABLED = AI_CLEANUP_ENABLED
FACTOID_TIMEOUT_SECONDS = 6.0

FACTOID_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "factoid_cache.json")

# Negative-result cache TTLs, so a transient network hiccup can be
# retried later but an AI "I don't actually know this song" verdict
# doesn't get re-asked every time the track comes back up.
FACTOID_FAILURE_RETRY_SECONDS = 300        # network/timeout/parse errors
FACTOID_UNKNOWN_RETRY_SECONDS = 86400      # AI explicitly wasn't confident

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

# Neutral "armed" DMX color shown the instant an answer is selected,
# before grading. Kept distinct from the green win celebration so hosts
# never confuse "selected" with "correct".
QUIZ_SELECT_DMX_RGB = (60, 110, 255)
QUIZ_SELECT_DMX_DIMMER = 200

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
