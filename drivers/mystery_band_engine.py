import random
import time

import config
from state import state
from drivers.music_library import music_library
from drivers.factoid_engine import apply_mystery_identify_question
from drivers import lighting_engine

# Generic distractor names, used only if the loaded rekordbox.xml library
# doesn't have 3 other distinct artists to draw decoys from.
_GENERIC_DECOY_POOL = ["Unknown Artist", "N/A", "Not Sure", "None Of These"]

# Set by check_new_track(), consumed by update() -- holds (artist_key,
# artist_display) for a confidently-identified new artist whose teaser is
# waiting on state.mystery_defer_until (the announcement VO finishing) --
# see _start_mystery()'s docstring for the history of this mechanism.
_pending_mystery = None


def _normalize_artist(artist):
    return str(artist).strip().lower()


def check_new_track(title, artist, confident):
    """Per-frame poll (inputs/gamepad.py::process_events(), right after
    ensure_prefetch()): flags the Mystery Band teaser to arm the first time
    a confidently-identified artist appears who hasn't had a question asked
    about them yet this session. state.asked_artists is populated by
    drivers/factoid_engine.py::_apply_active_question() every time a
    question actually gets served (queue pull, auto-advance, price game, or
    offline fallback).

    2026-08-10/11 history: a first attempt deferred this until the FULL
    transition settled (crossfade + swell to 100% volume) so the 30s
    answer clock wouldn't start counting against dead transition time --
    reverted because that delay overshot and made the client timer start
    noticeably late. Re-attempted here tied to a narrower, more precise
    point instead: state.mystery_defer_until, set by drivers/
    deck_orchestrator.py to exactly when the announcement VO clip itself
    is expected to finish (sweeper overlap + VO length) -- not the longer
    full crossfade/swell completion. Only set for ANNOUNCED transitions;
    a plain crossfade has nothing to wait for and arms immediately, same
    as always. update() consumes the deferred arm once the deadline hits.

    Suppressed entirely during the post-win intermission (2026-08-11) --
    music keeps playing normally, but no new teaser arms until
    state.intermission_active clears (drivers/win_sequence_engine.py)."""
    if state.intermission_active:
        return
    if not confident or not artist:
        return

    track_key = state.factoid_track_key
    if not track_key or track_key == state.mystery_track_key:
        return  # already evaluated this exact track

    state.mystery_track_key = track_key
    artist_key = _normalize_artist(artist)

    if artist_key in state.asked_artists:
        return  # not a mystery -- this artist has already been asked about

    # Lighting sting starts the moment the teaser is committed to -- flash
    # white, fade to black, then hold the room dark for however long the
    # deferral runs, so the "Who is this?" reveal lands out of darkness
    # rather than over an already-running pattern.
    lighting_engine.trigger_mystery_blackout()

    if time.time() < state.mystery_defer_until:
        # Announced transition still has its VO playing -- defer arming
        # until update() sees the deadline pass, instead of starting now.
        global _pending_mystery
        _pending_mystery = (artist_key, artist)
        return

    _start_mystery(artist_key, artist)


def _start_mystery(artist_key, artist_display):
    # Beta-Fix Feature Set item 10: since a teaser can now be answered (and
    # auto-graded) without state.mode ever reaching MODE_GAME, this is also
    # a valid "next game entry" point for the post-win score reset -- not
    # just the explicit Btn6/force_game_mode paths in inputs/gamepad.py.
    # Lazy import: inputs.gamepad imports this module at its own top level,
    # so importing it back here at module scope would be circular.
    from inputs import gamepad
    gamepad._maybe_reset_after_win()

    state.mystery_active = True
    state.mystery_resolved = False
    state.mystery_started_at = time.time()
    state.mystery_artist_key = artist_key
    state.mystery_artist_display = artist_display
    state.mystery_reveal_until = 0.0
    state.mystery_identify_question = _build_identify_question(artist_display)

    # Beta-Fix Feature Set item 2: client phones can answer the instant the
    # teaser starts, without waiting for the operator to force Game Mode.
    # apply_mystery_identify_question() -> _apply_active_question() only
    # populates the question/choices/round-clock fields -- it never touches
    # state.mode, so the physical panels stay on the "Who is this?" teaser
    # (graphics/matrix_canvas.py) until someone actually answers.
    state.round_first_answer_at = 0.0
    apply_mystery_identify_question(state.mystery_identify_question)

    # "Who is this?" is now up -- end the blackout hold and fade the running
    # pattern back in underneath it.
    lighting_engine.release_mystery_blackout()

    print(f"[MYSTERY BAND] New artist this session: {artist_display!r} -- teaser armed "
          f"for {config.MYSTERY_REVEAL_TIMEOUT_SECONDS}s, answerable from clients immediately.")


def build_identify_fallback_question(artist_display):
    """Public wrapper around _build_identify_question(), for
    drivers/factoid_engine.py::load_exhausted_fallback_question() (one-shot
    AI query policy: offline fallback for a track whose AI query is
    EXHAUSTED) to reuse the same local-only "who is this band" question
    builder the Mystery Band teaser uses, without reaching into a private
    function. Returns None if artist_display is empty."""
    if not artist_display:
        return None
    return _build_identify_question(artist_display)


def _build_identify_question(correct_artist):
    """Builds the "identify this band" question entirely locally from the
    loaded rekordbox.xml library -- no AI call, so it's instantly ready the
    moment Game Mode is entered anywhere inside the 10s teaser window."""
    correct_lower = correct_artist.strip().lower()
    pool = {a.strip() for a in music_library.all_artists()
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
    alongside price_game_engine.update(). First fires any teaser still
    waiting on state.mystery_defer_until (the announcement VO finishing --
    see check_new_track()'s docstring), then advances the 10s teaser
    window into the reveal-blink, then back to standard DJ mode display."""
    global _pending_mystery
    if _pending_mystery is not None and now >= state.mystery_defer_until:
        artist_key, artist_display = _pending_mystery
        _pending_mystery = None
        _start_mystery(artist_key, artist_display)

    if not state.mystery_active:
        return

    if state.mode == state.MODE_GAME:
        return  # entered via enter_game_from_mystery(); rendering already switched over

    elapsed = now - state.mystery_started_at

    # Reveal as soon as EITHER everyone's answered OR the timeout hits,
    # whichever comes first (2026-08-12 fix): drivers/live_round_engine.py
    # already auto-grades this round (state.quiz_locked -> True) the
    # instant every connected phone has locked in, completely independent
    # of this reveal timer -- previously the panels kept hiding the artist
    # behind "Who is this?" for the full MYSTERY_REVEAL_TIMEOUT_SECONDS
    # regardless, even after the round was already graded and the scores
    # were in, which read as the board just being slow/stuck.
    if not state.mystery_resolved and state.quiz_locked:
        state.mystery_resolved = True
        state.mystery_reveal_until = now + config.MYSTERY_REVEAL_BLINK_SECONDS
        print(f"[MYSTERY BAND] Everyone answered -- showing {state.mystery_artist_display!r}.")
    elif not state.mystery_resolved and elapsed >= config.MYSTERY_REVEAL_TIMEOUT_SECONDS:
        state.mystery_resolved = True
        state.mystery_reveal_until = now + config.MYSTERY_REVEAL_BLINK_SECONDS
        print(f"[MYSTERY BAND] Reveal timeout -- showing {state.mystery_artist_display!r}.")

    if state.mystery_resolved and now >= state.mystery_reveal_until:
        state.mystery_active = False


def is_teaser_live():
    """True only during the still-unresolved 10s window -- the window in
    which Btn2 should force the identify-band question in first."""
    return state.mystery_active and not state.mystery_resolved


def enter_game_from_mystery():
    """Btn2 hook (inputs/gamepad.py::handle_normal_trivia_button()): called
    instead of the normal queue-pull path whenever is_teaser_live() is True.
    (Btn6 is a dedicated Price Game trigger and doesn't check this at all --
    see inputs/gamepad.py::handle_quiz_gate_button().) Forces the prebuilt
    "identify this band" question in as the active round question, re-sorts
    the remaining cached queue for this track by the Mystery Band
    question-priority hierarchy, and ends the teaser."""
    question = state.mystery_identify_question
    if question is None:
        return False

    if not state.round_first_answer_at:
        # Nobody's answered the teaser-answering window yet -- safe (and
        # correct, it also resets the 30s round timer) to (re)apply. If a
        # client already answered, skip re-applying so we don't wipe out
        # their already-locked-in selection out from under them.
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
