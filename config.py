import os
import sys
import time

# ==========================================
# STANDALONE .EXE PACKAGING: BUNDLED-RESOURCE / OUTBOARD-FILE PATHS
# ==========================================
# True once PyInstaller's bootloader has taken over (both --onedir and
# --onefile builds set this). Plain `python main.py` leaves it unset.
IS_FROZEN = bool(getattr(sys, "frozen", False))

# Directory the .exe itself lives in -- NOT the same as the bundled
# resource dir under --onefile (that's a temp extraction folder, see
# resource_path() below). This is where an outboard, user-editable file
# like anthropic_key.txt is expected to sit, next to the .exe.
APP_ROOT_DIR = os.path.dirname(sys.executable) if IS_FROZEN else os.path.dirname(os.path.abspath(__file__))


def resource_path(*parts):
    """Resolves a path to a bundled asset (audio/, web/static/, etc.) that
    works identically in dev (`python main.py`), a --onedir build (assets
    sit next to the .exe), and a --onefile build (assets are unpacked to
    the temp dir named in sys._MEIPASS at runtime)."""
    base = getattr(sys, "_MEIPASS", APP_ROOT_DIR)
    return os.path.join(base, *parts)


# ==========================================
# OUTBOARD ANTHROPIC API KEY (anthropic_key.txt, packaged .exe deployments)
# ==========================================
# Standalone-.exe convention: the key lives in a plain-text file named
# anthropic_key.txt sitting next to the .exe (APP_ROOT_DIR), so it can be
# swapped/rotated without rebuilding. Dev runs (`python main.py`) fall back
# to the ANTHROPIC_API_KEY environment variable if that file isn't present,
# same as before this feature existed. A frozen build with no usable key
# from either source is treated as a hard misconfiguration -- halt cleanly
# with a clear popup/console alert rather than limping along with AI
# features silently disabled.
ANTHROPIC_KEY_FILE_PATH = os.path.join(APP_ROOT_DIR, "anthropic_key.txt")


def _load_anthropic_api_key():
    key = ""
    try:
        if os.path.exists(ANTHROPIC_KEY_FILE_PATH):
            with open(ANTHROPIC_KEY_FILE_PATH, "r", encoding="utf-8") as f:
                key = f.read().strip()
    except Exception:
        key = ""

    if not key:
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()

    if not key and IS_FROZEN:
        message = (
            "CRITICAL ERROR: 'anthropic_key.txt' not found in root directory. "
            "Please create this file with your API key."
        )
        print(message)
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Game Show Orchestrator -- Startup Error", message)
            root.destroy()
        except Exception:
            pass
        sys.exit(1)

    return key


# ==========================================
# HARDWARE PORTS & CANVAS SETTINGS
# ==========================================
# No ENTTEC_PORT here anymore (2026-08-19) -- drivers/dmx_driver.py finds
# the Enttec DMX USB PRO by USB VID/PID instead of a fixed path, since
# which /dev/ttyUSBx or COMx it lands on isn't stable across boots. Also
# means this one line no longer needs to differ between the Windows dev
# machine (COM5) and the Pi (was /dev/ttyUSB0) -- one less thing to keep
# in sync by hand when deploying.
MIDI_PORT_NAME = "Python_PMC_Port"

# Exact window caption of the pygame canvas simulator (set via
# pygame.display.set_caption() in graphics/matrix_canvas.py). Also used by
# drivers/rekordbox_driver.py's Z-order validator (win32gui.FindWindow) to
# resolve the canvas HWND -- kept as a single constant so the two stay in
# sync if the caption is ever changed.
CANVAS_WINDOW_TITLE = "6-Panel Game Show Matrix Simulator"

# USB-UART bridge chips used on the ESP32 DevKit V1 boards/clones this rig
# uses (Silicon Labs CP2102, and the two common CH340/CH9102 variants) --
# shared by drivers/led_bridge.py (matrix panel ESP32) and
# drivers/wled_engine.py (marquee WLED ESP32) since both auto-discover
# their board by USB VID/PID rather than a fixed port path (same reasoning
# as MIDI_PORT_NAME's comment above). Both boards can enumerate with the
# same VID/PID, so the two modules coordinate over drivers/serial_ports.py
# (see that module's docstring) instead of each independently grabbing
# "the first matching port" and racing/colliding.
ESP32_USB_SERIAL_VID_PIDS = {(0x10C4, 0xEA60), (0x1A86, 0x7523), (0x1A86, 0x55D4)}

# ==========================================
# PHYSICAL PANEL LAYOUT
# ==========================================
# 6 independent 32x16 LED panels arranged as a 3-row x 2-column grid:
#   Row 1: Panel 1, Panel 2
#   Row 2: Panel 3, Panel 4
#   Row 3: Panel 5, Panel 6
# Panels 1+2 are still combined into one logical 64x16 surface for headline
# text (see TOP_COMBINED). Panel IDs match the physical button numbering
# used by the game-show console (Btn1..Btn6).
PANEL_W = 32
PANEL_H = 16
ROW_GAP = 4  # simulator-only visual seam between rows

ROW_WIDTH = PANEL_W * 2
ROW1_Y = 0
ROW2_Y = PANEL_H + ROW_GAP
ROW3_Y = 2 * (PANEL_H + ROW_GAP)

PANELS = {
    1: (0, ROW1_Y, PANEL_W, PANEL_H),
    2: (PANEL_W, ROW1_Y, PANEL_W, PANEL_H),
    3: (0, ROW2_Y, PANEL_W, PANEL_H),
    4: (PANEL_W, ROW2_Y, PANEL_W, PANEL_H),
    5: (0, ROW3_Y, PANEL_W, PANEL_H),
    6: (PANEL_W, ROW3_Y, PANEL_W, PANEL_H),
}

# Panels 1+2 treated as a single logical 64x16 surface for headline text.
TOP_COMBINED = (0, ROW1_Y, ROW_WIDTH, PANEL_H)

MATRIX_WIDTH = ROW_WIDTH
MATRIX_HEIGHT = ROW3_Y + PANEL_H
# Dev-machine simulator window scale. Knocked down to ~1/3 of the original
# 10px-per-LED size (still an integer so the LED grid stays crisp) so the
# virtual matrix window doesn't dominate the dev display.
PIXEL_SCALE = 3
GAP = 1

# Pixel size of just the LED-matrix render region within the window --
# separate from WINDOW_W/H below (Feature Update: right-side overlay panel),
# since the window is now wider than the matrix render region alone. Every
# existing matrix-render call site (graphics/matrix_canvas.py) keeps using
# this, unchanged.
MATRIX_RENDER_WIDTH_PX = MATRIX_WIDTH * PIXEL_SCALE
MATRIX_RENDER_HEIGHT_PX = MATRIX_HEIGHT * PIXEL_SCALE

# ==========================================
# RIGHT-SIDE UI OVERLAY PANEL (Feature Update)
# ==========================================
# Diagnostic/control panel added to the right of the LED-matrix simulator
# window: session token usage + estimated $/hr cost, live CPU/RAM (psutil),
# Auto-DJ transition fine-tuning, playback controls (volume, next/prev
# track), a "Suppress AI Functions" checkbox, a "Test Westminster Clock"
# button, and a master "CLOSE APP" button. See graphics/overlay_panel.py.
#
# Window is fixed at 800x480 (2026-08-09) to match the physical 7" Pi
# touchscreen this now runs on -- the old "panel must fit within
# MATRIX_RENDER_HEIGHT_PX, never grow height" constraint from here is gone;
# it existed only because drivers/rekordbox_driver.py's OCR needed the
# always-on-top canvas window to not cover more of Rekordbox's window than
# its crop-ratio math expected. That driver (and OCR entirely) was retired
# 2026-08-07, so there's no longer anything behind this window whose
# position matters -- WINDOW_H can be whatever the touchscreen actually is.
WINDOW_W = 800
WINDOW_H = 480
OVERLAY_PANEL_WIDTH_PX = WINDOW_W - MATRIX_RENDER_WIDTH_PX

# Anthropic pricing (Claude Haiku 4.5, config.AI_CLEANUP_MODEL -- the only
# model this app ever calls) used to turn drivers/token_tracker.py's raw
# input/output counts into a live session cost estimate on the overlay
# panel. $ per million tokens.
HAIKU_INPUT_COST_PER_MTOK = 1.00
HAIKU_OUTPUT_COST_PER_MTOK = 5.00

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
# Never hardcode the key here directly. Packaged .exe builds instead read
# ANTHROPIC_KEY_FILE_PATH (anthropic_key.txt next to the .exe) -- see
# _load_anthropic_api_key() above.
ANTHROPIC_API_KEY = _load_anthropic_api_key()
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
# As soon as a deck's track is confidently identified (no button press
# required), drivers/factoid_engine.py background-fetches quiz questions for
# it, one Haiku call at a time, until the local cache holds this many
# distinct questions. Once full, the cache is never re-queried for that
# track (replays cost nothing) until a question is consumed by gameplay.
TRACK_QUESTIONS_PER_TRACK = 3
TRACK_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "track_cache.json")

# Priority tiers for TrackQuestionEngine's background worker queue (lower
# number = served first). A Hot Track import's prefetch (drivers/
# hot_track_engine.py) jumps ahead of the currently-playing deck's own
# per-frame refill requests in the shared queue.
FACTOID_HOT_TRACK_PREFETCH_PRIORITY = 0
FACTOID_NORMAL_PREFETCH_PRIORITY = 10

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

# True/False wrong-answer correction reveal gets its own, longer hold
# (2026-08-09 fix) -- it's scrolling marquee text (graphics/text_render.py),
# not a static flash, and at SCROLL_SPEED_PX_PER_SEC a realistic correction
# sentence takes several times longer than QUIZ_CELEBRATION_HOLD_SECONDS to
# complete even one scroll pass, so the old shared 2.5s was cutting it off
# before the room could read it. Only the correction reveal gets the longer
# hold -- the stats page afterward keeps its normal QUIZ_STATS_HOLD_SECONDS.
QUIZ_TF_CORRECTION_HOLD_SECONDS = 8.0

# ==========================================
# GAMEPAD BUTTON DEBOUNCE (Btns 5-8)
# ==========================================
BUTTON_DEBOUNCE_SECONDS = 0.15

# Btn2 (normal-trivia gate, moved off Btn6 2026-08-12) plays audio on every
# accepted press and can launch an API call -- a wider guard than the other
# buttons so a single physical press can never stack duplicate fetches/
# sounds. Btn6 (now a dedicated, always-local Price Game trigger) reuses
# this same constant for its own debounce.
QUIZ_GATE_DEBOUNCE_SECONDS = 0.4

# Btn2 pulls instantly from the pre-fetched track_cache.json queue -- no
# network call happens at press time. This timeout only covers the
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

# 3-channel DMX relay block, address 177 (right after the 176-channel
# fixture rig above -- see pi_deploy or the dip-switch note from setup).
# Relay logic per the hardware: any non-zero channel value energizes that
# relay, 0 de-energizes it. Relay 1 does a brief one-shot pulse on the
# BIG WIN game SFX (see inputs/gamepad.py::trigger_big_win() and
# drivers/dmx_driver.py::pulse_channel()/poll_pulses()) -- deliberately
# gated on state.sfx_enabled the same as the sound itself, so muting Game
# SFX also silences the relay.
RELAY1_CHANNEL = 177
RELAY2_CHANNEL = 178
RELAY3_CHANNEL = 179
RELAY_PULSE_SECONDS = 0.25

# ==========================================
# USB RELAY BOARD (4-channel, drivers/relay_engine.py, added 2026-09-14,
# corrected to 4 channels 2026-09-18)
# ==========================================
# Separate physical board from the 3-channel DMX relay block above -- a
# 4-channel USB relay module, addressed directly over its own serial link
# (NOT part of the 176-channel DMX universe). Protocol is the standard LCUS
# 4-byte command frame: [0xA0, channel(1-4), state, checksum], 9600 baud --
# see drivers/relay_engine.py's module docstring. Originally built assuming
# an 8-channel LCUS-8 board (hence the "point channel" living at 8) -- the
# real board only has 4 channels, channels 5-8 don't exist, corrected below.
#
# This board's CH340 USB-UART chip enumerates under the SAME VID/PID as
# some ESP32 clones already listed in ESP32_USB_SERIAL_VID_PIDS above.
# This board's firmware doesn't echo commands back (confirmed live
# 2026-09-14), so unlike led_bridge.py/wled_engine.py's heartbeat/JSON
# proof, relay_engine.py trusts VID/PID alone -- safe today since this is
# the only CH340 device on the rig (both ESP32 boards are CP2102). Would
# need a real disambiguation step if a CH340-based ESP32 (e.g. the
# still-pending accent board) ever joins. led_bridge.py/wled_engine.py's
# own port scans were fixed the same day this board was added
# (serial_ports.held_by(port) is None, not a hardcoded other-module name)
# so they can't fight over this board's port either way. Was undetected for
# a while (2026-09-18) because it was plugged into a dead port on the old
# USB hub -- moved to a new overcurrent-protected 4-port hub, enumerates
# fine now.
USB_RELAY_VID_PID = (0x1A86, 0x7523)
USB_RELAY_SERIAL_BAUD = 9600
USB_RELAY_CHANNEL_COUNT = 4
# Which channel gets the brief "point scored" pulse -- currently: Simon
# round cleared (drivers/simon_engine.py::press()'s round-clear block). NOT
# gated on state.sfx_enabled -- it's a physical scoring signal to external
# hardware, not an SFX accent.
#
# Channel 1 (2026-09-18: moved off channel 8, which doesn't exist on this
# 4-channel board) is meant to drive a physical electromagnetic doorbell --
# NOT wired up yet as of this correction, so this pulse only clicks the
# relay's own internal coil for now, nothing audible downstream. Once the
# doorbell is physically wired to channel 1, the same "one-shot relay timer
# in front of the coil caps the actual energized time" design intent from
# the original board still applies -- USB_RELAY_PULSE_SECONDS below only
# needs to reliably trigger that hardware timer, not be sized to the coil's
# safe duration; the software pulse is NOT what protects the coil.
USB_RELAY_POINT_CHANNEL = 1
USB_RELAY_PULSE_SECONDS = 0.25

# "BIG WIN" doorbell celebration (2026-09-18): inputs/gamepad.py::
# trigger_big_win() rings the doorbell USB_RELAY_BIG_WIN_RING_COUNT times
# in rapid succession (drivers/relay_engine.py::ring()) instead of playing
# the normal bigwin.wav SFX -- that play_processed_sound() call is
# commented out, not deleted, in case it's wanted again later.
# 120ms on/off cycle (2026-09-18 operator feedback: the original 250ms-on/
# 200ms-off felt too slow) -- 60ms each way, split evenly. Noticeably
# shorter than the single-ring USB_RELAY_PULSE_SECONDS (250ms) already
# proven to reliably trigger the one-shot hardware timer, so confirm by
# ear/feel that every one of the 8 strikes is actually landing at this
# speed, not just some of them -- tune the split (doesn't have to stay
# 50/50) if the solenoid can't fully reset between strikes this fast.
USB_RELAY_BIG_WIN_RING_COUNT = 8
USB_RELAY_RING_ON_SECONDS = 0.06
USB_RELAY_RING_OFF_SECONDS = 0.06

# ==========================================
# MARQUEE LIGHTS (2026-08-23, drivers/wled_engine.py)
# ==========================================
# "Marquee lights" = the WS2811 "bullet bulb" LEDs outlining the edges of
# the physical panel sections -- distinct from the pixels *inside* the
# panels, which drivers/led_bridge.py's own ESP32 already drives. Driven by
# a separate ESP32 running third-party WLED firmware (not esp32_firmware/
# Display.ino). Now that this board lives permanently on the rig next to
# the Pi (2026-09) instead of moving between the dev laptop and the show
# rig, drivers/wled_engine.py prefers a direct USB-serial link (WLED's
# native Adalight/"LEDstream" realtime-input protocol) and falls back to
# DDP (a realtime UDP pixel-push protocol WLED also supports natively)
# over WiFi -- e.g. when working on this board from the dev laptop instead
# of the Pi. WLED_HOST/WLED_DDP_PORT below are about that DDP fallback
# specifically (still addressed by mDNS hostname rather than a raw IP for
# the same reason as this file's own ENTTEC_PORT, since the DDP path can
# still land on whatever network the board was last tested on); the
# serial link needs no address, just USB-VID/PID auto-discovery (see
# ESP32_USB_SERIAL_VID_PIDS above). MARQUEE_-prefixed constants below are
# about the lights themselves, independent of either transport.
#
# WLED_SERIAL_ENABLED = True (2026-09-02): was False while this board ran
# WLED's "esp32dev_debug" build (WLED_DEBUG's verbose Serial prints
# corrupted the Adalight stream -- see git history/this file's prior
# comment here for the full diagnosis). Board was reflashed with a
# standard (non-debug) WLED 0.15.2 release build via its own web OTA
# updater; confirmed via /json/info afterward -- release: "ESP32" (was
# "ESP32_DEBUG"). Re-enabling serial gets the marquee off WiFi/DDP
# entirely, which is the actual fix for the WiFi-jitter chase-pattern
# stutter DDP could never fully avoid -- not just another mitigation.
WLED_SERIAL_ENABLED = True
#
# Hardware is a work in progress -- LED counts/segments below will grow as
# more panels get wired; confirm against the physical rig before trusting
# them stale. As of this note: LEDs 1-30 outline the combined "title"
# panels (1 and 2 together, 2x width hence 30 rather than 18), LEDs 31-102
# outline panels 3-6 individually at 18 LEDs each -- matching
# config.py's own PANELS dict for the matrix (6 physical panels; the
# marquee only has 5 outline sections because 1+2 combine into one "title"
# run). All 5 sections now wired. Pixel index 0 of EACH segment starts at
# that segment's top-right corner (confirmed against the physical build,
# direction of travel around the loop not yet confirmed -- verify
# empirically with a chase effect before relying on it).
WLED_HOST = "wled-2bb8e8.local"
WLED_DDP_PORT = 4048
# This board's factory MAC (lowercase, no colons, matching WLED's own
# JSON format -- same as config.ACCENT_WLED_MAC's format) -- derived
# from WLED_HOST's mDNS hostname and confirmed against `ip neigh`/lsusb
# output for this board's IP. Used by drivers/wled_engine.py to
# positively verify a candidate USB-serial port is actually this board
# before connecting (2026-09-15), rather than the old reject-based
# elimination, which only worked for exactly two indistinguishable
# candidates and broke once the accent board (also CP2102) existed --
# see drivers/wled_engine.py's _reconcile_with_led_bridge history.
MARQUEE_WLED_MAC = "b0a7322bb8e8"
MARQUEE_TOTAL_LEDS = 102
# "reverse": physical loop-travel direction isn't confirmed yet (only the
# top-right start corner is) -- flip per-segment here if a chase effect
# turns out to visually run backwards once seen live, rather than
# reworking the effect code itself.
MARQUEE_SEGMENTS = {
    "title": {"start": 0, "count": 30, "reverse": False},    # panels 1+2 combined outline
    # panel3/panel4 fixed 2026-09-14: the physical strip is snaked
    # title -> panel4 -> panel3 -> panel5 -> panel6, NOT in panel-number
    # order -- confirmed live (Simon's green/panel3 command was visually
    # landing on panel4's real outline while panel4's own command also
    # correctly landed there, i.e. panel3's declared range was physically
    # sitting on panel4's wiring). Every caller addresses segments by
    # name, so swapping just these two start offsets is the whole fix.
    "panel4": {"start": 30, "count": 18, "reverse": False},  # panel 4 outline -- wired right after title
    "panel3": {"start": 48, "count": 18, "reverse": False},  # panel 3 outline -- wired after panel4
    "panel5": {"start": 66, "count": 18, "reverse": False},  # panel 5 outline
    "panel6": {"start": 84, "count": 18, "reverse": False},  # panel 6 outline
}

# --- Third ESP32/WLED board: "outlines" accent strip (rebuilt 2026-09-15) ---
# A separate, third board from the marquee/outline ESP32 above
# (WLED_HOST/MARQUEE_* -- USB-serial + DDP, raw pixel push for
# frame-accurate chase effects). Drives a 120-lamp strip, controlled by
# WLED preset/effect switches only (no second WiFi client, no per-pixel
# timing needed for this effect).
#
# The originally-purchased board (a WeGoIOT "ESP32 WLED" DOM-WLE-18P
# commercial controller) was a dead end for wired control -- its
# exposed "IO33/GND" terminal turned out to be WLED's plain pushbutton
# input, not a UART, and its "INPUT/UART" USB-C jack never enumerated
# as a USB device at all despite ruling out cable/power/hub one at a
# time. Replaced with a blank ESP32 DevKit V1, flashed 2026-09-15 with
# stock WLED v16.0.1 via esptool (bootloader_esp32_8m.bin @0x1000 +
# partitions_c3_4m.bin @0x8000, both from install.wled.me/bin/boot/,
# app @0x10000 from the GitHub release -- confirmed booting and
# answering WLED's JSON API over serial before going into the rig).
#
# This replacement board enumerates on the same Silicon Labs CP2102
# chip as the matrix and marquee boards (VID/PID identical, even the
# same blank embedded serial number "0001") -- the "prefer a different
# chip" advice from the original purchase didn't pan out. Since
# drivers/serial_ports.py's heartbeat-elimination only ever handled
# TWO indistinguishable candidates (confirmed: it briefly caused a
# 5s main-loop stall bouncing between three when this board's USB
# first showed up unidentified, 2026-09-14 log), drivers/
# accent_engine.py instead identifies its own port positively by
# asking each unclaimed CP2102 candidate for its WLED info ({"v":true}
# over serial) and checking the "mac" field against ACCENT_WLED_MAC
# below -- burned into this specific chip at manufacture, so it can't
# collide with the matrix or marquee board no matter which USB port or
# enumeration order they land on.
ACCENT_WLED_MAC = "704bca4ef5cc"  # this board's factory MAC (from esptool chip_id / WLED's own /json/info), lowercase no colons to match WLED's own JSON format
ACCENT_SERIAL_BAUD = 115200
ACCENT_EFFECT_CYCLE_SECONDS = 3.0
# Built-in WLED effect IDs (not user-defined presets -- these ship with
# every WLED install, so the bring-up test below works with zero WLED
# configuration). Exact IDs/names can shift slightly between WLED
# versions; picked for visually obvious changes (solid/breathe/rainbow/
# chase/fireworks), not for the specific IDs mattering.
ACCENT_TEST_EFFECTS = [0, 2, 9, 38, 66]

# --- Accent strip effect selection (2026-09-15, replaced 2026-09-18) ---
# Used to be a 13-entry curated mapping onto the closest-looking WLED
# effect per DMX DJ_THEME_* pattern, aesthetically pairing the accent
# strip's motion with the DMX rig's bespoke Python animations. Replaced at
# the user's request with direct selection from WLED's FULL default effect
# catalog instead -- state.accent_theme_index is now literally a WLED
# effect ID (index into this list), not an index into a curated subset.
# The curated aesthetic-pairing idea may come back later as a way to trim
# this list down to a shorter "recommended" set (see git history for the
# original 13-entry mapping if that's revisited), but for now it's every
# effect WLED ships, per the user's explicit "let's go with what's default
# in WLED."
#
# Verified live 2026-09-18 against this board's own GET /json/eff (WLED
# 16.0.1, "Niji" build, fxcount 220) -- index-aligned with the board's own
# effect IDs, NOT guessed from generic WLED docs (this build's effect list
# includes the newer "PS " particle-system effects and doesn't necessarily
# match every WLED release 1:1). Re-verify against /json/eff if this
# board's firmware is ever upgraded, since IDs can shift between releases.
ACCENT_EFFECT_NAMES = [
    "Solid", "Blink", "Breathe", "Wipe", "Wipe Random", "Random Colors", "Sweep", "Dynamic",
    "Colorloop", "Rainbow", "Scan", "Scan Dual", "Fade", "Theater", "Theater Rainbow", "Running",
    "Saw", "Twinkle", "Dissolve", "Dissolve Rnd", "Sparkle", "Sparkle Dark", "Sparkle+", "Strobe",
    "Strobe Rainbow", "Strobe Mega", "Blink Rainbow", "Android", "Chase", "Chase Random",
    "Chase Rainbow", "Chase Flash", "Chase Flash Rnd", "Rainbow Runner", "Colorful",
    "Traffic Light", "Sweep Random", "Chase 2", "Aurora", "Stream", "Scanner", "Lighthouse",
    "Fireworks", "Rain", "Tetrix", "Fire Flicker", "Gradient", "Loading", "Rolling Balls",
    "Fairy", "Two Dots", "Fairytwinkle", "Running Dual", "Image", "Chase 3", "Tri Wipe",
    "Tri Fade", "Lightning", "ICU", "Multi Comet", "Scanner Dual", "Stream 2", "Oscillate",
    "Pride 2015", "Juggle", "Palette", "Fire 2012", "Colorwaves", "Bpm", "Fill Noise",
    "Noise 1", "Noise 2", "Noise 3", "Noise 4", "Colortwinkles", "Lake", "Meteor",
    "Copy Segment", "Railway", "Ripple", "Twinklefox", "Twinklecat", "Halloween Eyes",
    "Solid Pattern", "Solid Pattern Tri", "Spots", "Spots Fade", "Glitter", "Candle",
    "Fireworks Starburst", "Fireworks 1D", "Bouncing Balls", "Sinelon", "Sinelon Dual",
    "Sinelon Rainbow", "Popcorn", "Drip", "Plasma", "Percent", "Ripple Rainbow", "Heartbeat",
    "Pacifica", "Candle Multi", "Solid Glitter", "Sunrise", "Phased", "Twinkleup", "Noise Pal",
    "Sine", "Phased Noise", "Flow", "Chunchun", "Dancing Shadows", "Washing Machine",
    "Rotozoomer", "Blends", "TV Simulator", "Dynamic Smooth", "Spaceships", "Crazy Bees",
    "Ghost Rider", "Blobs", "Scrolling Text", "Drift Rose", "Distortion Waves", "Soap",
    "Octopus", "Waving Cell", "Pixels", "Pixelwave", "Juggles", "Matripix", "Gravimeter",
    "Plasmoid", "Puddles", "Midnoise", "Noisemeter", "Freqwave", "Freqmatrix", "GEQ",
    "Waterfall", "Freqpixels", "RSVD", "Noisefire", "Puddlepeak", "Noisemove", "Noise2D",
    "Perlin Move", "Ripple Peak", "Firenoise", "Squared Swirl", "PacMan", "DNA", "Matrix",
    "Metaballs", "Freqmap", "Gravcenter", "Gravcentric", "Gravfreq", "DJ Light", "Funky Plank",
    "Shimmer", "Pulser", "Blurz", "Drift", "Waverly", "Sun Radiation", "Colored Bursts",
    "Julia", "RSVD", "RSVD", "RSVD", "Game Of Life", "Tartan", "Polar Lights", "Swirl",
    "Lissajous", "Frizzles", "Plasma Ball", "Flow Stripe", "Hiphotic", "Sindots", "DNA Spiral",
    "Black Hole", "Wavesins", "Rocktaves", "Akemi", "PS Volcano", "PS Fire", "PS Fireworks",
    "PS Vortex", "PS Fuzzy Noise", "PS Ballpit", "PS Box", "PS Attractor", "PS Impact",
    "PS Waterfall", "PS Spray", "PS GEQ 2D", "PS GEQ Nova", "PS Ghost Rider", "PS Blobs",
    "PS DripDrop", "PS Pinball", "PS Dancing Shadows", "PS Fireworks 1D", "PS Sparkler",
    "PS Hourglass", "PS Spray 1D", "PS 1D Balance", "PS Chase", "PS Starburst", "PS GEQ 1D",
    "PS Fire 1D", "PS Sonic Stream", "PS Sonic Boom", "PS Springy", "PS Galaxy",
    "Color Clouds", "Slow Transition",
]
ACCENT_FX_SOLID = 0  # "Solid" -- used for the white/Price-Game look and flash() confirmations

# Approximate RGB stand-ins for DJ_COLOR_PALETTE's three dedicated-emitter
# looks (white lamp/amber lamp/uv) -- those store r=g=b=0 with the real
# intensity on a separate white/amber/uv attribute (see the DJColor class
# above), which only makes sense for the DMX fixtures' actual White/Amber/
# UV emitters. The accent strip is plain RGB, so sending those colors
# as-is would mean literal black -- these substitute the closest visible
# color instead. UV in particular can only ever be approximated this way;
# RGB LEDs cannot produce real ultraviolet light.
ACCENT_DEDICATED_EMITTER_RGB = {
    "white lamp": (255, 255, 255),
    "amber lamp": (255, 140, 0),
    "uv":         (120, 0, 255),
}

# WLED's built-in "Theater Chase" effect -- the "movie marquee" bulb-chase
# look from drivers/wled_engine.py's _apply_simon() top-strip pattern
# (every SIMON_MARQUEE_CHASE_SPACING'th pixel lit, shifting over time),
# now offered as a selectable accent-strip option too, per user request
# 2026-09-15 ("I like that effect, I want it to be part of the theme
# rotation"). Plain (13) uses whatever color is currently set, matching
# the Simon pattern's actual look; Rainbow (14) cycles hue instead --
# kept as a named alternative in case that variant is ever wanted.
ACCENT_FX_THEATER = 13
ACCENT_FX_THEATER_RAINBOW = 14

# WLED's built-in rainbow-cycle effect, used for accent_sound_enabled's
# sibling gradient control (state.accent_gradient_mode == "rainbow") --
# accent is effect-driven, not raw-pixel like the marquee, so "rainbow"
# here means selecting this built-in effect rather than hand-computing
# per-pixel hues. Same ID already referenced as the 3rd entry of
# ACCENT_TEST_EFFECTS's bring-up cycle; named separately here since that
# list is a test-cycle order, not meant as a lookup table for this.
ACCENT_FX_RAINBOW = 9

# Total LED count on the accent strip, per the user's own spec (a "120
# lamp strip") -- NOT yet cross-verified against this board's actual
# saved WLED LED-count setting (it wasn't reachable over WiFi to check
# at the time this was written; confirm via its /json/info "leds.count"
# if anything here seems off in practice).
ACCENT_TOTAL_LEDS = 120

# Segment split for the "top panel only" vs "all panels" movie-chase
# variants -- GUESS pending confirmation of the accent strip's actual
# physical wiring layout (unlike the marquee, whose MARQUEE_SEGMENTS were
# measured against the real build). Defaulting to the same 30-LED "title"
# span the marquee uses for its own top-panel section, purely because
# it's the only known reference point on this rig -- confirm/adjust
# against the real strip's physical layout before trusting this.
ACCENT_TOP_SEGMENT_LED_COUNT = 30

# Marquee light-show timing (drivers/wled_engine.py::update()). v1 scope is
# deliberately a simplified subset of the DMX rig's full state machine
# (drivers/lighting_engine.py) -- show-phase gating, DJ-color sync, a game-
# mode chase, win/loss flashes, and Westminster twinkle, but NOT yet the
# mystery-sting/song-intro-sparkle branches DMX also handles. Reuses the
# DMX rig's own intro-flash timing constants (SHOW_INTRO_DMX_FLASH_AT_
# SECONDS etc., further down this file) so the two rigs' flashes land in
# the same instant rather than drifting apart.
MARQUEE_FLASH_SECONDS = 0.25
MARQUEE_DJ_BREATHE_PERIOD_SECONDS = 4.0

# DJ-mode "dance" patterns, selected by state.marquee_theme_index (its own
# independent index since the 2026-09-17 per-fixture-type decoupling --
# see DJ_FEATURE_ORDER/MARQUEE_THEME_NAMES above). See drivers/
# wled_engine.py's _DJ_PATTERNS list.
MARQUEE_DJ_WAVE_LENGTH_LEDS = 12     # traveling brightness wave's wavelength, in pixels
MARQUEE_DJ_WAVE_SPEED_SECONDS = 3.0  # seconds for the wave to shift one full wavelength
MARQUEE_DJ_CHASE_LAP_SECONDS = 2.0   # seconds per full lap, complement-trail comet pattern
MARQUEE_DJ_BOUNCE_PERIOD_SECONDS = 3.0  # seconds per full there-and-back sweep, bounce pattern
MARQUEE_DJ_RAINBOW_SPEED = 0.15      # hue cycles/sec, rainbow chase pattern

# Game-mode: an all-white comet-style chase per segment (loops within its
# own segment, not across the whole strip -- title and panel3 are two
# separate physical loops, not one continuous run) plus a periodic double
# flash burst. Deliberately keyed off state.price_game_active rather than
# only state.mode == MODE_GAME in drivers/wled_engine.py::update() -- Price
# Game sets price_game_active True immediately (drivers/price_game_engine.
# py::_arm_intro()) but doesn't flip state.mode to MODE_GAME until its
# ~3.5s strobe+banner intro finishes, so gating on mode alone left the
# marquee still doing the DJ pattern for that whole window instead of
# snapping to the chase the instant the round starts.
MARQUEE_GAME_CHASE_STEP_SECONDS = 0.06  # time per 1-pixel step of the comet
MARQUEE_GAME_COMET_LENGTH = 6           # lit pixels in the comet's fading tail
MARQUEE_GAME_FLASH_INTERVAL_SECONDS = 4.0  # how often a flash burst interrupts the chase
MARQUEE_GAME_FLASH_BURST_SECONDS = 0.15    # each of the burst's 2 flashes

# Tap-tempo: rolling window of taps used to compute the period, and the
# sane clamp range so a mis-tap can't produce a silly-fast/slow oscillation.
TEMPO_TAP_WINDOW = 4
TEMPO_PERIOD_MIN_SECONDS = 0.25
TEMPO_PERIOD_MAX_SECONDS = 2.0
TEMPO_PERIOD_DEFAULT_SECONDS = 0.6

# Per-track DMX lightshow memory (drivers/light_prefs_engine.py): whenever
# the operator manually adjusts tap-tempo/color/pattern in DJ mode, the
# resulting tempo period + color index + theme index are saved for the
# current track once LIGHT_PREFS_SAVE_DEBOUNCE_SECONDS pass with no further
# adjustment (so a burst of taps/cycles writes to disk once, not on every
# single press) -- retrieved automatically the next time that track's
# identified, so the operator's tuned look for a song survives past a
# single play. CSV, hand-editable like announcement_text.csv.
LIGHT_PREFS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "light_prefs")
LIGHT_PREFS_CACHE_PATH = os.path.join(LIGHT_PREFS_DIR, "light_prefs.csv")
LIGHT_PREFS_SAVE_DEBOUNCE_SECONDS = 2.5

# ==========================================
# SHOW CURATION: PER-TRACK METADATA (2026-08-12)
# ==========================================
# drivers/music_metadata_engine.py -- content/mood tags + descriptive
# attributes used to build audience-appropriate event profiles (Corporate,
# Wedding, Bar Night, Kids Birthday, Ladies Night, ...) that filter
# drivers/deck_orchestrator.py's next-track candidate pool. CSV,
# hand-editable like light_prefs.csv/announcement_text.csv.
#
# Two field families, deliberately modeled differently:
#   - Boolean content tags (explicit/slow_dance/never_charted) -- exclusion
#     filters ("no explicit lyrics at the corporate gig").
#   - Descriptive attributes (decade/genre/energy_rank) -- typed values for
#     browsing/sorting, and finer profile constraints later
#     ("energy_rank >= 3", "genre == Children's").
MUSIC_METADATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "music_metadata")
MUSIC_METADATA_PATH = os.path.join(MUSIC_METADATA_DIR, "music_metadata.csv")

# Fixed, short list -- free-text genre would be useless for filtering. Kept
# to 8 by design (2026-08-12 planning conversation): Rock absorbs
# alternative/indie rather than getting its own slot.
MUSIC_GENRES = [
    "Rock", "Adult Top 40", "Hip-Hop/Rap", "R&B/Soul",
    "Country", "Dance/Electronic", "Oldies/Classic", "Children's",
]

MUSIC_ENERGY_RANK_MIN = 1
MUSIC_ENERGY_RANK_MAX = 5

# How many tracks go into a single Haiku call for the Tag Library pass --
# large enough that a 479-track library only takes ~25 requests, small
# enough that one malformed/truncated response only costs a re-tag of that
# one batch, not the whole run.
MUSIC_TAG_BATCH_SIZE = 20

# Tempo-tap visual feedback: red flash outline on panels 3-6 decays to 0
# over this long from the moment Btn5 is held.
TEMPO_FLASH_DECAY_SECONDS = 0.1

# ==========================================
# SPACE INVADERS MINI-GAME (DJ-mode dual-button Easter egg)
# ==========================================
# Entered from DJ_MODE by holding/pressing Gamepad Button 1 AND Button 3
# simultaneously (pygame JOYBUTTONDOWN indices 0 and 2); exited immediately
# via Select or Start (indices 9/10 on this controller -- see
# SI_EXIT_BUTTONS below for the full index story). See inputs/gamepad.py
# for the entry/exit + movement/fire input handling,
# drivers/space_invaders_engine.py for the game loop, and
# graphics/matrix_canvas.py::_render_space_invaders for rendering.
#
# The play field spans the FULL matrix canvas (MATRIX_WIDTH x
# MATRIX_HEIGHT, 64x56 under the 3-row x 2-panel grid) rather than being
# confined to a single 32x16 panel -- a proper full-canvas arcade screen
# reads far better on the rig than a game crammed into one panel's worth
# of pixels. The invader grid below is sized for this narrower-but-taller
# canvas (was 128x36 under the old 2-row/4-wide layout).
SI_ENTRY_BUTTONS = (0, 2)   # pygame JOYBUTTONDOWN indices for physical Btn1 + Btn3
# Index history (2026-08-14), all confirmed live via the console's
# "[BUTTON] Raw Button Pressed" log on the Pi -- this controller's raw
# JOYBUTTONDOWN indices don't match the Windows/XInput ones the rest of
# this file's comments were originally written against:
#   - Windows: Select=6, Start=7 (matches the old "Btn7"/"Btn8" numbering).
#   - First Pi attempt used (6, 7) unchanged -- wrong on two counts: those
#     raw indices actually land on the SHOULDER buttons here, not
#     Select/Start, AND (separately) initial testing miscounted which
#     shoulder was which.
#   - Confirmed correct mapping: Select=9, Start=10. Both were previously
#     unhandled entirely (nothing in inputs/gamepad.py checked btn==9 or
#     btn==10), which is why they read as fully "dead" rather than merely
#     wrong -- this controller has more physical buttons than the
#     Windows pad did, pushing indices further out.
SI_EXIT_BUTTONS = (9, 10)   # pygame indices for physical Select, Start
SI_ENTRY_SOUND_VOLUME = 1.0

SI_INVADER_COLS = 4
SI_INVADER_ROWS = 4
SI_INVADER_SPACING_X = 12
SI_INVADER_SPACING_Y = 10
SI_INVADER_TOP_Y = 2
SI_INVADER_SIZE = 5                    # px, square sprite footprint
SI_INVADER_STEP_X = 2                  # px per formation step
SI_INVADER_DROP_Y = 3                  # px dropped when the formation hits an edge
SI_INVADER_TICK_START_SECONDS = 0.6    # step interval, full formation alive
SI_INVADER_TICK_MIN_SECONDS = 0.15     # step interval, last invader standing

SI_PLAYER_WIDTH = 5
SI_PLAYER_HEIGHT = 3
SI_PLAYER_Y_FROM_BOTTOM = 3            # px up from the bottom edge

# --- Milton Bradley "Simon" mini-game (2026-08-20) ---
# Second DJ-mode secret combo, same "in the same vein" pattern as
# SI_ENTRY_BUTTONS above. Originally (3, 4) -- changed to (0, 1) 2026-08-21,
# too awkward a reach on the physical pad. Only matters as the DEFAULT now
# that combos can be rebound in place from Joy Assign (see
# joystick_bindings.py's update_combo()) without touching this file again.
SIMON_ENTRY_BUTTONS = (0, 1)
SIMON_FLASH_ON_SECONDS = 0.45     # a pad lit/sounding, playback step or input echo
SIMON_FLASH_GAP_SECONDS = 0.25    # dark pause between playback steps
SIMON_FAIL_HOLD_SECONDS = 1.5     # correct-answer flash hold before the game ends/restarts
SIMON_SOUND_VOLUME = 0.9

# Physical Simon hardware (2026-09-11): real arcade buttons + LEDs wired
# directly to the Pi's 40-pin GPIO header, separate from the joystick-pad
# simulation above -- see drivers/simon_hardware.py for the bring-up/test
# driver these back (Advanced panel, web remote). BCM numbering (matches
# RPi.GPIO.setmode(GPIO.BCM)); physical pin numbers noted per operator's
# wiring notes.
SIMON_HW_BUTTON_PINS = {
    "green": 17,   # physical pin 11
    "red": 23,     # physical pin 16 -- swapped with blue 2026-09-13, red/blue leads were crossed on the header
    "yellow": 22,  # physical pin 15
    "blue": 27,    # physical pin 13 -- swapped with red 2026-09-13, red/blue leads were crossed on the header
}  # common return: GND, physical pin 14
SIMON_HW_LED_PINS = {
    "green": 13,   # physical pin 33 -- 2026-09-13 remap (was wired one color off in a 4-way rotation): green/red/yellow/blue each mapped to the next color's old pin
    "red": 5,      # physical pin 29
    "yellow": 6,   # physical pin 31
    "blue": 12,    # physical pin 32
}  # common return: GND, physical pin 30 -- LEDs run on 12V through a MOSFET per channel, GPIO only drives the gate
SIMON_HW_LED_PULSE_SECONDS = 1.0

# Real-game entry via the physical arcade buttons (2026-09-13), separate
# from the joystick's secret-combo Easter egg above -- see drivers/
# simon_engine.py's "intro" phase / enter_simon_hardware(). Pad index 0-3
# order, matching both SIMON_HW_BUTTON_PINS/SIMON_HW_LED_PINS' dict order
# and panels 3-6 (graphics/matrix_canvas.py's _SIMON_PANEL_IDS) --
# explicit rather than reading dict key order so re-sorting either pin
# dict for documentation reasons can never silently reassign a pad index.
SIMON_HW_COLOR_ORDER = ["green", "red", "yellow", "blue"]
SIMON_HW_COLOR_RGB = {
    "green": (0, 255, 0),
    "red": (255, 0, 0),
    # Pure (255,255,0) reproduces as green on this marquee's LEDs (2026-09-14
    # operator feedback) -- red-shifted toward amber instead, reusing the
    # exact warm-amber value drivers/wled_engine.py::_resolve_marquee_color()
    # already uses as this same hardware's approximation for the DMX rig's
    # dedicated amber emitter (a color the operator confirmed looks good live).
    "yellow": (255, 140, 20),
    "blue": (0, 0, 255),
}
# "It's Simon!" intro jingle -- plays once while the top-panel banner/white
# chase and the panel3-6/physical-LED green-red-yellow-blue rotation run;
# the real game starts the instant it finishes. Not tracked in git (see
# pi_deploy/README.md's audio note) -- transferred to the Pi directly.
SIMON_INTRO_SOUND_PATH = resource_path("audio", "gameMusic", "simon.wav")
SIMON_INTRO_FALLBACK_SECONDS = 3.0   # used only if the intro sound fails to load/play
SIMON_INTRO_MAX_SECONDS = 20.0       # hard cap so a stuck/looping sound can never hang the intro forever
# Loss sequence: the correct answer's LED (physical button + matrix panel
# accent + marquee panel color) flashes at this rate for SIMON_FAIL_HOLD_
# SECONDS while its own color's sound effect plays once.
SIMON_LOSS_FLASH_PERIOD_SECONDS = 0.11   # 110ms
SIMON_LOSS_FLASH_DUTY = 0.90             # 90% on, 10% off
# No button pressed for this long while waiting on the player ("input"
# phase) -> time out and return to DJ mode (2026-09-13, still experimental/
# tunable -- see drivers/simon_engine.py's _handle_input_timeout()).
SIMON_INPUT_TIMEOUT_SECONDS = 20.0
# Pause between the player finishing a round (their last correct press) and
# the computer replaying the full (now one-longer) sequence for the next
# round -- 2026-09-14, operator feedback that the old ~0.7s gap (just
# SIMON_FLASH_ON/GAP_SECONDS) felt unfairly abrupt.
SIMON_ROUND_BREAK_SECONDS = 2.0
# DJ deck duck for the duration of a Simon game (entry through end), same
# "fade to silent, fade back" shape as Price Game's background-music duck
# (drivers/price_game_engine.py) -- reuses that module's already-per-frame-
# pumped drivers/midi_driver.py::update_fader_tween().
SIMON_MUSIC_DUCK_TWEEN_SECONDS = 0.4
SIMON_MUSIC_RESTORE_TWEEN_SECONDS = 0.6
# Top-strip ("title" segment) marquee chase during Simon (drivers/
# wled_engine.py::_apply_simon) -- a theater-marquee bulb chase (every
# SIMON_MARQUEE_CHASE_SPACING'th pixel lit across the WHOLE loop at once,
# shifting by one pixel every SIMON_MARQUEE_CHASE_STEP_SECONDS) rather than
# a single moving comet, per 2026-09-14 operator feedback.
SIMON_MARQUEE_CHASE_SPACING = 4          # 1 lit pixel then 3 dark, repeating
SIMON_MARQUEE_CHASE_STEP_SECONDS = 0.09  # time per 1-pixel shift of the whole pattern
# "Get Ready" pause between the intro jingle ending and round 1's first
# tone (2026-09-14, operator feedback that the game started right on the
# song's last beat, too abruptly) -- top banner reads "GET READY", marquee
# chase keeps running, no pad lit; see drivers/simon_engine.py's
# _update_get_ready().
SIMON_GET_READY_SECONDS = 1.0  # was 2.0 -- 2026-09-14 operator feedback that the break felt a little long
# Extended hold on the final score display after a hardware loss (2026-09-
# 14, operator feedback the old flash-then-immediately-gone felt too quick
# to actually read the score) -- marquee chase resumes around the still-
# displayed round count for this long before the game actually ends. Only
# applies to hardware-sourced games; the joystick combo's auto-restart is
# unchanged. See drivers/simon_engine.py's _update_score_review().
SIMON_SCORE_REVIEW_SECONDS = 3.0

SI_PLAYER_SPEED = 60.0                 # px/sec while held

SI_BULLET_SPEED = 90.0                 # px/sec, travels upward
SI_FIRE_COOLDOWN_SECONDS = 0.25
SI_EXPLOSION_HOLD_SECONDS = 0.2

# Physical panel-seam gap compensation (2026-08-14): panels 3+4 and 5+6
# (rows 2/3) sit with a small real mounting gap between the left/right
# panel, unlike panels 1+2 (row 1), which are flush -- confirmed from a
# photo of the physical rig. Since this game draws straight across all six
# panels as one continuous field, a sprite crossing that seam on rows 2/3
# visually "splits": content lands correctly up to the seam, then the
# panel on the far side is physically farther away than the pixel data
# assumes, so its content reads as having jumped too far right. Rendering
# swallows this many logical columns right at the seam (rows 2/3 only,
# nothing is ever drawn there) and shifts everything to its right back by
# the same amount to close the gap -- the same "bezel compensation" trick
# used for wallpapers spanning a physical gap between two monitors.
# Tune by eye once live: 0 disables compensation entirely.
SI_ROW23_GAP_PX = 2

# Amplified game feedback: the instant an answer is graded, ALL 10 uplight
# fixtures (2-11) snap to a brief solid green/red pulse alongside Fixture 1's
# own win/loss lamp, then fall back to the normal game chase -- see
# inputs/gamepad.py::trigger_big_win/trigger_loss and
# drivers/lighting_engine.py::_render_grade_flash.
DMX_GRADE_FLASH_SECONDS = 0.25

# DJ-mode uplighting: 13 themes (drivers/lighting_engine.py::
# _dj_theme_frame) -- 12 beat-synced animations plus theme 12, "Solid" (a
# steady non-animating wash in the current color, for when the operator
# wants the pattern out of the way). Btn8 cycles through these 13 only --
# deliberately NOT including DJ_THEME_ALL_OFF_INDEX below, since landing on
# a dark stop while cycling through patterns live reads as an error, not a
# lighting choice. ALL LIGHTS OFF is still reachable as a deliberate direct
# selection (the admin panel's theme slider, /api/dmx/scene), just never
# something you can cycle into by accident.
DJ_THEME_COUNT = 13
DJ_THEME_ALL_OFF_INDEX = DJ_THEME_COUNT  # index 13 == all lights off (direct-select only)

# DJ-mode main theme color palette, cycled by Btn7.
#
# Every fixture (all 11 are Rockville MINIRF4 V2 as of 2026-08-11) carries
# dedicated White, Amber and UV emitters alongside RGB, so the last three
# looks below drive those single channels instead of mixing RGB. That's
# deeper and more saturated than an RGB approximation -- and for UV it's the
# only way to get real blacklight at all, since RGB "purple" emits no UV.
class DJColor(tuple):
    """One selectable uplight look. Subclasses tuple of (r, g, b) so any
    code that just unpacks `r, g, b = color` keeps working unchanged, while
    the white/amber/uv emitters ride along as attributes."""

    def __new__(cls, name, r=0, g=0, b=0, white=0, amber=0, uv=0):
        self = super().__new__(cls, (r, g, b))
        self.name = name
        self.white = white
        self.amber = amber
        self.uv = uv
        return self


DJ_COLOR_PALETTE = [
    DJColor("red",     r=255, g=0,   b=0),
    DJColor("amber",   r=255, g=120, b=0),
    DJColor("yellow",  r=255, g=255, b=0),
    DJColor("green",   r=0,   g=255, b=60),
    DJColor("cyan",    r=0,   g=180, b=255),
    DJColor("purple",  r=160, g=0,   b=255),
    DJColor("magenta", r=255, g=0,   b=160),
    # Dedicated-emitter looks (appended, never inserted -- saved per-track
    # prefs in light_prefs/light_prefs.csv store a bare color_index, so
    # reordering this list would silently repaint every remembered track).
    DJColor("white lamp", white=255),
    DJColor("amber lamp", amber=255),
    DJColor("uv",         uv=255),
]

# --- Per-fixture-type DJ look decoupling (2026-09-17) ---
# DMX, marquee, and outline/accent used to share one color_index/theme_index
# pair (whatever Btn7/Btn8 set applied to all three identically). Now each
# picks independently -- state.dmx_*/marquee_*/accent_* -- and Btn9 (new,
# see inputs/gamepad.py::handle_feature_select) selects which one Btn7/Btn8
# currently target.
DJ_FEATURE_ORDER = ("dmx", "marquee", "outline")
# Mirrors DMX_GRADE_FLASH_SECONDS/MARQUEE_FLASH_SECONDS's role, just for the
# new Btn9 "you just selected this feature" confirmation flash.
DJ_FEATURE_FLASH_SECONDS = 0.25

# Gradient is a control separate from the solid-color swatch picker (not
# extra DJ_COLOR_PALETTE entries) -- "adjacent"/"complementary" reuse
# drivers/color_utils.py::hue_shift() against whatever solid color is also
# selected; "rainbow" ignores the selected color entirely on every surface.
GRADIENT_MODES = ("off", "rainbow", "adjacent", "complementary")

# Human-readable names for the admin panel's DMX-pattern dropdown, index-
# aligned with drivers/lighting_engine.py::_dj_theme_frame's theme==N
# branches (0-12; index 13, DJ_THEME_ALL_OFF_INDEX, is deliberately not a
# cycle entry -- see that constant's own comment -- so it's not named here
# either, the admin panel offers it as a separate explicit action).
DJ_THEME_NAMES = [
    "Unison Breathe", "Chase Sweep", "Alternate", "Sparkle",
    "Converging Chase", "Twinkle Strobe", "Wave Gradient", "Heartbeat",
    "Bounce", "Comet", "Beat Flash", "Paired Chase", "Solid",
]

# Same idea for the marquee's admin-panel dropdown, index-aligned with
# drivers/wled_engine.py::_DJ_PATTERNS.
MARQUEE_THEME_NAMES = [
    "Breathe", "Wave", "Twinkle Base", "Call & Response",
    "Complement Chase", "Rainbow Chase", "Bounce Comet", "Confetti",
    "Movie Chase",
]

# WLED built-in effect ID for the outline/accent board's "sound" checkbox
# (state.accent_sound_enabled) -- confirmed live 2026-09-17 against this
# board's own /json/eff (WLED 16.0.1, fxcount 220, AudioReactive usermod
# compiled in per that session's work getting analog mic input configured).
# GEQ is the classic bar-graph audio-reactive look; other audio-reactive IDs
# on this same board/build if a different one is ever wanted: 137 Freqwave,
# 138 Freqmatrix, 141 Freqpixels, 155 Freqmap, 158 Gravfreq, 198 PS GEQ 2D.
# Re-verify against /json/eff if this board's WLED version ever changes --
# same caveat ACCENT_EFFECT_NAMES's own header comment already carries.
ACCENT_AUDIOREACTIVE_FX_ID = 139  # "GEQ"

# Game-mode chase pace (seconds per step across fixtures 2-11).
CHASE_PACE_MID_SECONDS = 0.12
CHASE_PACE_FAST_SECONDS = 0.05
CHASE_PACE_SLOW_SECONDS = 0.28

# Song-transition uplighting sequence (drivers/lighting_engine.py::
# trigger_song_transition(), called by drivers/deck_orchestrator.py the
# instant a track transition fires; see _song_intro_state() for the full
# phase machine), four stages, same for every track whether it has a saved
# look or not (2026-08-12 -- an earlier version treated recorded/unrecorded
# tracks differently, which wasn't intentional, just an artifact of how the
# sequence evolved):
#   1. WHITE FLASH  -- an instant, brief full-white pop (DMX_SONG_INTRO_
#      FLASH_SECONDS), a hard cut on and off, not a fade -- the "hey, look
#      up" beat.
#   2. BLACK        -- a hard cut to black. Held for at least
#      DMX_SONG_INTRO_BLACK_SECONDS (enough to cover a plain crossfade),
#      but on an ANNOUNCED transition it holds for however long the
#      station VO actually runs (2026-08-12): the room stays dark for the
#      whole announcement and the twinkles come up as it ends, rather than
#      the lights popping back on over the top of the voice-over.
#      drivers/deck_orchestrator.py passes the measured VO length into
#      lighting_engine.trigger_song_transition().
#   3. SPARKLE      -- the animated Sparkle pattern (drivers/
#      lighting_engine.py::_dj_theme_frame theme 3), forced to white
#      regardless of the track's actual color, held for
#      DMX_SPARKLE_INTRO_SECONDS -- the actual attention-grabbing loop.
#      Fades UP over DMX_SPARKLE_FADE_IN_SECONDS rather than hard-cutting,
#      so it emerges out of the blackout instead of snapping on. Runs at a
#      fixed DMX_SPARKLE_BPM regardless of the track's own tempo -- the
#      twinkle is a house cue with its own pulse, not a preview of the
#      song; the track's real tempo takes over at SETTLE.
#   4. SETTLE       -- fades UP (DMX_SONG_TRANSITION_FADE_IN_SECONDS) into
#      the track's real resolved look: its saved pattern/color if it has
#      one, otherwise one implied by its energy (see ENERGY_COLOR_NAMES/
#      ENERGY_THEME_INDICES below), resolved earlier in the sequence so
#      it's ready the instant this fade begins. This is also where the
#      stored/AI-predicted tempo resumes driving the pattern.
DMX_SONG_INTRO_FLASH_SECONDS = 0.15
DMX_SONG_INTRO_BLACK_SECONDS = 1.0
DMX_SPARKLE_INTRO_SECONDS = 15.0
DMX_SPARKLE_FADE_IN_SECONDS = 1.0
DMX_SPARKLE_BPM = 130.0
DMX_SPARKLE_PERIOD_SECONDS = 60.0 / DMX_SPARKLE_BPM  # ~0.4615s
DMX_SONG_TRANSITION_FADE_IN_SECONDS = 1.5

# Mystery Band lighting sting (drivers/lighting_engine.py::
# trigger_mystery_blackout/release_mystery_blackout): every fixture snaps to
# full white, fades to black, and HOLDS there -- for however long the
# announcement VO runs -- until the "Who is this?" teaser actually appears,
# at which point the running pattern fades back in. The hold is open-ended
# by design (no timeout constant): its length is whatever the deferred
# teaser needs, so the room sits in darkness right up to the reveal.
DMX_MYSTERY_FLASH_SECONDS = 0.14
DMX_MYSTERY_FADE_OUT_SECONDS = 0.7
DMX_MYSTERY_FADE_IN_SECONDS = 1.2

# Per-track tempo fallback. Used when a track has no operator-saved tempo,
# no ID3 BPM tag, and the online (AI) BPM lookup either hasn't answered yet
# or came back unknown -- 120 BPM is a neutral dance-floor default that
# reads as deliberate rather than as whatever the previous track left
# behind. See drivers/deck_orchestrator.py and drivers/factoid_engine.py.
TEMPO_DEFAULT_BPM = 120.0
TEMPO_DEFAULT_PERIOD_SECONDS = 60.0 / TEMPO_DEFAULT_BPM  # 0.5s
# Sanity bounds for an AI-reported BPM before it's trusted/cached.
TEMPO_AI_BPM_MIN = 40.0
TEMPO_AI_BPM_MAX = 220.0

# Per-track energy classification (2026-08-12): "slow" | "fast" | "dark",
# fetched from the AI alongside bpm/release_year (drivers/factoid_engine.py,
# same request -- no extra API call) and cached per track key (drivers/
# light_prefs_engine.py's "energy" column). Reviewed the ~90 tracks the
# operator had already hand-picked colors/patterns for before adding this:
# those choices read as eclectic per-song taste rather than a consistent
# energy-coded system (e.g. "Thunderstruck" got red, a "slow" color below,
# despite being a driving anthem), so this taxonomy is built fresh from the
# palette given here rather than reverse-engineered from that CSV. A few
# choices do land right where you'd expect if you already think this way
# -- both AC/DC tracks got green (this file's "fast" color), and Pink
# Floyd's "Time" got UV (this file's "dark" color) -- which is reassuring
# but not enough of a pattern across ~90 songs to derive rules from safely.
#
# ENERGY_THEME_INDICES has no operator-specified mapping to work from (only
# colors were) -- it's my own judgment call grouping drivers/
# lighting_engine.py::_dj_theme_frame's patterns by how "urgent" their
# motion reads (steady/single-pulse vs. multi-point chase vs. sparse
# flicker), independent of pattern *speed*, which already tracks the
# track's actual tempo via state.dj_tempo_period regardless of this
# grouping. Straightforward to retune if it doesn't match what you
# picture -- these are just index lists.
ENERGY_COLOR_NAMES = {
    "slow": ["amber", "amber lamp", "uv", "purple", "red", "magenta"],
    "fast": ["white lamp", "green", "cyan"],
    "dark": ["purple", "uv", "cyan"],
}
ENERGY_THEME_INDICES = {
    "slow": [0, 6, 7, 10, 12],   # breathing, wave, heartbeat, beat-flash, solid
    "fast": [1, 4, 8, 9, 11],    # chase, bidirectional chase, bounce, comet, paired chase
    "dark": [2, 5, 6, 12],       # alternating, random twinkle, wave, solid
}
# Resolved by name against DJ_COLOR_PALETTE rather than hardcoded indices,
# so ENERGY_COLOR_NAMES above stays readable and safe even if the palette
# is ever reordered (it currently isn't, and shouldn't be -- see the
# append-only comment on DJ_COLOR_PALETTE -- but this costs nothing).
_dj_color_index_by_name = {c.name: i for i, c in enumerate(DJ_COLOR_PALETTE)}
ENERGY_COLOR_INDICES = {
    energy: [_dj_color_index_by_name[name] for name in names]
    for energy, names in ENERGY_COLOR_NAMES.items()
}

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
# First-launch-only bootstrap default -- once ANY value (this default, a
# remote fetch, or an operator edit) has ever been saved to
# BRANDING_CACHE_PATH, that on-disk value is authoritative forever after
# (see drivers/branding_engine.py) and the remote URL is never consulted
# again, so an operator's edit can never get silently clobbered.
BRANDING_DEFAULT_TEXT = "Trivia Nite"
BRANDING_FETCH_TIMEOUT_SECONDS = 4.0

# ==========================================
# ANNOUNCEMENT TAG-PHRASE TEXT (top-strip banner after a station
# announcement VO, drivers/announcement_engine.py) -- distinct from
# BRANDING_* above and from the unrelated /api/announcement/toggle route
# (drivers/auto_dj_engine.py's Auto-Announce on/off flag). Stored as CSV,
# not JSON/txt, specifically so a venue operator can hand-edit it outside
# the app (Excel/Sheets/Notepad) -- e.g. swap in "Enjoy A Beer At Joes!".
# Table schema (2026-08-10 redesign): one row per audio/announcements/
# file (filename,text,updated_at) -- not a single global value. See
# drivers/announcement_engine.py.
# ==========================================
ANNOUNCEMENT_TEXT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "announcements")
ANNOUNCEMENT_TEXT_CACHE_PATH = os.path.join(ANNOUNCEMENT_TEXT_DIR, "announcement_text.csv")
# Placeholder caption auto-assigned to any announcement file that doesn't
# have a real caption yet -- an obvious "someone needs to write this one"
# signal to the operator, editable later via the admin panel.
ANNOUNCEMENT_DEFAULT_TEXT = "GET READY"
ANNOUNCEMENT_BANNER_HOLD_SECONDS = 5.0

# ==========================================
# ERA TRIVIA SCHEDULING (drivers/factoid_engine.py "era" category)
# ==========================================
# Soft guarantee: by the Nth question of a game (state.game_question_count),
# if no "era" question has been served yet this game, promote one to the
# front of the queue if the async AI prefetch has produced one by then.
ERA_FORCE_AT_QUESTION_N = 3

# ==========================================
# IDLE MATRIX CYCLE (panels 3-6, DJ mode -- drivers/idle_cycle_engine.py)
# ==========================================
IDLE_START_GAME_SECONDS = 10.0
IDLE_LEADERBOARD_PAGE_SECONDS = 10.0
IDLE_DANCE_SECONDS = 10.0
IDLE_LEADERBOARD_PAGE_SIZE = 8
# GOAL PAGE: "ROUND N" / "<win score> pt to WIN" banner + top-4 scorecard
# (2 players per page, one player per panel-pair -- name on one panel,
# score on the panel beside it).
IDLE_GOAL_PAGE_SECONDS = 10.0
IDLE_GOAL_PAGE_SIZE = 2

# Idle-animation theming (Beta-Fix Feature Set, 2026-08-12): the "dancing"
# phase's panels 3-6 pool splits into a GENERAL set (dots/lines/critters --
# no seasonal imagery, so they work under any theme) and per-theme sets
# (graphics/animations.py::IDLE_THEMES) that get layered on top of the
# general pool based on state.idle_theme. This list is the source of truth
# for valid theme names/order -- graphics/animations.py asserts its
# IDLE_THEMES dict has exactly these keys, and web/remote_server.py
# validates incoming admin-panel selections against it, so the two can't
# silently drift apart.
IDLE_ANIMATION_THEMES = ["Halloween", "Birthday", "Question Marks"]
IDLE_THEME_DEFAULT = "Question Marks"

# ==========================================
# LIVE ROUND CLOCK (drivers/live_round_engine.py): 30s question timeout +
# auto-grade once every connected phone-joined player has locked in.
# ==========================================
QUESTION_TIMEOUT_SECONDS = 30.0
TIMESUP_HOLD_SECONDS = 5.0
# The Mystery Band "Who is this?" identify question gets extra time on top
# of the standard 30s -- even with the transition-settle fix (drivers/
# mystery_band_engine.py), it's the very first thing clients see after a
# song change, arriving with less runway/attention than a mid-game
# question, so it gets a longer window as a deliberate buffer.
MYSTERY_QUESTION_TIMEOUT_SECONDS = 40.0
# A player counts as "connected" if /api/player/question polled within this
# many seconds -- play.html polls every 1.2s, so this is a generous ~4x margin.
CONNECTED_PLAYER_TIMEOUT_SECONDS = 5.0

# ==========================================
# WIN SEQUENCE (drivers/win_sequence_engine.py): duck music, play applause,
# restore -- fires once a phone-joined player's score reaches GAME_WIN_SCORE.
# ==========================================
GAME_WIN_SCORE = 15
APPLAUSE_DIR = resource_path("audio", "applause")
WIN_DUCK_LEVEL_PCT = 30
WIN_DUCK_TWEEN_SECONDS = 1.0
WIN_RESTORE_TWEEN_SECONDS = 1.0
NEW_GAME_BANNER_SECONDS = 3.0

# Post-win intermission (2026-08-11): once the win sequence (duck+applause+
# restore) finishes, the game stops for this many minutes before the next
# one can start -- operator-adjustable via the admin panel, same pattern as
# GAME_WIN_SCORE. Music keeps playing normally; only the mystery "Who is
# this?" teaser is suppressed for the duration.
INTERMISSION_MINUTES_DEFAULT = 5

# Intermission auto-next watchdog (drivers/win_sequence_engine.py): Auto-DJ's
# own duration-based timer (drivers/auto_dj_engine.py) keeps running
# unmodified through intermission -- this is a backstop, not the primary
# mechanism. How far past a song's own AUTODJ_PRE_SWITCH_SECONDS trigger
# point (i.e. how overdue) before the watchdog forces a transition itself
# rather than risk sitting in dead air during a break nobody's actively
# watching.
INTERMISSION_AUTO_NEXT_GRACE_SECONDS = 20.0
BRANDING_OVERLAY_INTERVAL_SECONDS = 20.0
BRANDING_OVERLAY_DURATION_SECONDS = 5.0

# ==========================================
# OFFLINE QUIZ FALLBACK
# ==========================================
FALLBACK_QUESTIONS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fallback_questions.json")

# ==========================================
# "PRICE GAME" BONUS ROUND
# ==========================================
# Trigger: the AI-tagged "release_year" on a track's trivia questions (see
# drivers/factoid_engine.py's release_year JSON field) falling in this range
# arms the Btn5+Btn6 hold-combo override (inputs/gamepad.py::
# force_price_game()) -- it fires a DMX strobe + on-screen banner, then a
# decade-specific pricing trivia question fetched live from the AI
# (drivers/price_game_engine.py). Any decade qualifies, including the
# current one -- this is just a sanity floor/ceiling (matches
# drivers/factoid_engine.py::_parse_release_year's own bounds), not a
# 70s/80s-only restriction. A plain Btn6 tap ignores this entirely and
# always draws from the local price_game_bank.csv instead (see
# PRICE_GAME_BANK_PATH below, drivers/price_bank_engine.py) -- guaranteed
# to work regardless of whether the AI ever tagged this track's era.
PRICE_GAME_MIN_YEAR = 1950
PRICE_GAME_MAX_YEAR = time.localtime().tm_year
PRICE_GAME_STROBE_SECONDS = 1.5
PRICE_GAME_BANNER_SECONDS = 2.0
PRICE_GAME_QUESTION_TIMEOUT_SECONDS = 8.0

# ==========================================
# DJ MODE "MYSTERY BAND" TEASER
# ==========================================
# On a new track by an artist not yet asked about this session, panels 1+2
# hide the title behind a "Who is this?" teaser -- staged per
# drivers/mystery_band_engine.py::reveal_stage() and the MYSTERY_SPARKLE_/
# MYSTERY_SOLID_/MYSTERY_CASCADE_STEP_SECONDS constants below -- for
# MYSTERY_REVEAL_TIMEOUT_SECONDS. If Game Mode isn't entered in that
# window, the title reveals with a slow invert-color blink for
# MYSTERY_REVEAL_BLINK_SECONDS before standard DJ mode resumes. See
# drivers/mystery_band_engine.py.
MYSTERY_REVEAL_TIMEOUT_SECONDS = 15.0
MYSTERY_REVEAL_BLINK_SECONDS = 3.0
MYSTERY_REVEAL_BLINK_PERIOD_SECONDS = 0.8

# "Who is this?" reveal timing rewrite (2026-09-16): rather than the flat
# qmark-cycle above, panels 3-6 now cascade in one option at a time, in
# button order (config.SIMON_HW_COLOR_ORDER), while panels 1+2 step through
# a sparkle "Who is this?" -> solid "Is this:" beat first -- see
# drivers/mystery_band_engine.py::reveal_stage(). Purely additive to the
# timing above: MYSTERY_REVEAL_TIMEOUT_SECONDS is still the total unanswered
# budget: SPARKLE + SOLID + 3 * CASCADE_STEP (~6.1s) is spent revealing the
# 4th and final option, leaving most of the remaining 15s for people to
# actually answer, same total window as before this reveal was staged.
MYSTERY_SPARKLE_SECONDS = 1.5       # "Who is this?" sparkle
MYSTERY_SOLID_SECONDS = 1.0         # "Is this:" beat, nothing revealed yet
MYSTERY_CASCADE_STEP_SECONDS = 1.2  # time between each option's reveal

# Same per-option cascade build-up (matrix: options appear one at a time;
# marquee: theater-chase in that panel's own button color -- see
# drivers/factoid_engine.py::question_reveal_count(), graphics/
# matrix_canvas.py::_render_quiz_mode(), drivers/wled_engine.py::
# _apply_question_marquee()), now applied to EVERY Game Mode question
# (2026-09-18), not just the once-per-song mystery teaser above. No
# sparkle/solid pre-roll here -- a normal question's own text already
# displays immediately, there's no "Who is this?" suspense to build
# first -- and the per-option step is deliberately much shorter than
# MYSTERY_CASCADE_STEP_SECONDS (1.2s) since this now happens far more
# often than once per song: 4 options fully revealed in ~1.4s total
# instead of the mystery reveal's ~7.3s.
QUESTION_CASCADE_STEP_SECONDS = 0.35

# Question-priority hierarchy for the questions queued after the forced
# "identify this band" question, once Game Mode is entered from a live
# Mystery Band window. Categories not listed here (e.g. career-stat,
# song-meaning) sort after all of these, in their original queue order.
MYSTERY_CATEGORY_PRIORITY = {"geography": 0, "date": 1, "true_false": 2, "real_name": 3}

# Solo (no registered players) "score for fun at the panel" mode
# (2026-09-17, operator feedback): drivers/factoid_engine.py::
# advance_to_next_queued_question() skips any queued question whose
# category is in this set when nobody's registered -- someone standing
# right up against an 18px matrix panel has a much more limited view than
# a phone screen, so open-ended categories like "song_meaning" ("what is
# this song about?") read as too long/hard to size up at a glance. Left in
# the queue rather than discarded, so a player joining mid-track still
# gets the full, unfiltered multiplayer queue (that's "fine" there per the
# same feedback -- phones have room for the longer text).
PANEL_SOLO_EXCLUDED_CATEGORIES = {"song_meaning"}

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
# Directory, not a fixed file list (2026-08-12 -- moved off a hardcoded
# 3-file list in audio/sound_effects/ that had silently drifted out of
# sync with what was actually on disk there, a 4th and 5th track sitting
# unused in a separate audio/Game_music/ folder). audio/audio_engine.py::
# _pick_game_music() globs this directory for every *.wav in it and deals
# through them like a shuffled deck (see drivers/deck_orchestrator.py::
# _pick_announcement(), the same pattern already used for announcements),
# so dropping a new track in this folder picks it up automatically with no
# code change, and the tracks don't have to follow any particular naming
# convention.
GAME_MUSIC_DIR = resource_path("audio", "gameMusic")

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
# Price Game board layout. The banner is only ROW_WIDTH (64px) across and
# the item name gets one line of it, so the AI is asked for an abbreviated
# name ("Doz. Eggs", "Gal. Gas") rather than a full one -- the display
# hard-truncates anything longer instead of scrolling it, since a moving
# line is unreadable in the second or two the room gets to look at it.
PRICE_GAME_ITEM_MAX_CHARS = 12
# At/above this price the cents are dropped ($149.99 -> $149): on a 32px
# panel the extra glyphs were what pushed prices into scrolling.
PRICE_GAME_WHOLE_DOLLAR_MIN = 100.0

# Hand-editable local question bank (drivers/price_bank_engine.py): one row
# per year from PRICE_GAME_MIN_YEAR to PRICE_GAME_MAX_YEAR (2026-08-12 --
# generated once via a one-off script, editable afterward like
# light_prefs.csv/announcement_text.csv). This is what Btn6 draws from now --
# it never touches the network, so it can't fail/fall back into a Mystery
# Band question the way the old AI-fetch-dependent Price Game path could.
# See PRICE_GAME_CATEGORIES below for the AI-fetch path's own category list
# (still used by the web remote's "Force Game Mode -> Price Game" control).
PRICE_GAME_BANK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "price_game")
PRICE_GAME_BANK_PATH = os.path.join(PRICE_GAME_BANK_DIR, "price_game_bank.csv")

PRICE_GAME_CATEGORIES = [
    {
        "label": "Household Goods",
        # "Paper towels" deliberately left off this list (2026-08-12) --
        # reported as the AI's default go-to pick almost every round
        # despite the other examples listed alongside it.
        "examples": "laundry detergent, dish soap, trash bags, a light bulb, a bar of hand soap, a roll of postage stamps",
    },
    {
        "label": "Pantry / Groceries",
        "examples": "a pound of ground hamburger, a gallon of milk, a box of cereal, a can of coffee, a loaf of bread, a dozen eggs",
    },
    {
        "label": "Entertainment / Electronics",
        "examples": "a color television set, a home stereo system, an LP vinyl record, a Walkman, a pack of batteries, a blank VHS tape",
    },
    {
        "label": "Apparel / Footwear",
        "examples": "a pair of name-brand sneakers, a pair of denim jeans, a t-shirt, a winter coat",
    },
    {
        # New category (2026-08-12), covering the classic "cost of living"
        # / economic-benchmark items requested directly -- gas, cars, and
        # other big-ticket everyday-economy staples that don't fit neatly
        # into the categories above.
        "label": "Big-Ticket / Cost of Living",
        "examples": "a new car, a gallon of gasoline, a movie theater ticket, a monthly apartment rent, a new home, a fast food combo meal",
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

# ==========================================
# POWER MONITOR (2026-08-17): BROWNOUT/UNDER-VOLTAGE LOGGING
# ==========================================
# Diagnostic aid for the occasional live-show brownout on the show Pi --
# drivers/power_monitor.py polls `vcgencmd get_throttled` on this interval
# and prints a timestamped line (captured into startup.log by pi_deploy/
# start.sh) the instant the Pi's own power-management chip reports an
# under-voltage condition, tagged with whatever track was playing at that
# moment. Lets a brownout be lined up against the "[AUTO-DJ] Tracking ..."
# lines already in the same log to check whether rapid track
# changes/seeks are actually the trigger. No-op on anything that isn't
# Linux with vcgencmd on PATH (the Windows dev machine included), so this
# is always safe to poll from main.py's loop regardless of host.
POWER_MONITOR_POLL_SECONDS = 2.0

# LED_BROWNOUT_GUARD_* removed 2026-09-02 -- was a field-emergency software
# mitigation (drivers/led_bridge.py dropped pixels on full/near-full-matrix
# flashes to cap simultaneous-LEDs-on current draw) for touring rig
# brownouts on an underpowered 5V supply. Superseded by installing the
# higher-wattage DC converter this was always meant to be a stopgap for
# (see the removed comment's own "the real fix is a beefier 5V supply"
# note) -- the matrix now renders whatever's commanded at full brightness,
# no pixel-dropping.

# ==========================================
# MAIN-LOOP STALL LOGGING (2026-08-19)
# ==========================================
# Diagnostic aid for the recurring ~4s LED-heartbeat/DMX-ready flapping on
# the show Pi (drivers/led_bridge.py's _HEARTBEAT_TIMEOUT_S window) --
# main.py times each stage of its per-frame loop (input processing, DMX
# render, LED matrix render/send, etc.) and prints a line naming the
# worst-offending stage the instant one iteration takes noticeably longer
# than its 25ms budget (40fps). The goal is to catch the NEXT occurrence
# with hard per-stage numbers in startup.log instead of having to infer
# which subsystem stalled from symptoms alone. 300ms is deliberately loose
# (12x the normal 25ms frame budget) so ordinary GC/scheduler jitter stays
# quiet and only a real stall -- this one's suspected to run ~4 FULL
# seconds -- actually logs.
MAIN_LOOP_STALL_WARN_SECONDS = 0.3

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

# How long the "a ON" / "a OFF" toggle confirmation overlays panel 3.
AUTODJ_TOGGLE_OVERLAY_SECONDS = 1.5

# ==========================================
# AUTO-DJ: STATION ANNOUNCEMENT VOICE-OVERS
# ==========================================
# Auto-DJ track transitions overlay a station-announcement clip from this
# folder (drivers/deck_orchestrator.py::_pick_announcement()). Shuffled-deck
# selection, not independent random -- every clip plays once before any of
# them repeat, then the deck reshuffles and goes again.
ANNOUNCEMENTS_DIR = resource_path("audio", "announcements")

# Native playback library (replaces rekordbox.xml as the source of truth --
# see drivers/music_library.py). MUSIC_DIR holds the actual party library;
# SWEEPERS_DIR holds the short radio-style "whoosh" one-shots that lead into
# an announcement (audio/dj_engine.py::play_sweeper_and_announcement).
MUSIC_DIR = resource_path("audio", "music")
SWEEPERS_DIR = resource_path("audio", "sweepers")

# ==========================================
# YOUTUBE IMPORT (Library page, operator-only -- drivers/youtube_import_engine.py)
# ==========================================
# Audience song-request feature: paste a URL, a background job downloads +
# converts it with yt-dlp and drops the MP3 into MUSIC_DIR like any other
# upload. Rejected outright (no download attempted) if the source video
# runs longer than this -- keeps a mis-pasted URL (e.g. a full concert or
# a podcast) from tying up the import worker for several minutes mid-show.
YOUTUBE_IMPORT_MAX_DURATION_SECONDS = 1200  # 20 minutes
# Matches typical DJ-pool MP3 quality -- no reason to import at anything
# higher than what the rest of the library already sounds like.
YOUTUBE_IMPORT_MP3_QUALITY_KBPS = 192

# ==========================================
# HOT TRACK (audience YouTube import priority treatment, 2026-08-15)
# ==========================================
# Gate: the entire hot-track treatment (priority prefetch + confirmation
# flash + guaranteed fallback quiz + auto-cue) only applies if the
# CURRENTLY PLAYING track has at least this much runway left at the moment
# the import job starts (drivers/hot_track_engine.py::arm()). Below this,
# the import still happens, it just proceeds as a plain, unhurried library
# addition -- no priority prefetch, no flash, and no auto-cue-on-import.
HOT_TRACK_MIN_REMAINING_SECONDS = 90.0

# Cue-placement barrier, checked separately once the import is actually
# ready to cue (drivers/hot_track_engine.py::place_cue()): if fewer than
# this many seconds are left on the current track at that moment, the
# imported track does NOT jump the already-queued "next" pick -- it's
# placed in the slot AFTER that one instead (deck_orchestrator's 2-deep
# cue queue).
HOT_TRACK_CUE_BARRIER_SECONDS = 30.0

# Confirmation flash (graphics/matrix_canvas.py::_draw_hot_track_flash):
# 4 alternating reverse/normal phases of this length each -- 4x this is the
# total flash duration (1.0s at the default).
HOT_TRACK_FLASH_PHASE_SECONDS = 0.25

# ==========================================
# AUTO-ANNOUNCEMENT TOGGLE (Gamepad Btn1, DJ mode only)
# ==========================================
# On by default at launch; Gamepad Btn1 (inputs/gamepad.py, JOYBUTTONDOWN
# index 0, DJ mode only) toggles it. See drivers/auto_dj_engine.py.
AUTO_ANNOUNCE_ENABLED_BY_DEFAULT = True

# How long the "v ON" / "v OFF" toggle confirmation overlays panel 3.
AUTO_ANNOUNCE_TOGGLE_OVERLAY_SECONDS = 1.5

# ==========================================
# GAME SFX ADMIN TOGGLE (operator web remote)
# ==========================================
# Gates raw_ding/raw_buzzer/raw_bigwin/raw_coin/raw_buzz_short in
# inputs/gamepad.py -- lets the operator mute quiz sound effects for a
# quiet venue without touching the music deck volume.
SFX_ENABLED_BY_DEFAULT = True

# ==========================================
# DJ BRANDING ASSEMBLY ANIMATION (top strip, panels 1+2)
# ==========================================
# Shares BRANDING_OVERLAY_INTERVAL_SECONDS/BRANDING_OVERLAY_DURATION_SECONDS
# above for when it fires -- this just controls how long the fly-in/settle
# takes before the assembled string holds steady for the rest of that window.
BRANDING_ASSEMBLY_DURATION_SECONDS = 0.8

# ==========================================
# GAMEPAD BTN1/BTN3 TAP-VS-HOLD OVERLAYS (Feature Update)
# ==========================================
# Btn1 (index 0, DJ mode): tap toggles Auto-Announcement (unchanged).
# Holding past this threshold instead shows a live status overlay (star =
# questions available, dollar = Price Game available, red arrow = AI
# exhausted/no questions) on panel 3. See inputs/gamepad.py's
# _process_btn1_hold()/_handle_btn1_release().
BTN1_HOLD_THRESHOLD_SECONDS = 0.35

# On release of a genuine hold, the status overlay persists this long
# afterward instead of hiding immediately -- a quick re-tap of Btn1 during
# this window dismisses it early without toggling Auto-Announcement.
BTN1_HOLD_OVERLAY_PERSIST_SECONDS = 10.0

# Mid/Large pulse cadence for the two-frame star/dollar/arrow animations
# (graphics/animations.py: anim_bold_star / anim_bold_dollar / anim_arrow_down).
BTN1_STATUS_PULSE_INTERVAL_SECONDS = 0.35

# Btn3 (index 2, DJ mode): tap opens the QR remote-control popup. Holding
# past this threshold instead overlays the session's total AI token usage
# on panel 3, visible strictly while held (hides immediately on release).
BTN3_HOLD_THRESHOLD_SECONDS = 0.35

# ==========================================
# LOCAL WEB REMOTE SERVER (Feature Update)
# ==========================================
# FastAPI/uvicorn server (web/remote_server.py) serving the mobile remote
# control page. Triggered by a Btn3 tap (see above), which pops up a QR
# code (web/qr_popup.py) linking to http://<LAN_IP>:<WEB_REMOTE_PORT>.
WEB_REMOTE_PORT = 8765

# QR popup auto-dismisses after this long, or instantly on a second Btn3 tap.
QR_POPUP_AUTO_DISMISS_SECONDS = 10.0

# Remote's "-10 Seconds" Auto-DJ button: how far to pull the elapsed-track
# window forward (drivers/auto_dj_engine.py already reads
# state.auto_dj_track_started_at/duration every frame -- no new engine hook
# needed).
WEB_AUTODJ_SKIP_SECONDS = 10.0

# ==========================================
# STATIC QR / NON-WIFI GUEST ACCESS (2026-08-14)
# ==========================================
# The QR codes above (web/qr_popup.py, graphics/overlay_panel.py,
# /api/quiz/qr) normally encode http://<LAN_IP>:<WEB_REMOTE_PORT> -- fine
# for guests on the SAME WiFi as this machine, useless for anyone on
# cellular data or a venue with no WiFi at all, and it changes every
# venue/network so the QR can't be printed once and reused.
#
# drivers/tunnel_engine.py runs `cloudflared tunnel --url ...` as a
# background subprocess, giving the local web remote a public HTTPS
# address reachable from ANY network. Cloudflare's free "quick tunnel"
# mode needs no account/domain, but hands back a new random
# https://xxxx.trycloudflare.com URL every time it starts -- so instead of
# the QR encoding that directly, it encodes a URL on the OPERATOR'S OWN
# static web server (see cloudflare_redirect/ at the repo root, deployed
# separately) that 302-redirects to wherever the tunnel currently is.
# tunnel_engine.py reports the current tunnel URL to that redirector via a
# pre-shared-secret POST every time it changes, plus a periodic heartbeat.
#
# Leave the three URLs below blank to disable this entirely -- every QR
# call site falls back to the LAN-IP URL exactly as before this feature
# existed (see web/net_info.py::get_admin_url()/get_play_url()).
TUNNEL_REDIRECT_ADMIN_URL = "https://yannitellphotography.com/trivia/admin.php"
TUNNEL_REDIRECT_PLAY_URL = "https://yannitellphotography.com/trivia/play.php"
TUNNEL_REDIRECT_UPDATE_URL = "https://yannitellphotography.com/trivia/update.php"

# Must match cloudflare_redirect/config.php's SHARED_SECRET exactly --
# pre-filled here and in that file so they match out of the box; change
# both together if you ever want to rotate it.
TUNNEL_REDIRECT_SECRET = "m8YaRwCJ_cmRybVu0VGhNbKuvyU9T_xqw4c79ZjXQlQ"

# How often tunnel_engine.py re-POSTs the current tunnel URL even when it
# hasn't changed, so cloudflare_redirect/'s staleness check (STALE_AFTER_
# SECONDS in its config.php) never times out while the app is running.
TUNNEL_HEARTBEAT_SECONDS = 60.0

# How long tunnel_engine.py waits before respawning cloudflared after it
# exits (e.g. launched before the network was up on a cold boot). Short
# enough to come back quickly once the network's actually ready, long
# enough not to hammer subprocess spawns during a longer outage.
TUNNEL_RETRY_SECONDS = 10.0

# Remote's "+10 Seconds" Auto-DJ button: how far to push the elapsed-track
# window back (i.e. buy the DJ more time before the auto-advance transition
# arms) -- the mirror image of WEB_AUTODJ_SKIP_SECONDS above.
WEB_AUTODJ_ADD_SECONDS = 10.0

# ==========================================
# WEB REMOTE: MANUAL GAME-MODE TRIGGER + CATEGORY SELECTOR
# ==========================================
# Host-facing labels for the remote's manual "force Game Mode" category
# picker, mapped to the internal category keys already used to tag queued
# questions (drivers/factoid_engine.py's "category" field) / the Price
# Game / Mystery Band engines. See inputs/gamepad.py::force_game_mode().
WEB_GAME_CATEGORIES = [
    {"key": "price_game", "label": "Price Game"},
    {"key": "geography", "label": "Geography"},
    {"key": "true_false", "label": "True/False"},
    {"key": "band_name", "label": "Band Name"},
]

# ==========================================
# GAMEPAD SHUTDOWN COMBO (Feature Update)
# ==========================================
# Holding physical Btn5 AND Btn2 (pygame JOYBUTTONDOWN indices 5 and 1)
# together for this many continuous seconds triggers a graceful app
# shutdown -- see inputs/gamepad.py::_process_shutdown_combo(). Moved from
# Btn5+Btn1 to Btn5+Btn2 (joypad remap) once Btn1's own tap/hold overlay
# handling made Btn1 too "busy" a button to also carry the shutdown combo.
# Index for Btn5 corrected 2026-08-14 for the Pi's controller (confirmed
# live via the console's "Raw Button Pressed" log): the physical L
# shoulder -- what this whole app calls "Btn5" -- reports as raw index 5
# under this controller's Linux/SDL2 driver, not 4 like it did on Windows.
# See the same note by SI_EXIT_BUTTONS in the SPACE INVADERS section for
# the full story (Select/Start shifted even further, to 9/10).
SHUTDOWN_COMBO_BUTTONS = (5, 1)  # physical Btn5, Btn2
SHUTDOWN_COMBO_HOLD_SECONDS = 5.0

# ==========================================
# FORCE PRICE GAME COMBO
# ==========================================
# Holding physical Btn5 AND Btn6 (pygame JOYBUTTONDOWN indices 5 and 6 on
# this controller -- see the SHUTDOWN_COMBO_BUTTONS note above)
# together for this many continuous seconds force-starts a Price Game round
# -- see inputs/gamepad.py::_process_force_price_game_combo()/
# force_price_game(). A joystick X- axis long-press was tried first and
# reverted: the X-axis is edge-triggered straight to "Next Track" the
# instant it crosses the hold threshold, in the same JOYAXISMOTION handler,
# before any hold-duration check could ever tell a tap from a hold -- so
# holding it also fired an unwanted, audience-visible track transition
# every time. A two-button combo (same shape as the shutdown combo above)
# has no such side effect once each button's own individual tap action is
# suppressed while the other combo button is held (see the combo-forming
# check in inputs/gamepad.py's JOYBUTTONDOWN handling).
FORCE_PRICE_GAME_COMBO_BUTTONS = (5, 6)  # physical Btn5, Btn6
FORCE_PRICE_GAME_COMBO_HOLD_SECONDS = 2.0

# ==========================================
# WESTMINSTER "BAT CLOCK" TOP-OF-HOUR EVENT (Feature Update)
# ==========================================
# Automated top-of-the-hour show sequence (plus a manual test trigger from
# the desktop overlay panel and the web remote) -- see
# drivers/westminster_engine.py. Matrix phase machine: flash -> particle
# scatter -> digital time readout (kept abstract/simple rather than a
# literal analog clock face, since the rig is a 64x56 red-only LED matrix).
WESTMINSTER_FLASH_SECONDS = 0.4
WESTMINSTER_PARTICLE_SECONDS = 1.5
WESTMINSTER_READOUT_SECONDS = 4.0
WESTMINSTER_PARTICLE_COUNT = 24
WESTMINSTER_PARTICLE_SPEED_PX_PER_SEC = 22.0

# ==========================================
# SECONDARY HDMI FULLSCREEN MATRIX DISPLAY (FALLBACK CANVAS) (Feature Update)
# ==========================================
# The physical ESP32 LED matrix panels are sometimes unavailable for a show,
# so this opens a second, independent pygame window (via pygame._sdl2.video.
# Window -- pygame-ce supports true multi-window SDL2 sessions, unlike the
# single pygame.display.set_mode() window graphics/matrix_canvas.py already
# owns for the dev overlay simulator) dedicated to a high-visibility, scaled
# render of the same LED matrix output for an external monitor/projector.
# See graphics/secondary_canvas.py.
SECONDARY_CANVAS_ENABLED = True

# Exact window caption of the fallback display -- kept distinct from
# CANVAS_WINDOW_TITLE (the primary dev-overlay simulator) so the two are
# never confused by an operator's taskbar/alt-tab list.
SECONDARY_CANVAS_WINDOW_TITLE = "LED Matrix -- Fallback Display"

# Multi-monitor auto-placement: if a second monitor (Index 1, i.e. any
# monitor besides the primary -- see graphics/secondary_canvas.py::
# _get_monitor_rects()) is detected, the fallback canvas automatically opens
# borderless-fullscreen on it. With only one monitor connected, it instead
# stays off entirely unless this is flipped True, in which case it opens as
# a small movable/resizable window on the single monitor instead (useful for
# testing the fallback canvas on a dev machine with no second display).
SECONDARY_CANVAS_SHOW_ON_SINGLE_MONITOR = False

# Windowed fallback size (single-monitor case only) -- kept at the matrix's
# native 8:7 aspect ratio (MATRIX_WIDTH:MATRIX_HEIGHT = 64:56) at a 15x
# integer pixel scale so the LED-pixel look stays crisp even in dev testing.
SECONDARY_CANVAS_WINDOWED_SIZE = (960, 840)

# LED-pixel aesthetic: draws a thin dark gap between each scaled LED cell
# (same idea as PIXEL_SCALE/GAP on the primary simulator) so the fullscreen
# render still reads as a matrix of individual pixels rather than a smooth
# blur, and outlines each physical 32x16 panel's seam. Toggle either off for
# a cleaner/more solid-looking fill on a lower-resolution projector.
SECONDARY_CANVAS_SHOW_PIXEL_GRID = True
SECONDARY_CANVAS_PIXEL_GAP_PX = 2
SECONDARY_CANVAS_SHOW_PANEL_SEAMS = True

# DMX lightning FX (drivers/lighting_engine.py): kill all uplights instantly,
# then 1-3 fast strobe flashes, then fade fixture-index-2's UV channel
# (the first Rockville MINIRF4 uplighter -- the only fixture type in this
# rig with a UV channel at all; dmx_driver.py's actual Fixture 1 is a plain
# RGB win/loss indicator lamp with no UV channel) from 0 to 255.
WESTMINSTER_STROBE_MIN = 1
WESTMINSTER_STROBE_MAX = 3
WESTMINSTER_STROBE_INTERVAL_SECONDS = 0.1
WESTMINSTER_UV_RAMP_SECONDS = 2.0

# Audio routing: both DJ deck channel faders duck to 0, the chime plays,
# then faders restore to their pre-mute level. Skipped entirely (visual/DMX
# only) if Auto Voice (state.auto_announce_enabled) is OFF.
WESTMINSTER_AUDIO_DUCK_SECONDS = 0.25
WESTMINSTER_AUDIO_RESTORE_SECONDS = 0.5
# A folder, not a single file: audio/HourClockBell/ holds multiple bell
# samples (Hour1.wav, Hour2.wav, ...) and audio_engine.pick_random_chime()
# picks one at random each time the chime fires.
HOUR_CLOCK_BELL_DIR = resource_path("audio", "HourClockBell")

# ==========================================
# TRIVIA NIGHT SHOW FLOW (2026-08-13): Setup -> Countdown -> scripted open
# -> live show -> scripted close. See drivers/show_engine.py.
# ==========================================
TRIVIA_NIGHT_MUSIC_DIR = resource_path("audio", "TriviaNightMusic")
SHOW_START_MUSIC_PATH = os.path.join(TRIVIA_NIGHT_MUSIC_DIR, "ShowStart.mp3")
SHOW_END_MUSIC_PATH = os.path.join(TRIVIA_NIGHT_MUSIC_DIR, "ShowEnd.mp3")

# Planning-tool estimate only (Setup page's "~N rounds" readout) -- average
# real-world round length (gameplay only, not counting the intermission
# that follows it, which is added separately from the operator's own
# Intermission setting). Nothing else in the codebase depends on this; it's
# a rough per-night estimate, not a timer that drives actual gameplay pacing.
SHOW_AVG_ROUND_MINUTES = 10.0

# Setup page's contracted-time range slider: both handles snap to this many
# minutes, and the whole picker spans this many hours forward from "now".
SHOW_TIME_SLIDER_STEP_MINUTES = 30
SHOW_TIME_SLIDER_MAX_HOURS_AHEAD = 5.0

# Fixed show-open script, timed against ShowStart.mp3's own playback clock
# (not per-frame-derived -- these are the exact beats requested). Each
# entry is (seconds_into_clip, text) -- the LED slide-text machine in
# graphics/matrix_canvas.py slides the previous line out and this one in
# the instant its timestamp is reached. "Mark Yannitell!" flashes instead
# of holding steady (see SHOW_INTRO_FLASH_BEAT_INDEX below).
SHOW_INTRO_BEATS = [
    (2.6, "Do you know what song this is?"),
    (6.7, "Get your phone out and play along!"),
    (11.0, "It's Trivia Night!"),
    (13.7, "Here's the star of trivia night"),
    (17.05, "Mark Yannitell!"),
    (25.0, ""),               # LED clears -- gives the live host's spoken
                               # intro room to breathe once the last slide's
                               # text has made its point.
    (28.0, "Trivia Nite!"),   # LAST beat -- holds indefinitely (no window
                               # end, no slide-out). The intro no longer
                               # auto-ends at a fixed timestamp; it only
                               # ends via skip_intro() (Joy X-,
                               # inputs/gamepad.py), whenever the host's
                               # live speech over the ShowStart.mp3 music
                               # actually wraps up.
]
# Index into SHOW_INTRO_BEATS whose text flashes on/off instead of holding
# solid once slid in (the DJ-name reveal).
SHOW_INTRO_FLASH_BEAT_INDEX = 4
SHOW_INTRO_FLASH_PERIOD_SECONDS = 0.3

# DMX beat: hard flash all-white-to-black, then a second white flash
# transitioning into a green marquee chase that holds for the rest of the
# intro (reuses the existing chase pattern/color machinery in
# drivers/lighting_engine.py -- theme index 1's chase, forced green).
SHOW_INTRO_DMX_FLASH_AT_SECONDS = 10.77
SHOW_INTRO_DMX_FLASH_SECONDS = 0.15
# Animation period fed into drivers/lighting_engine.py::_dj_theme_frame()
# for the post-flash green marquee chase (a snappier pace than a normal
# tap-tempo-driven chase, for the "marquee lights" energy requested).
SHOW_INTRO_CHASE_PERIOD_SECONDS = 0.35

# No fixed intro end-time constant anymore -- the intro holds open-ended
# (SHOW_INTRO_BEATS' last beat has no window end) until the operator ends
# it manually via Joy X- (inputs/gamepad.py -> drivers/show_engine.py::
# skip_intro()), giving the live host's spoken intro as much room as they
# need instead of a fixed cutoff.

# How long ShowStart.mp3 fades out, and the deck's channel fader tweens
# back UP to state.music_volume (from wherever a previous show's outro
# left it at 0), right as _finish_intro() hands off into the first real
# track -- a quick crossfade-like handoff rather than either audio source
# hard-cutting.
SHOW_INTRO_HANDOFF_FADE_MS = 600

# How long a still-playing outro (ShowEnd.mp3 + applause) fades out if
# "Start Game"/schedule_start() re-enters "dark" before that outro ever
# got the chance to finish on its own (2026-08-20 fix) -- "dark" is
# supposed to be a deliberate SILENT pause, which a leftover outro track
# still audibly playing underneath it defeats entirely. Quick, not
# instant -- an abrupt hard cut reads as a glitch, same reasoning as the
# intro handoff fade above.
SHOW_DARK_ENTRY_FADE_MS = 600

# Show-close script: fade whatever's on the deck, play SHOW_END_MUSIC_PATH
# + a random applause clip together, LED champion banner, DMX twinkle for
# the song's duration, then blackout + a persistent contact-info banner
# until the next "Start Game".
SHOW_OUTRO_DECK_FADE_SECONDS = 2.0
SHOW_END_LED_CONTACT_TEXT = "DJ MARK TRIVIA 740-396-8036"
# DMX fade-to-black once ShowEnd.mp3 finishes (a smooth fade, unlike the
# intro's hard-cut flashes -- "when song stops, fade out DMX").
SHOW_OUTRO_DMX_FADE_SECONDS = 2.5

# Setup-phase LED text (state.show_phase == "setup"/"countdown") -- held
# statically on panels 1+2 while the operator configures the Setup page or
# waits on a scheduled start, from app launch until "Start Game" is pressed.
SHOW_SETUP_LED_TEXT = "READY"

# Unattended-autoplay fallback (2026-08-19): if the Setup/"READY" screen
# sits untouched -- no physical rig input, no web remote action -- for this
# long, the show starts itself: straight to live/the first track, no dark
# pause, no ShowStart.mp3/LED intro choreography, so the room doesn't sit
# on dead air if the host hasn't shown up yet. See drivers/show_engine.py::
# _update_unattended_autoplay().
SHOW_UNATTENDED_AUTOPLAY_TIMEOUT_SECONDS = 60.0

# Blink rate for the "AUTO :NN" countdown banner (panels 1+2) and the
# final-10s "SHOW WILL START NOW" panels (3-6) -- shared by both so they
# flash in sync rather than drifting against each other. 0.5s on/off (1s
# full cycle) -- clearly noticeable without being frantic.
SHOW_UNATTENDED_FLASH_PERIOD_SECONDS = 0.5

# Inside this many remaining seconds, panels 3-6 (normally the DMX/LED/
# tunnel/network status chips) switch to a flashing "SHOW WILL START NOW",
# one word per panel -- an unmissable last warning right before the
# unattended-autoplay fallback actually fires.
SHOW_UNATTENDED_FINAL_WARNING_SECONDS = 10.0

# How long the deck fades out when the host hits "Reset to Setup" on an
# unattended-autoplay show (web remote only, live-panel banner) -- no
# outro fanfare, since this wasn't a real hosted show, just a fast fade
# back to the blank Setup screen.
SHOW_UNATTENDED_RESET_FADE_SECONDS = 2.0

# Setup-countdown physical-button interactions (2026-09-16): green opens a
# "START SHOW NOW?" confirm (panel3/green=No, panel4/red=Yes -- see
# drivers/simon_engine.py::_poll_setup_hardware()); blue kicks off a
# Bluetooth gamepad reconnect (drivers/bluetooth_engine.py::
# reconnect_paired_devices_async()) and holds its CONNECTING/SUCCESS/NO
# GAMEPAD feedback on panels 1+2 for this long once it resolves.
GAMEPAD_CONNECT_FEEDBACK_HOLD_SECONDS = 3.0
# Safety ceiling for the "CONNECTING..." message shown the instant blue is
# pressed, before the background reconnect attempt (which can itself take
# up to _LIST_TIMEOUT_S + _CONNECT_TIMEOUT_S per paired device, drivers/
# bluetooth_engine.py) has resolved -- so a wedged/never-returning attempt
# can't leave "CONNECTING..." on the panels forever. reconnect_paired_
# devices_async() overwrites this with a fresh GAMEPAD_CONNECT_FEEDBACK_
# HOLD_SECONDS window the moment it actually finishes, whichever comes first.
GAMEPAD_CONNECT_PENDING_MAX_SECONDS = 15.0

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
