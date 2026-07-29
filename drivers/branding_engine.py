import os
import time
import queue
import threading

try:
    import requests
except ImportError:
    requests = None

import config
from state import state

_DEFAULT_TEXT = ""


class BrandingEngine:
    """Fetches/caches the DJ's branding text from a remote .txt file
    (Section 6). The remote page/form that WRITES branding.txt is out of
    scope for this app -- this engine only ever reads it, on a background
    thread so a slow/broken network can never stall the render loop."""

    def __init__(self):
        self._lock = threading.Lock()
        self._text = self._load_cache()
        self._queue = queue.Queue()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        # Once per launch, unconditionally.
        self.request_refresh()

    def _load_cache(self):
        try:
            if os.path.exists(config.BRANDING_CACHE_PATH):
                with open(config.BRANDING_CACHE_PATH, "r", encoding="utf-8") as f:
                    return f.read().strip()
        except Exception:
            pass
        return _DEFAULT_TEXT

    def _save_cache(self, text):
        try:
            with open(config.BRANDING_CACHE_PATH, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            pass

    def request_refresh(self):
        if requests is None:
            return
        try:
            self._queue.put_nowait(True)
        except Exception:
            pass

    def get_current_text(self):
        with self._lock:
            return self._text

    def _worker_loop(self):
        while True:
            self._queue.get()
            try:
                resp = requests.get(config.BRANDING_URL, timeout=config.BRANDING_FETCH_TIMEOUT_SECONDS)
                resp.raise_for_status()
                text = resp.text.strip()
                if text:
                    with self._lock:
                        self._text = text
                    self._save_cache(text)
                    print(f"[BRANDING] Fetched branding text: {text!r}")
            except Exception as e:
                # Silent to the caller -- last-known-good text stays in
                # effect. Logged so a persistent failure is still visible.
                print(f"[BRANDING] Fetch failed, keeping last-known text: {e}")


branding_engine = BrandingEngine()


def get_current_text():
    return branding_engine.get_current_text()


def notify_deck_change():
    """Call once per accepted deck-switch. Every Nth call (config-driven)
    triggers a branding refresh."""
    if state.deck_change_count % config.BRANDING_FETCH_EVERY_N_DECK_CHANGES == 0:
        branding_engine.request_refresh()
