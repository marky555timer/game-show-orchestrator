import csv
import os
import glob
import time
import threading

import config

_HEADER = ["filename", "text", "updated_at"]


class AnnouncementEngine:
    """Per-file announcement banner captions -- a table (filename -> text
    shown on the top 2 panels when that specific file plays), stored as CSV
    under config.ANNOUNCEMENT_TEXT_CACHE_PATH with one row per
    audio/announcements/*.wav|mp3 file (2026-08-10 redesign, replacing the
    original single-global-value design).

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
                            entries[filename] = row.get("text") or config.ANNOUNCEMENT_DEFAULT_TEXT
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
                for filename, text in sorted(self._entries.items()):
                    writer.writerow({"filename": filename, "text": text, "updated_at": now_str})
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
                    self._entries[filename] = config.ANNOUNCEMENT_DEFAULT_TEXT
                    added += 1
        if added:
            self._save_cache()
            print(f"[ANNOUNCEMENT] Added {added} placeholder caption(s) for previously-uncatalogued file(s).")

    def get_text_for(self, filename):
        with self._lock:
            return self._entries.get(filename, config.ANNOUNCEMENT_DEFAULT_TEXT)

    def set_text_for(self, filename, text):
        if not filename:
            return
        text = str(text).strip() or config.ANNOUNCEMENT_DEFAULT_TEXT
        with self._lock:
            self._entries[filename] = text
        self._save_cache()
        print(f"[ANNOUNCEMENT] {filename!r} caption set to: {text!r}")


announcement_engine = AnnouncementEngine()


def get_text_for(filename):
    return announcement_engine.get_text_for(filename)


def set_text_for(filename, text):
    announcement_engine.set_text_for(filename, text)


def reconcile_with_audio_dir():
    announcement_engine.reconcile_with_audio_dir()
