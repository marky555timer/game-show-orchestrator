import csv
import os
import glob
import time
import threading

import config

_HEADER = ["filename", "text", "swear", "updated_at"]


def _swear_from_csv(raw):
    return (raw or "").strip().lower() == "true"


def _swear_to_csv(value):
    return "true" if value else "false"


class AnnouncementEngine:
    """Per-file announcement banner captions -- a table (filename ->
    {"text": banner caption, "swear": bool}) shown on the top 2 panels when
    that specific file plays, stored as CSV under
    config.ANNOUNCEMENT_TEXT_CACHE_PATH with one row per
    audio/announcements/*.wav|mp3 file (2026-08-10 redesign, replacing the
    original single-global-value design; swear column added 2026-08-14 for
    the Btn3 swear-tag toggle, see inputs/gamepad.py::
    toggle_last_announcement_swear()).

    Reconciled against that folder on startup (reconcile_with_audio_dir()):
    any audio file with no caption row yet gets a config.ANNOUNCEMENT_DEFAULT_TEXT
    ("GET READY") placeholder, so the operator hears the file, sees the
    obviously-a-placeholder caption on screen, and can replace it later via
    the admin panel's "Last Announcement Text" field -- which always reads/
    writes whichever file most recently played (state.last_announcement_filename,
    set by drivers/deck_orchestrator.py the instant a file is picked), not a
    single global value. CSV chosen (not JSON) so a venue operator can
    hand-edit the table directly outside the app."""

    def __init__(self):
        self._lock = threading.Lock()
        self._entries = self._load_cache()
        self.reconcile_with_audio_dir()

    def _load_cache(self):
        entries = {}
        try:
            if os.path.exists(config.ANNOUNCEMENT_TEXT_CACHE_PATH):
                with open(config.ANNOUNCEMENT_TEXT_CACHE_PATH, "r", encoding="utf-8", newline="") as f:
                    for row in csv.DictReader(f):
                        filename = (row.get("filename") or "").strip()
                        if filename:
                            entries[filename] = {
                                "text": row.get("text") or config.ANNOUNCEMENT_DEFAULT_TEXT,
                                "swear": _swear_from_csv(row.get("swear")),
                            }
        except Exception as e:
            print(f"[ANNOUNCEMENT] Failed to read cache, starting empty: {e}")
        return entries

    def _save_cache(self):
        try:
            os.makedirs(config.ANNOUNCEMENT_TEXT_DIR, exist_ok=True)
            with open(config.ANNOUNCEMENT_TEXT_CACHE_PATH, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=_HEADER)
                writer.writeheader()
                now_str = time.strftime("%Y-%m-%d %H:%M:%S")
                for filename, entry in sorted(self._entries.items()):
                    writer.writerow({
                        "filename": filename, "text": entry["text"],
                        "swear": _swear_to_csv(entry.get("swear")), "updated_at": now_str,
                    })
        except Exception as e:
            print(f"[ANNOUNCEMENT] Failed to save cache: {e}")

    def reconcile_with_audio_dir(self):
        """Adds a placeholder caption row for any audio/announcements/ file
        that doesn't have one yet. Idempotent -- only writes to disk if
        something actually changed, safe to call any time (e.g. after
        dropping new files in while the app is running)."""
        paths = (glob.glob(os.path.join(config.ANNOUNCEMENTS_DIR, "*.wav"))
                 + glob.glob(os.path.join(config.ANNOUNCEMENTS_DIR, "*.mp3")))
        added = 0
        with self._lock:
            for path in paths:
                filename = os.path.basename(path)
                if filename not in self._entries:
                    self._entries[filename] = {"text": config.ANNOUNCEMENT_DEFAULT_TEXT, "swear": False}
                    added += 1
        if added:
            self._save_cache()
            print(f"[ANNOUNCEMENT] Added {added} placeholder caption(s) for previously-uncatalogued file(s).")

    def get_text_for(self, filename):
        with self._lock:
            entry = self._entries.get(filename)
            return entry["text"] if entry else config.ANNOUNCEMENT_DEFAULT_TEXT

    def set_text_for(self, filename, text):
        if not filename:
            return
        text = str(text).strip() or config.ANNOUNCEMENT_DEFAULT_TEXT
        with self._lock:
            entry = dict(self._entries.get(filename) or {"swear": False})
            entry["text"] = text
            self._entries[filename] = entry
        self._save_cache()
        print(f"[ANNOUNCEMENT] {filename!r} caption set to: {text!r}")

    def is_swear(self, filename):
        with self._lock:
            entry = self._entries.get(filename)
            return bool(entry and entry.get("swear"))

    def toggle_swear(self, filename):
        """Flips the swear tag for filename and returns the new value --
        the caller (inputs/gamepad.py::toggle_last_announcement_swear())
        uses the return value to decide which LED confirmation to show."""
        with self._lock:
            entry = dict(self._entries.get(filename) or {"text": config.ANNOUNCEMENT_DEFAULT_TEXT})
            new_value = not entry.get("swear", False)
            entry["swear"] = new_value
            self._entries[filename] = entry
        self._save_cache()
        print(f"[ANNOUNCEMENT] {filename!r} swear tag set to: {new_value}")
        return new_value


announcement_engine = AnnouncementEngine()


def get_text_for(filename):
    return announcement_engine.get_text_for(filename)


def set_text_for(filename, text):
    announcement_engine.set_text_for(filename, text)


def is_swear(filename):
    return announcement_engine.is_swear(filename)


def toggle_swear(filename):
    return announcement_engine.toggle_swear(filename)


def reconcile_with_audio_dir():
    announcement_engine.reconcile_with_audio_dir()
