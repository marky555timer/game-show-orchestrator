"""ASK QUESTIONS panic button (web remote, 2026-10-27).

One press = "get a question in front of the room now":
  1. Lifts question suppression (the remote's Suppress Questions box).
  2. Current song has saved questions -> asks one right away. Questions not
     yet asked this session go first; once they're all used, the song's
     earlier questions repeat. This also covers a song reached with PREVIOUS
     TRACK, whose artist was already asked about, so nothing asks by itself.
  3. Current song has none (a "dud") -> skips to a song that does have
     questions, so two duds never play back to back, and asks there once the
     new song is up (its "Who is this?" teaser counts, if that arms on its own).
"""
import random
import time

from state import state
from drivers import deck_orchestrator
from drivers import mystery_band_engine
from drivers.factoid_engine import track_engine, _sanitize_track_key
from drivers.music_library import music_library

_PENDING_TIMEOUT_SECONDS = 60.0   # give up waiting for the jumped-to song after this
_SETTLE_SECONDS = 2.0             # let the new song be heard a moment before the question

# Set when a press skipped to another song: ask there once it is playing.
_pending = None   # {"deck_change": int, "at": float}


def _current_track():
    from inputs import gamepad
    return gamepad._current_dj_track()


def _song_questions(key):
    return track_engine.peek(key) if key else []


def _queue_unasked_first(key):
    """Refill the song's queue when it's used up (repeat), then put questions
    not asked this session in front of ones already asked."""
    if not state.track_question_queue:
        state.track_question_queue = _song_questions(key)
    asked = state.asked_question_texts
    state.track_question_queue.sort(key=lambda q: q.get("question") in asked)


def _ask_now():
    from inputs import gamepad
    _queue_unasked_first(state.factoid_track_key)
    gamepad.handle_normal_trivia_button()


def _pick_song_with_questions():
    recent = {t["path"] for t in deck_orchestrator._history[-deck_orchestrator._TRACK_HISTORY_SIZE:]}
    current_key = state.factoid_track_key
    with_q = []
    for t in music_library.all_tracks():
        key = _sanitize_track_key(t.get("title", ""), t.get("artist", ""))
        if key == current_key or t["path"] in recent:
            continue
        qs = track_engine.peek(key) if track_engine.cached_question_count(t.get("title", ""), t.get("artist", "")) else []
        if qs:
            with_q.append((t, qs))
    if not with_q:
        return None
    # Honour the Setup page's crowd filters when that still leaves something.
    allowed = {t["path"] for t in deck_orchestrator._apply_show_filters([t for t, _ in with_q])}
    pool = [(t, qs) for t, qs in with_q if t["path"] in allowed] or with_q
    # Prefer songs that still have questions nobody has heard yet.
    fresh = [(t, qs) for t, qs in pool
             if any(q.get("question") not in state.asked_question_texts for q in qs)]
    return random.choice(fresh or pool)[0]


def ask():
    """Returns {"ok": bool, "message": str} for the remote."""
    global _pending
    was_suppressed = state.questions_suppressed
    state.questions_suppressed = False
    state.trivia_confirm_active = False   # a jukebox "TRIVIA QUESTION?" prompt is moot now
    print(f"[ASK QUESTIONS] Pressed (suppressed was {was_suppressed}).")

    if state.show_phase != "live":
        return {"ok": False, "message": "Start the show first."}
    if state.medley_active:
        return {"ok": False, "message": "Questions are back on; they start after the Dance Medley."}
    if state.intermission_active:
        return {"ok": False, "message": "Questions are back on; they start after the intermission."}
    if state.mode == state.MODE_GAME:
        return {"ok": True, "message": "A question is already up."}
    if _pending is not None or deck_orchestrator.has_pending_move():
        return {"ok": True, "message": "Changing songs; the question follows."}

    title, artist = _current_track()
    key = state.factoid_track_key
    confident = state.deck1_confident if state.active_deck == 1 else state.deck2_confident
    has_questions = bool(confident and key and (state.track_question_queue or _song_questions(key)))
    if has_questions or mystery_band_engine.is_teaser_live():
        _ask_now()
        return {"ok": True, "message": "Asking now."}

    song = _pick_song_with_questions()
    if song is None:
        return {"ok": False, "message": "No other song has questions saved yet."}
    print(f"[ASK QUESTIONS] '{title}' has no questions -- jumping to "
          f"'{song.get('title')}' - '{song.get('artist')}'.")
    deck_orchestrator.set_cued_track(song["path"])
    _pending = {"deck_change": state.deck_change_count, "at": time.time()}
    deck_orchestrator.trigger_track_move("next")
    from drivers import auto_dj_engine
    auto_dj_engine.notify_manual_track_move()
    return {"ok": True, "message": f"Jumping to {song.get('title')}; question follows."}


def update():
    """Per frame (inputs/gamepad.py::process_events): after a jump, ask on the
    new song once it is playing, identified and past its announcement."""
    global _pending
    if _pending is None:
        return
    now = time.time()
    if now - _pending["at"] > _PENDING_TIMEOUT_SECONDS:
        print("[ASK QUESTIONS] New song never settled -- giving up on the follow-up question.")
        _pending = None
        return
    if state.deck_change_count == _pending["deck_change"] or deck_orchestrator.has_pending_move():
        return
    _pending.setdefault("landed", now)   # new song is on the deck as of now
    if state.questions_suppressed or state.medley_active or state.show_phase != "live":
        _pending = None
        return
    if state.mode != state.MODE_DJ:
        _pending = None   # something already asked (teaser answered, Btn2...)
        return
    if mystery_band_engine.is_teaser_live() or mystery_band_engine._pending_mystery is not None:
        _pending = None   # the song's own "Who is this?" teaser is the question
        return
    confident = state.deck1_confident if state.active_deck == 1 else state.deck2_confident
    if not confident or not state.factoid_track_key:
        return
    if now < max(state.mystery_defer_until, _pending["landed"]) + _SETTLE_SECONDS:
        return
    _pending = None
    _ask_now()
