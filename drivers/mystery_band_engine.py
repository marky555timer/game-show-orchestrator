import random
import time

import config
from state import state
from drivers.rekordbox_driver import rb_driver
from drivers.factoid_engine import apply_mystery_identify_question

# Generic distractor names, used only if the loaded rekordbox.xml library
# doesn't have 3 other distinct artists to draw decoys from.
_GENERIC_DECOY_POOL = ["Unknown Artist", "N/A", "Not Sure", "None Of These"]


def _normalize_artist(artist):
    return str(artist).strip().lower()


def check_new_track(title, artist, confident):
    """Per-frame poll (inputs/gamepad.py::process_events(), right after
    ensure_prefetch()): arms the Mystery Band teaser the first time a
    confidently-identified artist appears who hasn't had a question asked
    about them yet this session. state.asked_artists is populated by
    drivers/factoid_engine.py::_apply_active_question() every time a
    question actually gets served (queue pull, auto-advance, price game, or
    offline fallback)."""
    if not confident or not artist:
        return

    track_key = state.factoid_track_key
    if not track_key or track_key == state.mystery_track_key:
        return  # already evaluated this exact track

    state.mystery_track_key = track_key
    artist_key = _normalize_artist(artist)

    if artist_key in state.asked_artists:
        return  # not a mystery -- this artist has already been asked about

    _start_mystery(artist_key, artist)


def _start_mystery(artist_key, artist_display):
    state.mystery_active = True
    state.mystery_resolved = False
    state.mystery_started_at = time.time()
    state.mystery_artist_key = artist_key
    state.mystery_artist_display = artist_display
    state.mystery_reveal_until = 0.0
    state.mystery_identify_question = _build_identify_question(artist_display)
    print(f"[MYSTERY BAND] New artist this session: {artist_display!r} -- teaser armed "
          f"for {config.MYSTERY_REVEAL_TIMEOUT_SECONDS}s.")


def _build_identify_question(correct_artist):
    """Builds the "identify this band" question entirely locally from the
    loaded rekordbox.xml library -- no AI call, so it's instantly ready the
    moment Game Mode is entered anywhere inside the 10s teaser window."""
    correct_lower = correct_artist.strip().lower()
    pool = {a.strip() for (_, _, a) in rb_driver.db.tracks_db
            if a and a.strip() and a.strip().lower() != correct_lower}

    decoys = random.sample(list(pool), min(3, len(pool)))

    fallback_pool = list(_GENERIC_DECOY_POOL)
    seen_lower = {correct_lower} | {d.lower() for d in decoys}
    while len(decoys) < 3 and fallback_pool:
        candidate = fallback_pool.pop(0)
        if candidate.lower() not in seen_lower:
            decoys.append(candidate)
            seen_lower.add(candidate.lower())

    choices = [correct_artist] + decoys
    random.shuffle(choices)

    return {
        "headline": "mystery band",
        "full": "",
        "question": "Who is this?",
        "choices": [c[:24] for c in choices],
        "correct_index": choices.index(correct_artist),
        "release_year": None,
        "category": "identify_band",
        "ts": time.time(),
    }


def update(now):
    """Per-frame poll -- called from inputs/gamepad.py::process_events()
    alongside price_game_engine.update(). Advances the 10s teaser window
    into the reveal-blink, then back to standard DJ mode display."""
    if not state.mystery_active:
        return

    if state.mode == state.MODE_GAME:
        return  # entered via enter_game_from_mystery(); rendering already switched over

    elapsed = now - state.mystery_started_at

    if not state.mystery_resolved and elapsed >= config.MYSTERY_REVEAL_TIMEOUT_SECONDS:
        state.mystery_resolved = True
        state.mystery_reveal_until = now + config.MYSTERY_REVEAL_BLINK_SECONDS
        print(f"[MYSTERY BAND] Reveal timeout -- showing {state.mystery_artist_display!r}.")

    if state.mystery_resolved and now >= state.mystery_reveal_until:
        state.mystery_active = False


def is_teaser_live():
    """True only during the still-unresolved 10s window -- the window in
    which Btn6 should force the identify-band question in first."""
    return state.mystery_active and not state.mystery_resolved


def enter_game_from_mystery():
    """Btn6 hook (inputs/gamepad.py::handle_quiz_gate_button()): called
    instead of the normal queue-pull/price-game path whenever
    is_teaser_live() is True. Forces the prebuilt "identify this band"
    question in as the active round question, re-sorts the remaining
    cached queue for this track by the Mystery Band question-priority
    hierarchy, and ends the teaser."""
    question = state.mystery_identify_question
    if question is None:
        return False

    apply_mystery_identify_question(question)
    _sort_queue_by_priority()

    state.mystery_active = False
    state.mystery_resolved = False
    state.mystery_identify_question = None
    print("[MYSTERY BAND] Identify-band question served -- queue re-sorted by priority hierarchy.")
    return True


def _sort_queue_by_priority():
    """Section 2 hierarchy: Geography > Date/Release Year > True/False >
    Real Name/Stage Name. Categories not in the map (career_stat,
    song_meaning) sort last, in their original relative order (stable sort)."""
    priority = config.MYSTERY_CATEGORY_PRIORITY
    state.track_question_queue.sort(key=lambda q: priority.get(q.get("category", ""), 99))
