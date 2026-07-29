import os
import re
import json
import time
import queue
import math
import threading
import traceback
import mss
import xml.etree.ElementTree as ET
from difflib import get_close_matches
from PIL import Image, ImageOps
import pytesseract

try:
    import requests
except ImportError:
    requests = None

from state import state
import config

try:
    import pygetwindow as gw
except ImportError:
    gw = None

POSSIBLE_TESSERACT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expanduser(r"~\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"),
]

for path in POSSIBLE_TESSERACT_PATHS:
    if os.path.exists(path):
        pytesseract.pytesseract.tesseract_cmd = path
        break


class RekordboxDbLookup:
    def __init__(self):
        self.tracks_db = []
        self.load_database()

    def load_database(self, custom_xml_path=None):
        cwd_xml = os.path.join(os.getcwd(), "rekordbox.xml")
        script_dir_xml = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "rekordbox.xml"))

        possible_xml_paths = [
            custom_xml_path, cwd_xml, script_dir_xml,
            os.path.expanduser(r"~\Documents\rekordbox.xml"),
            os.path.expanduser(r"~\OneDrive\Documents\rekordbox.xml"),
            os.path.expanduser(r"~\OneDrive\Desktop\rekordbox.xml"),
            r"C:\rekordbox.xml", os.path.expanduser(r"~\Desktop\rekordbox.xml")
        ]

        target_path = next((p for p in possible_xml_paths if p and os.path.exists(p)), None)

        if not target_path:
            return

        try:
            tree = ET.parse(target_path)
            root = tree.getroot()
            collection = root.find("COLLECTION")

            if collection is not None:
                for track in collection.findall("TRACK"):
                    title = track.get("Name", "").strip()
                    artist = track.get("Artist", "").strip()
                    if title:
                        clean_key = re.sub(r'[^A-Z0-9]', '', title.upper())
                        self.tracks_db.append((clean_key, title, artist))
        except Exception:
            pass

    def query(self, raw_ocr):
        if not raw_ocr or not self.tracks_db:
            return None

        ocr_key = re.sub(r'[^A-Z0-9]', '', raw_ocr.upper())
        if len(ocr_key) < 4:
            return None

        keys = [item[0] for item in self.tracks_db]
        matches = get_close_matches(ocr_key, keys, n=1, cutoff=0.75)

        if matches:
            matched_key = matches[0]
            for clean_key, orig_title, artist in self.tracks_db:
                if clean_key == matched_key:
                    return (orig_title, artist)
        return None


class RekordboxSanitizedOcrDriver:
    def __init__(self):
        state.deck1_track = ("READY FOR DECK 1", "")
        state.deck2_track = ("READY FOR DECK 2", "")
        state.deck1_confident = False
        state.deck2_confident = False
        state.active_deck = 1
        state.heartbeat_x = 0  # Absolute bottom-left dot offset (0..15)

        self._running = False
        self._thread = None
        self._heartbeat_thread = None
        self.db = RekordboxDbLookup()

        self.deck1_bounds = (0.290, 0.045, 0.280, 0.035)
        self.deck2_bounds = (0.290, 0.545, 0.280, 0.035)

        # Per-deck (dx, dy) pixel nudge applied to the OCR crop AFTER the ratio
        # maths, for fine alignment a fractional ratio can't express cleanly --
        # a ratio tweak drifts as the rekordbox window is resized, a pixel
        # offset doesn't. It moves the crop's TOP-LEFT corner and grows
        # width/height to compensate, so the right/bottom edges stay put and
        # the capture is always a superset of the old one.
        #
        # Deck 1's top-left was landing 5px too far right, clipping the first
        # glyph of the title, hence dx = -5.
        self.deck1_crop_offset = (-5, 0)
        self.deck2_crop_offset = (0, 0)

        self.fader_pixel_x = 960
        self.fader_pixel_y = 594
        self._last_raw_pixel_rgb = None

        self._last_deck1_raw = ""
        self._last_deck2_raw = ""

        self._last_d1_bytes = None
        self._last_d2_bytes = None

        self._cleanup_cache = {}
        self._cleanup_cache_lock = threading.Lock()
        self._cleanup_queue = queue.Queue()
        self._cleanup_inflight = set()
        self._last_ai_call_time = {1: 0.0, 2: 0.0}
        self._cleanup_thread = None

        self._maybe_clear_cache()
        self._load_cleanup_cache()

        if config.AI_CLEANUP_ENABLED and requests is not None:
            self._cleanup_thread = threading.Thread(target=self._cleanup_worker, daemon=True)
            self._cleanup_thread.start()

    def _maybe_clear_cache(self):
        clear_flag = os.environ.get("CLEAR_AI_CACHE", "")
        if clear_flag and clear_flag == config.AI_CLEANUP_CACHE_CLEAR_SECRET:
            try:
                if os.path.exists(config.AI_CLEANUP_CACHE_PATH):
                    os.remove(config.AI_CLEANUP_CACHE_PATH)
            except Exception:
                pass

    def _load_cleanup_cache(self):
        try:
            if os.path.exists(config.AI_CLEANUP_CACHE_PATH):
                with open(config.AI_CLEANUP_CACHE_PATH, "r", encoding="utf-8") as f:
                    self._cleanup_cache = json.load(f)
        except Exception:
            self._cleanup_cache = {}

    def _save_cleanup_cache(self):
        try:
            with self._cleanup_cache_lock:
                snapshot = dict(self._cleanup_cache)
            with open(config.AI_CLEANUP_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, indent=2)
        except Exception:
            pass

    def _queue_for_cleanup(self, raw_str, deck_num):
        if not config.AI_CLEANUP_ENABLED or requests is None:
            return
        if not raw_str or len(raw_str.strip()) < 3:
            return

        with self._cleanup_cache_lock:
            if raw_str in self._cleanup_cache:
                return
        if raw_str in self._cleanup_inflight:
            return

        now = time.time()
        if now - self._last_ai_call_time.get(deck_num, 0.0) < config.AI_CLEANUP_MIN_GAP_SECONDS:
            return

        self._cleanup_inflight.add(raw_str)
        self._cleanup_queue.put((raw_str, deck_num))

    def _cleanup_worker(self):
        while True:
            raw_str, deck_num = self._cleanup_queue.get()
            try:
                self._last_ai_call_time[deck_num] = time.time()
                cleaned = self._ai_clean_title(raw_str)

                if cleaned:
                    with self._cleanup_cache_lock:
                        self._cleanup_cache[raw_str] = list(cleaned)
                    self._save_cleanup_cache()

                    current_raw = self._last_deck1_raw if deck_num == 1 else self._last_deck2_raw
                    if raw_str == current_raw:
                        if deck_num == 1:
                            state.deck1_track = cleaned
                            state.deck1_confident = True
                        else:
                            state.deck2_track = cleaned
                            state.deck2_confident = True
            # A silent failure here is expensive: no cleanup means the deck
            # never becomes "confident", which means no factoid or quiz
            # question is ever requested for the track. Report the API's own
            # message so a bad key / exhausted balance / rejected parameter is
            # visible instead of looking like the AI simply had nothing to say.
            except requests.exceptions.HTTPError as e:
                resp = e.response
                status = resp.status_code if resp is not None else None
                body = ""
                if resp is not None:
                    try:
                        body = str((resp.json().get("error") or {}).get("message", ""))[:200]
                    except Exception:
                        body = (resp.text or "")[:200]
                print(f"[AI CLEANUP FAILED] HTTP {status} on {raw_str!r}: {body}")
            except Exception as e:
                print(f"[AI CLEANUP FAILED] {type(e).__name__} on {raw_str!r}: {e}")
            finally:
                self._cleanup_inflight.discard(raw_str)

    def _ai_clean_title(self, raw_str):
        if not config.ANTHROPIC_API_KEY:
            return None

        prompt = (
            "Clean this OCR-scanned DJ track display text into a clean "
            "'Title|Artist' format. Fix obvious OCR errors. Reply with "
            "ONLY the cleaned 'Title|Artist' text, nothing else, no "
            "explanation. If no artist is present, leave it blank after "
            f"the pipe. Raw text: {raw_str}"
        )

        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": config.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": config.AI_CLEANUP_MODEL,
                "max_tokens": 120,
                # Sonnet 5 runs adaptive thinking by default when "thinking"
                # is omitted, and max_tokens caps thinking + output COMBINED.
                # On a tiny budget that can burn the whole allowance before a
                # single character of 'Title|Artist' is written, so this
                # returns empty text -> no cleanup -> the deck never becomes
                # "confident" -> no factoid or quiz question is ever requested
                # for the track. Cleanup is a formatting task, not a reasoning
                # task, so disabling thinking is both correct and cheaper.
                "thinking": {"type": "disabled"},
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=config.AI_CLEANUP_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
        text = "".join(
            block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
        ).strip()

        if not text:
            return None

        title, _, artist = text.partition("|")
        title = self._truncate_text(title.strip(), 32)
        artist = self._truncate_text(artist.strip(), 32) if artist.strip() else ""

        if len(title) < 2:
            return None

        return (title, artist)

    def _truncate_text(self, text, max_chars=32):
        if len(text) > max_chars:
            return text[:max_chars - 3].strip() + "..."
        return text

    def _parse_and_sanitize(self, raw_str, current_state_track, deck_num):
        current_confident = state.deck1_confident if deck_num == 1 else state.deck2_confident

        if not raw_str or not raw_str.strip():
            return current_state_track[0], current_state_track[1], current_confident

        lower_check = raw_str.lower()
        if any(system_kw in lower_check for system_kw in ["pycache", "init", "driver", "import", "def ", "class "]):
            return current_state_track[0], current_state_track[1], current_confident

        db_match = self.db.query(raw_str)
        if db_match:
            title, artist = db_match
            return (self._truncate_text(title, 32), self._truncate_text(artist, 32), True)

        with self._cleanup_cache_lock:
            cached = self._cleanup_cache.get(raw_str)
        if cached:
            return (cached[0], cached[1], True)

        clean = raw_str.replace('\n', ' ').strip()
        clean = re.sub(r'^[é\.,~—_\|\s]+', '', clean)
        clean = re.sub(r'[_\r\n\t\|\\/~#?]+', ' ', clean)
        clean = re.sub(r'\b\d{2,3}\.\d{2}\b', '', clean)
        clean = re.sub(r'\b\d{1,2}[AB]\b', '', clean)

        junk_patterns = [
            r'\[.*?(EXTENDED|INTRO|CLEAN|DIRTY|CLUB|EDIT|REMASTER|BOOTLEG|RIP|OUTRO).*?\]',
            r'\(.*?(EXTENDED|INTRO|CLEAN|DIRTY|CLUB|EDIT|REMASTER|BOOTLEG|RIP|OUTRO).*?\)',
            r'\b(EXTENDED MIX|INTRO CLEAN|INTRO DIRTY|CLUB EDIT)\b'
        ]
        for pattern in junk_patterns:
            clean = re.sub(pattern, '', clean, flags=re.IGNORECASE)

        clean = re.sub(r'\s+', ' ', clean).strip(' -–—.,')

        artist = ""
        title = clean

        if " - " in clean or " – " in clean:
            parts = re.split(r'\s+[\-\–\—]\s+', clean, maxsplit=1)
            if len(parts) == 2 and parts[1].strip():
                part1, part2 = parts[0].strip(' -–—'), parts[1].strip(' -–—')
                artist = part1
                title = part2

        short_title = self._truncate_text(title, max_chars=32)
        short_artist = self._truncate_text(artist, max_chars=32) if artist else ""

        if len(short_title) < 3:
            return current_state_track[0], current_state_track[1], current_confident

        self._queue_for_cleanup(raw_str, deck_num)

        return (short_title, short_artist, False)

    def _get_rekordbox_window_rect(self):
        if gw:
            try:
                wins = gw.getWindowsWithTitle('rekordbox')
                if wins and wins[0].width > 100:
                    w = wins[0]
                    if not w.isMinimized:
                        return {"top": w.top, "left": w.left, "width": w.width, "height": w.height}
            except Exception:
                pass
        return None

    def _detect_active_deck_visually(self, sct, rect):
        """HYPER-SENSITIVE PIXEL WATCH: ANY RGB shift forces immediate deck toggle."""
        try:
            rx, ry = rect["left"], rect["top"]

            pixel_crop = {
                "top": int(ry + self.fader_pixel_y),
                "left": int(rx + self.fader_pixel_x),
                "width": 1,
                "height": 1
            }

            sct_img = sct.grab(pixel_crop)
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            r, g, b = img.getpixel((0, 0))

            # INSTANT TOGGLE ON ANY PIXEL DIFFERENCE
            if self._last_raw_pixel_rgb is not None and (r, g, b) != self._last_raw_pixel_rgb:
                # Force swap deck immediately

                # state.active_deck = 2 if state.active_deck == 1 else 1
                state.active_deck = 2 if b < 200 else 1


                


                active_track = state.deck1_track if state.active_deck == 1 else state.deck2_track

                # print(f"[LIVE PIXEL TRIGGER ] RGB ({r},{g},{b}) -> FORCED SWAP TO DECK {state.active_deck}: {active_track}")

            self._last_raw_pixel_rgb = (r, g, b)

        except Exception as e:
            print(f"[FADER LOCK ERROR] {e}")

    def _heartbeat_loop(self):
        """Oscillates state.heartbeat_x across 0..15 every 3 seconds."""
        while self._running:
            # 3 second period = 2*pi / 3 rad/sec
            t = time.time()
            cycle = (math.sin(t * (2.0 * math.pi / 3.0)) + 1.0) / 2.0  # Normalized 0.0 to 1.0
            state.heartbeat_x = int(cycle * 15)
            time.sleep(0.02)

    def _ocr_crop_region(self, sct, rect, bounds, deck_num):
        rx, ry, rw, rh = rect["left"], rect["top"], rect["width"], rect["height"]
        top_r, left_r, width_r, height_r = bounds

        dx, dy = self.deck1_crop_offset if deck_num == 1 else self.deck2_crop_offset

        crop = {
            "top": int(ry + rh * top_r) + dy,
            "left": int(rx + rw * left_r) + dx,
            # Grow by the same amount we shifted so the right/bottom edges
            # hold still; max(1, ...) keeps mss from choking on a 0px grab.
            "width": max(1, int(rw * width_r) - dx),
            "height": max(1, int(rh * height_r) - dy)
        }

        sct_img = sct.grab(crop)
        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        gray = img.convert("L")

        curr_bytes = gray.tobytes()
        last_bytes = self._last_d1_bytes if deck_num == 1 else self._last_d2_bytes

        if curr_bytes == last_bytes:
            return None

        if last_bytes is not None and len(curr_bytes) == len(last_bytes):
            diff_count = sum(1 for a, b in zip(curr_bytes, last_bytes) if abs(a - b) > 30)
            if diff_count < (len(curr_bytes) * 0.03):
                return None

        if deck_num == 1:
            self._last_d1_bytes = curr_bytes
        else:
            self._last_d2_bytes = curr_bytes

        inverted = ImageOps.invert(gray)
        thresh = inverted.point(lambda p: 255 if p > 140 else 0)

        raw_text = pytesseract.image_to_string(thresh, config="--psm 6").strip()

        lower_raw = raw_text.lower()
        if any(kw in lower_raw for kw in ["rekordbox", "driver", "def ", "import"]):
            return ""

        return raw_text

    def _poll_loop(self):
        print("[OCR DRIVER] Hyper-Sensitive Pixel Engine & Heartbeat Active.")
        last_ocr_time = 0.0

        with mss.mss() as sct:
            while self._running:
                try:
                    rect = self._get_rekordbox_window_rect()
                    if not rect:
                        m = sct.monitors[1]
                        rect = {"top": m["top"], "left": m["left"], "width": m["width"], "height": m["height"]}

                    # 1. Exact Pixel Crossfader Sampling (50Hz)
                    self._detect_active_deck_visually(sct, rect)

                    # 2. Background Track Metadata Check (200ms throttle)
                    now = time.time()
                    if now - last_ocr_time >= 0.2:
                        last_ocr_time = now

                        deck1_raw = self._ocr_crop_region(sct, rect, self.deck1_bounds, 1)
                        deck2_raw = self._ocr_crop_region(sct, rect, self.deck2_bounds, 2)

                        if deck1_raw is not None and deck1_raw != self._last_deck1_raw:
                            self._last_deck1_raw = deck1_raw
                            title1, artist1, confident1 = self._parse_and_sanitize(deck1_raw, state.deck1_track, 1)
                            state.deck1_track = (title1, artist1)
                            state.deck1_confident = confident1
                            print(f"[DECK 1 UPDATE] '{deck1_raw}' -> {(title1, artist1)} (confident={confident1})")

                        if deck2_raw is not None and deck2_raw != self._last_deck2_raw:
                            self._last_deck2_raw = deck2_raw
                            title2, artist2, confident2 = self._parse_and_sanitize(deck2_raw, state.deck2_track, 2)
                            state.deck2_track = (title2, artist2)
                            state.deck2_confident = confident2
                            print(f"[DECK 2 UPDATE] '{deck2_raw}' -> {(title2, artist2)} (confident={confident2})")

                except Exception as e:
                    print(f"[OCR DRIVER ERROR] Shield caught exception:")
                    traceback.print_exc()

                time.sleep(0.02)

    def start(self):
        if not self._running:
            self._running = True
            self._thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._thread.start()
            self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
            self._heartbeat_thread.start()

    def stop(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def get_track(self):
        if state.active_deck == 1:
            return state.deck1_track
        return state.deck2_track

    def get_track_string(self):
        track = self.get_track()
        if track[1]:
            return f"{track[0]} - {track[1]}"
        return track[0]


rb_driver = RekordboxSanitizedOcrDriver()
rb_driver.start()

def get_rekordbox_track():
    return rb_driver.get_track()

def get_rekordbox_track_string():
    return rb_driver.get_track_string()

def trigger_next_track():
    pass

def shutdown_rekordbox_driver():
    rb_driver.stop()