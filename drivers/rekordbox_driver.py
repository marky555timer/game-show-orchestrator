import os
import re
import time
import threading
import mss
import xml.etree.ElementTree as ET
from difflib import get_close_matches
from PIL import Image, ImageOps
import pytesseract

from state import state

try:
    import pygetwindow as gw
except ImportError:
    gw = None

# Locate Tesseract executable across standard paths
POSSIBLE_TESSERACT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expanduser(r"~\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"),
]

tesseract_found = False
for path in POSSIBLE_TESSERACT_PATHS:
    if os.path.exists(path):
        pytesseract.pytesseract.tesseract_cmd = path
        tesseract_found = True
        print(f"[OCR DRIVER] Tesseract binary bound at: {path}")
        break

if not tesseract_found:
    print("[OCR DRIVER ERROR] Could not find tesseract.exe in standard paths!")


class RekordboxDbLookup:
    """Helper class to load and fuzzy-search Rekordbox XML export for clean metadata."""
    def __init__(self):
        self.tracks_db = []  # List of tuples: (search_key, original_title, artist)
        self.load_database()

    def load_database(self, custom_xml_path=None):
        cwd_xml = os.path.join(os.getcwd(), "rekordbox.xml")
        script_dir_xml = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "rekordbox.xml"))
        
        possible_xml_paths = [
            custom_xml_path,
            cwd_xml,
            script_dir_xml,
            os.path.expanduser(r"~\Documents\rekordbox.xml"),
            os.path.expanduser(r"~\OneDrive\Documents\rekordbox.xml"),
            os.path.expanduser(r"~\OneDrive\Desktop\rekordbox.xml"),
            r"C:\rekordbox.xml",
            os.path.expanduser(r"~\Desktop\rekordbox.xml")
        ]
        
        target_path = None
        for p in possible_xml_paths:
            if p and os.path.exists(p):
                target_path = p
                break

        if not target_path:
            print(f"[DB LOOKUP] WARN: No rekordbox.xml found! Placed search in active directory: '{os.getcwd()}'")
            print("[DB LOOKUP] To enable full database info, export 'rekordbox.xml' into your project folder.")
            return

        print(f"[DB LOOKUP] Found XML database at: '{target_path}'. Parsing...")
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
                print(f"[DB LOOKUP] SUCCESS: Indexed {len(self.tracks_db)} tracks from XML.")
            else:
                print("[DB LOOKUP] WARN: COLLECTION tag missing in XML.")
        except Exception as e:
            print(f"[DB LOOKUP ERROR] XML Parse Error: {e}")

    def query(self, raw_ocr):
        """Fuzzy-matches OCR string against indexed track collection."""
        if not raw_ocr or not self.tracks_db:
            print(f"[DB LOOKUP] Skipping DB lookup (raw_ocr='{raw_ocr}', db_len={len(self.tracks_db)})")
            return None

        ocr_key = re.sub(r'[^A-Z0-9]', '', raw_ocr.upper())
        print(f"[DB LOOKUP] Querying Key: '{ocr_key}' (from raw: '{raw_ocr}')")

        if len(ocr_key) < 3:
            print("[DB LOOKUP] Search key too short, skipping fuzzy match.")
            return None

        keys = [item[0] for item in self.tracks_db]
        matches = get_close_matches(ocr_key, keys, n=1, cutoff=0.50)

        if matches:
            matched_key = matches[0]
            for clean_key, orig_title, artist in self.tracks_db:
                if clean_key == matched_key:
                    print(f"[DB LOOKUP] MATCH FOUND! -> Title: '{orig_title}' | Artist: '{artist}'")
                    return (orig_title, artist)
        
        print(f"[DB LOOKUP] No fuzzy match found for '{ocr_key}' above cutoff threshold.")
        return None


class RekordboxSanitizedOcrDriver:
    def __init__(self):
        self.cached_track = ("READY FOR DECK", "")
        self.raw_cached_string = "READY FOR DECK"
        self._running = False
        self._thread = None

        self.db = RekordboxDbLookup()

        # Calibrated bounding boxes: (top_ratio, left_ratio, width_ratio, height_ratio)
        self.deck1_bounds = (0.291, 0.035, 0.260, 0.028)
        self.deck2_bounds = (0.291, 0.535, 0.260, 0.028)

        self._last_deck1_raw = ""
        self._last_deck2_raw = ""

    def _truncate_text(self, text, max_chars=32):
        if len(text) > max_chars:
            return text[:max_chars - 3].strip() + "..."
        return text

    def _parse_and_sanitize(self, raw_str):
        if not raw_str or not raw_str.strip():
            return ("READY FOR DECK", "")

        # 1. Filter out code/screen artifact noise leaks aggressively
        clean_check = re.sub(r'[^a-zA-Z0-9]', '', raw_str.lower())
        if "pycache" in clean_check or "sys" in clean_check or "oeoy" in clean_check:
            return (self.cached_track[0], self.cached_track[1])

        # 2. DB Lookup First
        db_match = self.db.query(raw_str)
        if db_match:
            title, artist = db_match
            return (self._truncate_text(title, 32), self._truncate_text(artist, 32))

        # 3. Fallback Regex Parsing
        print(f"[SANITY PARSER] Falling back to Regex parser for: '{raw_str}'")
        clean = raw_str.replace('\n', ' ').strip()
        clean = re.sub(r'[_\r\n\t\|\\/~#?]+', ' ', clean)

        clean = re.sub(r'\b\d{2,3}\.\d{2}\b', '', clean)
        clean = re.sub(r'\b\d{1,2}[AB]\b', '', clean)
        clean = re.sub(r'[\-\+]?\d{2}:\d{2}\.\d', '', clean)

        junk_patterns = [
            r'\[.*?(EXTENDED|INTRO|CLEAN|DIRTY|CLUB|EDIT|REMASTER|BOOTLEG|RIP|OUTRO).*?\]',
            r'\(.*?(EXTENDED|INTRO|CLEAN|DIRTY|CLUB|EDIT|REMASTER|BOOTLEG|RIP|OUTRO).*?\)',
            r'\b(EXTENDED MIX|INTRO CLEAN|INTRO DIRTY|CLUB EDIT)\b'
        ]
        for pattern in junk_patterns:
            clean = re.sub(pattern, '', clean, flags=re.IGNORECASE)

        clean = re.sub(r'\s+', ' ', clean).strip(' -–—')

        artist = ""
        title = clean

        if " - " in clean or " – " in clean:
            parts = re.split(r'\s+[\-\–\—]\s+', clean, maxsplit=1)
            if len(parts) == 2 and parts[1].strip():
                part1, part2 = parts[0].strip(' -–—'), parts[1].strip(' -–—')
                
                version_keywords = ["VERSION", "SINGLE", "REMIX", "MIX", "EDIT", "FEAT", "FT"]
                if any(kw in part2.upper() for kw in version_keywords):
                    title = f"{part1} ({part2})"
                    artist = ""
                else:
                    artist = part1
                    title = part2
            else:
                title = parts[0].strip(' -–—')

        elif " BY " in clean.upper():
            parts = re.split(r'\s+BY\s+', clean, flags=re.IGNORECASE, maxsplit=1)
            if len(parts) == 2 and parts[1].strip():
                title = parts[0].strip()
                artist = parts[1].strip()

        if title.isupper() or title.islower():
            title = title.title()
        if artist.isupper() or artist.islower():
            artist = artist.title()

        short_title = self._truncate_text(title, max_chars=32)
        short_artist = self._truncate_text(artist, max_chars=32) if artist else ""

        print(f"[SANITY PARSER] Result -> Title: '{short_title}' | Artist: '{short_artist}'")
        return (short_title if short_title else "READY FOR DECK", short_artist)

    def _get_rekordbox_window_rect(self):
        if gw:
            try:
                wins = gw.getWindowsWithTitle('rekordbox')
                if wins and wins[0].width > 100:
                    w = wins[0]
                    return {"top": w.top, "left": w.left, "width": w.width, "height": w.height}
            except Exception as e:
                print(f"[OCR DRIVER] Window lookup exception: {e}")
                pass
        return None

    def _detect_active_deck_visually(self, sct, rect):
        """Samples pixel just left of center notch on crossfader track for blue fill."""
        rx, ry, rw, rh = rect["left"], rect["top"], rect["width"], rect["height"]
        
        pixel_crop = {
            "top": int(ry + rh * 0.558),
            "left": int(rx + rw * 0.495),
            "width": 1,
            "height": 1
        }
        
        img = sct.grab(pixel_crop)
        
        # Safe tuple unpacking regardless of RGB or RGBA/BGRA return formats
        pixel = img.pixels[0][0]
        b, g, r = pixel[0], pixel[1], pixel[2]
        
        # High blue channel value on left side of notch means Deck 1 is active
        is_blue = (b > 160) and (b > r + 40)
        
        if is_blue:
            if state.active_deck != 1:
                state.active_deck = 1
                print(f"[CROSSFADER DETECT] -> DECK 1 ACTIVE (Blue Bar: B={b}, R={r})")
        else:
            if state.active_deck != 2:
                state.active_deck = 2
                print(f"[CROSSFADER DETECT] -> DECK 2 ACTIVE (No Blue Bar: B={b}, R={r})")

    def _ocr_crop_region(self, sct, rect, bounds):
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
        inverted = ImageOps.invert(gray)
        thresh = inverted.point(lambda p: 255 if p > 140 else 0)

        raw_text = pytesseract.image_to_string(thresh, config="--psm 6").strip()

        # Reject reads starting with invalid characters or heavy symbol garbage
        if raw_text.startswith(('?', '_', '.', '/')) or raw_text.lower().startswith('al it'):
            return ""

        clean_check = re.sub(r'[^a-zA-Z0-9]', '', raw_text)
        if len(clean_check) < 5 or "pycache" in raw_text.lower():
            return ""

        return raw_text

    def _poll_loop(self):
        print("[OCR DRIVER] Background OCR thread started.")
        with mss.mss() as sct:
            while self._running:
                try:
                    rect = self._get_rekordbox_window_rect()
                    if not rect:
                        m = sct.monitors[1]
                        rect = {"top": m["top"], "left": m["left"], "width": m["width"], "height": m["height"]}

                    # 1. Visually detect active deck from crossfader blue bar
                    self._detect_active_deck_visually(sct, rect)

                    # 2. Capture and parse OCR regions independently
                    deck1_raw = self._ocr_crop_region(sct, rect, self.deck1_bounds)
                    deck2_raw = self._ocr_crop_region(sct, rect, self.deck2_bounds)

                    # Update Deck 1 state independently
                    if deck1_raw and len(deck1_raw) > 2 and deck1_raw != self._last_deck1_raw:
                        self._last_deck1_raw = deck1_raw
                        print(f"\n[OCR DRIVER] DECK 1 RAW OCR READ: '{deck1_raw}'")
                        state.deck1_track = self._parse_and_sanitize(deck1_raw)

                    # Update Deck 2 state independently
                    if deck2_raw and len(deck2_raw) > 2 and deck2_raw != self._last_deck2_raw:
                        self._last_deck2_raw = deck2_raw
                        print(f"\n[OCR DRIVER] DECK 2 RAW OCR READ: '{deck2_raw}'")
                        state.deck2_track = self._parse_and_sanitize(deck2_raw)

                except Exception as e:
                    print(f"[OCR DRIVER ERROR] Exception in poll loop: {e}")

                time.sleep(0.3)

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
        """Returns active playing deck's tuple: (Song Title, Artist)"""
        if state.active_deck == 1:
            return state.deck1_track
        return state.deck2_track

    def get_track_string(self):
        """Returns string representation of the active playing deck."""
        track = self.get_track()
        if track[1]:
            return f"{track[0]} - {track[1]}"
        return track[0]


# Global Instance & Exports
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