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


class BrandingEngine:
    """Branding text for the DJ-mode ticker. config.BRANDING_DEFAULT_TEXT
    ("Trivia Nite") is a first-launch-only bootstrap value -- the instant
    ANY value (that default, a remote fetch, or an operator edit) is saved
    to config.BRANDING_CACHE_PATH, that on-disk value becomes authoritative
    forever after and the remote BRANDING_URL fetch is never consulted
    again (2026-08-10 fix). Previously the periodic notify_deck_change()
    refresh would silently overwrite an operator's own edit the next time
    it happened to succeed -- intentional by the original design ("URL as
    fallback"), but that meant an edit could never actually stick, which
    is exactly what was reported ("goes back to Happyfamily every time")."""

    def __init__(self):
        self._lock = threading.Lock()
        self._has_local_value = os.path.exists(config.BRANDING_CACHE_PATH)
        self._text = self._load_cache()
        self._queue = queue.Queue()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        if not self._has_local_value:
            # No cache file has ever been saved -- this is a genuinely
            # fresh install, so it's safe (and the only time) to consult
            # the remote URL before falling back to the bootstrap default.
            self.request_refresh()

    def _load_cache(self):
        if self._has_local_value:
            try:
                with open(config.BRANDING_CACHE_PATH, "r", encoding="utf-8") as f:
                    text = f.read().strip()
                if text:
                    return text
            except Exception:
                pass
        # Fresh install (or an unreadable/empty cache file): bootstrap the
        # default and persist it immediately so it's authoritative from
        # this point on, same as an explicit operator edit would be.
        self._save_cache(config.BRANDING_DEFAULT_TEXT)
        self._has_local_value = True
        return config.BRANDING_DEFAULT_TEXT

    def _save_cache(self, text):
        try:
            with open(config.BRANDING_CACHE_PATH, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            pass

    def request_refresh(self):
        if requests is None or self._has_local_value:
            return
        try:
            self._queue.put_nowait(True)
        except Exception:
            pass

    def get_current_text(self):
        with self._lock:
            return self._text

    def set_text(self, text):
        """Real-Time Web Remote Branding Controller: applies a live edit
        from the web remote immediately (no restart needed) and persists it
        to the same on-disk cache, so it survives a restart AND is now
        permanently authoritative -- request_refresh() no-ops forever once
        self._has_local_value is True, so no future remote fetch can ever
        clobber this again."""
        text = str(text).strip() or config.BRANDING_DEFAULT_TEXT
        with self._lock:
            self._text = text
        self._has_local_value = True
        self._save_cache(text)
        print(f"[BRANDING] Live edit via web remote: {text!r}")

    def _worker_loop(self):
        while True:
            self._queue.get()
            try:
                resp = requests.get(config.BRANDING_URL, timeout=config.BRANDING_FETCH_TIMEOUT_SECONDS)
                resp.raise_for_status()
                text = resp.text.strip()
                if text and not self._has_local_value:
                    with self._lock:
                        self._text = text
                    self._has_local_value = True
                    self._save_cache(text)
                    print(f"[BRANDING] Fetched branding text: {text!r}")
            except Exception as e:
                # Silent to the caller -- last-known-good text stays in
                # effect. Logged so a persistent failure is still visible.
                print(f"[BRANDING] Fetch failed, keeping last-known text: {e}")


branding_engine = BrandingEngine()


def get_current_text():
    return branding_engine.get_current_text()


def set_current_text(text):
    branding_engine.set_text(text)


def notify_deck_change():
    """Call once per accepted deck-switch. Every Nth call (config-driven)
    triggers a branding refresh."""
    if state.deck_change_count % config.BRANDING_FETCH_EVERY_N_DECK_CHANGES == 0:
        branding_engine.request_refresh()
