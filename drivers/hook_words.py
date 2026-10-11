"""Hook words: a song's signature phrase spelled across panels 3-6, one
word (or pair) per panel, each popping up as it's sung -- e.g. Whitney's
"WITH" / "SOMEBODY" / "WHO" / "LOVES ME!". A surprise for a small,
hand-picked set of songs, not lyrics for everything.

hook_words.json (repo root), keyed by the music file's name:
    "panels": four texts, in reading order (panel 3, 4, 5, 6)
    "times":  one list of four song-position seconds per time the phrase is
              sung -- when each panel's word starts
Times come from the PC (Demucs vocals -> Whisper word timing), not the Pi.

The song position is time.time() - state.now_playing_started_at, which
deck_orchestrator stamps when the deck starts and every seek re-aligns.
Off during medleys, questions, show phases and blackout (the caller only
draws it in DJ mode)."""
import json
import os
import threading
import time

import config
from state import state

_data = {}
_mtime = None


def _load():
    """Re-reads hook_words.json whenever it changes on disk."""
    global _data, _mtime
    try:
        m = os.path.getmtime(config.HOOK_WORDS_FILE)
    except OSError:
        _data, _mtime = {}, None
        return _data
    if m != _mtime:
        try:
            with open(config.HOOK_WORDS_FILE, encoding="utf-8") as f:
                _data = json.load(f)
        except Exception as e:
            print(f"[HOOK WORDS] Could not read {config.HOOK_WORDS_FILE}: {e}")
            _data = {}
        _mtime = m
    return _data


def active(now=None):
    """(panels, shown_at) while a phrase is on, else None. shown_at[k] is
    the wall time panel k's word appeared, or None if it hasn't yet."""
    if not config.HOOK_WORDS_ENABLED or not state.now_playing_path or not state.now_playing_started_at:
        return None
    entry = _load().get(os.path.basename(state.now_playing_path))
    if not entry:
        return None
    now = time.time() if now is None else now
    pos = now - state.now_playing_started_at + config.HOOK_WORDS_LEAD_SECONDS
    for times in entry.get("times", []):
        if times[0] - config.HOOK_WORDS_CLEAR_BEFORE_SECONDS <= pos <= times[-1] + config.HOOK_WORDS_HOLD_SECONDS:
            shown = [now - (pos - ts) if pos >= ts else None for ts in times]
            return entry["panels"], shown
    return None


def _seek_to_phrase(name, entry):
    from drivers import deck_orchestrator
    target = max(0.0, entry["times"][0][0] - config.HOOK_WORDS_DEMO_LEAD_IN_SECONDS)
    deck = "deck1" if state.active_deck == 1 else "deck2"
    if not deck_orchestrator.dj_engine.seek_deck(deck, target):
        return {"ok": False, "error": "could not seek the playing track"}
    now = time.time()
    state.now_playing_started_at = now - target
    state.auto_dj_track_started_at = now - target  # keep Auto-DJ's cue-out timer in step
    print(f"[HOOK WORDS] Demo: {name!r} jumped to {target:.1f}s.")
    return {"ok": True, "song": name, "seek_to_seconds": round(target, 1)}


def demo(name=None):
    """Web remote HOOK WORDS DEMO: jumps to a few seconds before the
    phrase. If the song isn't the one playing, cues it, moves to it, and
    jumps once its transition has finished."""
    from drivers import deck_orchestrator, auto_dj_engine
    data = _load()
    if not data:
        return {"ok": False, "error": "no hook words saved"}
    name = name or next(iter(data))
    entry = data.get(name)
    if not entry:
        return {"ok": False, "error": "no hook words for that song"}
    if deck_orchestrator.has_pending_move():
        return {"ok": False, "error": "a transition is already in progress"}
    if os.path.basename(state.now_playing_path or "") == name:
        return _seek_to_phrase(name, entry)
    path = os.path.join(config.MUSIC_DIR, name)
    if not os.path.isfile(path):
        return {"ok": False, "error": "song file not found"}
    deck_orchestrator.set_cued_track(path)
    deck_orchestrator.trigger_track_move("next")
    auto_dj_engine.notify_manual_track_move()

    def wait_then_seek():
        deadline = time.time() + 60
        while time.time() < deadline:
            time.sleep(0.2)
            if os.path.basename(state.now_playing_path or "") == name and not deck_orchestrator.has_pending_move():
                _seek_to_phrase(name, entry)
                return
        print("[HOOK WORDS] Demo: the song never came up -- gave up.")

    threading.Thread(target=wait_then_seek, daemon=True, name="hook-words-demo").start()
    return {"ok": True, "song": name, "switching": True}
