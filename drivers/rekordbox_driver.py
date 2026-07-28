import os
import re
import json
import time
import queue
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
        state.active_deck = 1

        self._running = False
        self._thread = None
        self.db = RekordboxDbLookup()

        # RECALIBRATED TITLE CROP BOUNDS
        self.deck1_bounds = (0.290, 0.045, 0.280, 0.035)
        self.deck2_bounds = (0.290, 0.545, 0.280, 0.035)

        # EXACT USER PINPOINT: X=960, Y=587 (verified against screenshot:
        # dead-center of Deck 1's STEM fader column, stable blue #1473eb
        # across the full y=478-522 band vs Deck 2's grey #323232)
        self.fader_pixel_x = 960
        self.fader_pixel_y = 587
        self._last_raw_pixel_rgb = None

        self._last_deck1_raw = ""
        self._last_deck2_raw = ""

        self._last_d1_bytes = None
        self._last_d2_bytes = None

        # ------------------------------------------
        # AI CLEANUP: cache + queue + worker thread
        # ------------------------------------------
        self._cleanup_cache = {}          # raw_ocr_string -> [title, artist]
        self._cleanup_cache_lock = threading.Lock()
        self._cleanup_queue = queue.Queue()
        self._cleanup_inflight = set()    # raw strings currently queued/running
        self._last_ai_call_time = {1: 0.0, 2: 0.0}  # per-deck rate limit
        self._cleanup_thread = None

        self._maybe_clear_cache()
        self._load_cleanup_cache()

        if config.AI_CLEANUP_ENABLED and requests is not None:
            self._cleanup_thread = threading.Thread(target=self._cleanup_worker, daemon=True)
            self._cleanup_thread.start()
            print("[AI CLEANUP] Background worker online.")
        elif config.AI_CLEANUP_ENABLED and requests is None:
            print("[AI CLEANUP] 'requests' package not installed — AI cleanup disabled. Run: pip install requests")
        else:
            print("[AI CLEANUP] Disabled (no ANTHROPIC_API_KEY set).")

    # ------------------------------------------
    # AI CLEANUP CACHE (disk persistence)
    # ------------------------------------------
    def _maybe_clear_cache(self):
        clear_flag = os.environ.get("CLEAR_AI_CACHE", "")
        if clear_flag and clear_flag == config.AI_CLEANUP_CACHE_CLEAR_SECRET:
            try:
                if os.path.exists(config.AI_CLEANUP_CACHE_PATH):
                    os.remove(config.AI_CLEANUP_CACHE_PATH)
                    print(f"[AI CLEANUP] Cache cleared via CLEAR_AI_CACHE secret: {config.AI_CLEANUP_CACHE_PATH}")
            except Exception as e:
                print(f"[AI CLEANUP] Failed to clear cache: {e}")

    def _load_cleanup_cache(self):
        try:
            if os.path.exists(config.AI_CLEANUP_CACHE_PATH):
                with open(config.AI_CLEANUP_CACHE_PATH, "r", encoding="utf-8") as f:
                    self._cleanup_cache = json.load(f)
                print(f"[AI CLEANUP] Loaded {len(self._cleanup_cache)} cached entries.")
        except Exception as e:
            print(f"[AI CLEANUP] Cache load failed (starting fresh): {e}")
            self._cleanup_cache = {}

    def _save_cleanup_cache(self):
        try:
            with self._cleanup_cache_lock:
                snapshot = dict(self._cleanup_cache)
            with open(config.AI_CLEANUP_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, indent=2)
        except Exception as e:
            print(f"[AI CLEANUP] Cache save failed: {e}")

    # ------------------------------------------
    # AI CLEANUP WORKER
    # ------------------------------------------
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

        # Per-deck rate limit: skip queuing if we called too recently for this deck
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

                    # Only overwrite live state if this deck hasn't already
                    # moved on to a different raw OCR string in the meantime
                    current_raw = self._last_deck1_raw if deck_num == 1 else self._last_deck2_raw
                    if raw_str == current_raw:
                        if deck_num == 1:
                            state.deck1_track = cleaned
                        else:
                            state.deck2_track = cleaned
                        print(f"[AI CLEANUP] Deck {deck_num} (active deck is {state.active_deck}) '{raw_str}' -> {cleaned}")
                    else:
                        print(f"[AI CLEANUP] Result cached but STALE for deck {deck_num} — "
                              f"OCR moved on ('{current_raw}' != '{raw_str}'). Will apply next time this text is seen.")

            except Exception as e:
                print(f"[AI CLEANUP ERROR] {e}")
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
                "max_tokens": 60,
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
        if not raw_str or not raw_str.strip():
            return current_state_track

        # Filter code editor leak words
        lower_check = raw_str.lower()
        if any(system_kw in lower_check for system_kw in ["pycache", "init", "driver", "import", "def ", "class "]):
            return current_state_track

        db_match = self.db.query(raw_str)
        if db_match:
            title, artist = db_match
            return (self._truncate_text(title, 32), self._truncate_text(artist, 32))

        # Check AI cleanup cache before falling back to regex parsing
        with self._cleanup_cache_lock:
            cached = self._cleanup_cache.get(raw_str)
        if cached:
            return (cached[0], cached[1])

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
            return current_state_track

        # Regex pass is our immediate/interim value — queue the raw OCR
        # string for AI cleanup in the background in case it can do better
        self._queue_for_cleanup(raw_str, deck_num)

        return (short_title, short_artist)

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
        """Precision Pixel Monitor at absolute (X=960, Y=587)."""
        try:
            rx, ry = rect["left"], rect["top"]

            # Target exact pixel offset: X=960, Y=587
            pixel_crop = {
                "top": int(ry + self.fader_pixel_y),
                "left": int(rx + self.fader_pixel_x),
                "width": 1,
                "height": 1
            }

            sct_img = sct.grab(pixel_crop)
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
            r, g, b = img.getpixel((0, 0))

            # Detect color shifts dynamically
            if (r, g, b) != self._last_raw_pixel_rgb:
                self._last_raw_pixel_rgb = (r, g, b)
                
                # Blue indicator threshold check
                is_blue = (b > 120) and (b > r + 30)
                target_deck = 1 if is_blue else 2

                state.active_deck = target_deck
                active_track = state.deck1_track if target_deck == 1 else state.deck2_track
                print(f"[PIXEL LOCK 960,587] Color shifted -> RGB({r},{g},{b}) | DECK {target_deck} ACTIVE: {active_track}")

        except Exception as e:
            print(f"[FADER LOCK ERROR] {e}")

    def _ocr_crop_region(self, sct, rect, bounds, deck_num):
        rx, ry, rw, rh = rect["left"], rect["top"], rect["width"], rect["height"]
        top_r, left_r, width_r, height_r = bounds

        crop = {
            "top": int(ry + rh * top_r),
            "left": int(rx + rw * left_r),
            "width": int(rw * width_r),
            "height": int(rh * height_r)
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
            img.save("ocr_debug_deck1.png")
        else:
            self._last_d2_bytes = curr_bytes
            img.save("ocr_debug_deck2.png")

        inverted = ImageOps.invert(gray)
        thresh = inverted.point(lambda p: 255 if p > 140 else 0)

        raw_text = pytesseract.image_to_string(thresh, config="--psm 6").strip()

        lower_raw = raw_text.lower()
        if any(kw in lower_raw for kw in ["rekordbox", "driver", "def ", "import"]):
            return ""

        return raw_text

    def _poll_loop(self):
        print("[OCR DRIVER] Pixel-Locked Precision Loop Online (X=960, Y=587).")
        last_ocr_time = 0.0
        last_no_window_warn = 0.0

        with mss.mss() as sct:
            while self._running:
                try:
                    rect = self._get_rekordbox_window_rect()
                    if not rect:
                        now_warn = time.time()
                        if now_warn - last_no_window_warn > 3.0:
                            print("[WINDOW LOOKUP WARNING] Rekordbox window not found/confirmed "
                                  "by pygetwindow — falling back to primary monitor bounds for "
                                  "pixel-lock. Deck detection continues, but coordinates assume "
                                  "the window is at (0,0). Check window title/focus if this persists.")
                            last_no_window_warn = now_warn
                        m = sct.monitors[1]
                        rect = {"top": m["top"], "left": m["left"], "width": m["width"], "height": m["height"]}

                    # 1. Exact Pixel Crossfader Sampling (50Hz) — PRIME PRIORITY,
                    # always runs first every iteration regardless of OCR state.
                    self._detect_active_deck_visually(sct, rect)

                    # 2. Background Track Metadata Check (200ms throttle)
                    now = time.time()
                    if now - last_ocr_time >= 0.2:
                        last_ocr_time = now

                        deck1_raw = self._ocr_crop_region(sct, rect, self.deck1_bounds, 1)
                        deck2_raw = self._ocr_crop_region(sct, rect, self.deck2_bounds, 2)

                        if deck1_raw is not None and deck1_raw != self._last_deck1_raw:
                            self._last_deck1_raw = deck1_raw
                            parsed1 = self._parse_and_sanitize(deck1_raw, state.deck1_track, 1)
                            state.deck1_track = parsed1
                            print(f"[DECK 1 UPDATE] '{deck1_raw}' -> {parsed1}")

                        if deck2_raw is not None and deck2_raw != self._last_deck2_raw:
                            self._last_deck2_raw = deck2_raw
                            parsed2 = self._parse_and_sanitize(deck2_raw, state.deck2_track, 2)
                            state.deck2_track = parsed2
                            print(f"[DECK 2 UPDATE] '{deck2_raw}' -> {parsed2}")

                except Exception as e:
                    print(f"[OCR DRIVER ERROR] Top-level shield caught exception:")
                    traceback.print_exc()

                time.sleep(0.02)

    def start(self):
        if not self._running:
            self._running = True
            self._thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._thread.start()

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