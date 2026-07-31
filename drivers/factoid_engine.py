import os
import re
import json
import time
import queue
import random
import threading

try:
    import requests
except ImportError:
    requests = None

import config
from state import state

_PLACEHOLDER_PREFIXES = ("ready for deck", "no track loaded")

# Reasons that just mean "nothing to report yet" (no track cued up, or a
# request already in flight) -- NOT a failure, so no banner for these.
_BENIGN_STATUS_REASONS = ("NO CONFIDENT TRACK ID", "FETCHING FACTOID...")


def _print_failure_banner(title, artist, reason):
    """Large, hard-to-miss console echo of why an AI question request
    failed -- printed once per failure surfaced (not spammed every frame),
    so a bad API key, exhausted quota, or network outage is obvious in the
    terminal instead of silently falling back to the local fallback
    question set."""
    if reason in _BENIGN_STATUS_REASONS:
        return
    banner = "!" * 62
    print(f"\n{banner}")
    print("!! AI FACTOID/QUESTION REQUEST FAILED")
    print(f"!! TRACK  : {title!r} - {artist!r}")
    print(f"!! REASON : {reason}")
    print(f"{banner}\n")


def _http_error_detail(resp, limit=200):
    """Pull the Anthropic API's human-readable error message out of a failed
    response body. The API returns {"error": {"type": ..., "message": ...}},
    so the message is the one field worth surfacing; falls back to raw text
    when the body isn't the JSON envelope we expect."""
    if resp is None:
        return "no response body"
    try:
        payload = resp.json()
        err = payload.get("error") or {}
        msg = err.get("message") or ""
        etype = err.get("type") or ""
        combined = f"{etype}: {msg}".strip(": ") if etype else msg
        if combined:
            return combined[:limit]
    except Exception:
        pass
    text = (resp.text or "").strip()
    return text[:limit] if text else "empty response body"


def log_incoming_question(source, data):
    """Mandatory evidence-tracking log: prints the full question payload no
    matter how it arrived (fresh Haiku call, disk cache hit, or local
    fallback_questions.json), before it ever reaches state/render."""
    print("=== CACHED / FETCHED QUESTION DATA ===")
    print(json.dumps({
        "source": source,
        "headline": data.get("headline", ""),
        "full": data.get("full", ""),
        "question": data.get("question", ""),
        "choices": data.get("choices", []),
        "correct_index": data.get("correct_index", -1),
        "correction": data.get("correction", ""),
    }, indent=2))


def _sanitize_track_key(title, artist):
    """Section 1 cache key format: "artist - title" (lowercased,
    whitespace-collapsed). Falls back to just the title if no artist is
    known yet."""
    t = re.sub(r'\s+', ' ', str(title).strip().lower())
    a = re.sub(r'\s+', ' ', str(artist).strip().lower())
    return f"{a} - {t}" if a else t


# Small distinct fallback strings swapped in when the AI repeats a
# name/word across distractor slots (Section 2.3 guardrail) despite the
# prompt instruction not to. Kept short/generic since they must fit the
# same small LED-panel choice boxes as real answers.
_FALLBACK_DISTRACTOR_POOL = ["N/A", "Unknown", "Not Sure", "None Of These"]


def _dedupe_distractors(correct, wrong):
    """Case-insensitively deduplicates `wrong` against `correct` and
    against itself so a played round never shows the same name/choice
    string in two option slots. Duplicates are swapped for a distinct
    fallback string where one is available; if the fallback pool is
    exhausted the slot is dropped entirely (shrinking the returned list)
    rather than risk a duplicate -- matrix_canvas.py already renders any
    option index beyond len(choices) as a blank "----" panel, and
    select_quiz_answer() already refuses to select it."""
    seen = {correct.strip().lower()}
    fallback_pool = list(_FALLBACK_DISTRACTOR_POOL)
    deduped = []
    for w in wrong:
        key = w.strip().lower()
        if w and key not in seen:
            deduped.append(w)
            seen.add(key)
            continue
        replacement = None
        while fallback_pool:
            candidate = fallback_pool.pop(0)
            if candidate.lower() not in seen:
                replacement = candidate
                break
        if replacement:
            deduped.append(replacement)
            seen.add(replacement.lower())
        # else: drop the slot cleanly rather than keep a duplicate.
    return deduped


def _decade_label(year):
    """1970-1979 -> "70s", 1980-1989 -> "80s", else None -- the trigger for
    the 70s/80s Price Game bonus round (config.PRICE_GAME_MIN_YEAR/MAX_YEAR)."""
    if year is None:
        return None
    if config.PRICE_GAME_MIN_YEAR <= year <= 1979:
        return "70s"
    if 1980 <= year <= config.PRICE_GAME_MAX_YEAR:
        return "80s"
    return None


_CURRENT_YEAR = time.localtime().tm_year


def _parse_release_year(raw):
    """Validates the AI-reported "release_year" field: must be a plain
    4-digit numeral in a sane historical range, else the field is treated
    as unknown (None) rather than trusted blindly."""
    s = str(raw).strip()
    if not re.fullmatch(r'\d{4}', s):
        return None
    year = int(s)
    return year if 1900 <= year <= _CURRENT_YEAR else None


def _looks_like_real_track(title, artist):
    t = str(title).strip()
    if len(t) < 3:
        return False
    low = t.lower()
    if any(low.startswith(p) for p in _PLACEHOLDER_PREFIXES):
        return False
    return True


def _apply_active_question(data, source):
    """Loads `data` as the CURRENTLY ACTIVE on-screen round question (Btn6
    pop, multi-question auto-advance, or an offline fallback) -- distinct
    from the background prefetch status preview (factoid_headline/full/
    status), which only peeks at the queue."""
    log_incoming_question(source, data)
    state.factoid_question = data.get("question", "")
    state.factoid_choices = list(data.get("choices", []))
    state.factoid_correct_index = data.get("correct_index", -1)
    state.factoid_correction = data.get("correction", "")
    state.quiz_is_test = False
    state.quiz_selected_index = -1
    state.quiz_locked = False
    state.quiz_graded_at = 0.0
    state.fixture1_mode = "off"  # Reset rule: new question -> Fixture 1 black

    # Section 2 (Mystery Band): a question is genuinely "asked" the instant
    # it becomes the active round question, regardless of source (queue
    # pull, auto-advance, price game, or offline fallback) -- track_key is
    # "artist - title" (see _sanitize_track_key), so peel the artist back off.
    track_key = state.factoid_track_key
    if " - " in track_key:
        artist_key = track_key.split(" - ", 1)[0].strip()
        if artist_key:
            state.asked_artists.add(artist_key)


def load_forced_fallback_question(source_label="FORCED_FALLBACK"):
    """Bypasses the AI fetch and pre-fetch queue entirely: grabs a random
    question from fallback_questions.json (or the built-in mock question if
    that file is unavailable/empty) and applies it straight to state as the
    active round question. Used by the Btn6 empty-cache timeout tripwire and
    the Btn0 emergency override so the show is never left without a playable
    question."""
    fallback = _load_fallback_question()
    if not fallback:
        mock = build_mock_question()
        fallback = {
            "headline": "offline trivia",
            "full": "",
            "question": mock["question"],
            "choices": mock["choices"],
            "correct_index": mock["correct_index"],
            "ts": time.time(),
        }
    _apply_active_question(fallback, source_label)
    return fallback


def pull_next_from_queue(key):
    """Btn6: instantly pop the next pre-fetched question for `key` off the
    runtime queue and make it the active round question. Returns True if a
    question was available, False if the queue for that track is empty."""
    if key != state.factoid_track_key or not state.track_question_queue:
        return False
    nxt = state.track_question_queue.pop(0)
    _apply_active_question(nxt, "QUEUE/BTN6")
    return True


def advance_to_next_queued_question():
    """Multi-question game loop: pops the next pre-fetched question off the
    active track's runtime queue and makes it the active round question,
    staying in GAME_MODE. Returns False (caller should return to DJ_MODE) if
    no more questions are queued for this track."""
    if not state.track_question_queue:
        return False
    nxt = state.track_question_queue.pop(0)
    _apply_active_question(nxt, "QUEUE/AUTO-ADVANCE")
    return True


class TrackQuestionEngine:
    """Background-prefetches up to TRACK_QUESTIONS_PER_TRACK distinct quiz
    questions (Haiku-generated) per confidently-identified track, caching
    them to disk (track_cache.json) so a replayed track never costs another
    API call once its cache is full. Runs continuously as soon as a valid
    track/artist is identified -- no button press required."""

    def __init__(self):
        self._cache = {}  # key -> list[question dict], up to TRACK_QUESTIONS_PER_TRACK
        self._cache_lock = threading.Lock()
        self._queue = queue.Queue()
        self._inflight = set()
        self._price_offered = set()  # track keys already armed/offered a Price Game this session

        self._load_cache()

        if config.FACTOID_AI_ENABLED and requests is not None:
            threading.Thread(target=self._worker_loop, daemon=True).start()

    # ------------------------------------------------------------
    # Disk cache
    # ------------------------------------------------------------
    def _load_cache(self):
        try:
            if os.path.exists(config.TRACK_CACHE_PATH):
                with open(config.TRACK_CACHE_PATH, "r", encoding="utf-8") as f:
                    self._cache = json.load(f)
        except Exception:
            self._cache = {}

    def _save_cache(self):
        try:
            with self._cache_lock:
                snapshot = dict(self._cache)
            with open(config.TRACK_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, indent=2)
        except Exception:
            pass

    # ------------------------------------------------------------
    # Public entry point -- cheap, safe to call every frame regardless of
    # mode (DJ or GAME); it's how track identification triggers pre-fetch
    # without waiting for Btn6.
    # ------------------------------------------------------------
    def ensure_prefetch(self, title, artist, confident):
        if not confident or not _looks_like_real_track(title, artist):
            if not confident and state.factoid_track_key:
                state.factoid_track_key = ""
                state.factoid_headline = ""
                state.factoid_full = ""
                state.factoid_status = "NO CONFIDENT TRACK ID"
                state.track_question_queue = []
                state.price_game_pending = False
                state.price_game_decade = ""
            return

        key = _sanitize_track_key(title, artist)

        with self._cache_lock:
            cached = list(self._cache.get(key, []))

        if key != state.factoid_track_key:
            state.factoid_track_key = key
            state.track_question_queue = list(cached)
            # New track -- any Price Game armed for the previous track is no
            # longer relevant. Re-armed just below if this track's own
            # cached questions already carry an eligible release_year.
            state.price_game_pending = False
            state.price_game_decade = ""
            if cached:
                print(f"[TRACK CACHE] {len(cached)} cached question(s) already on disk for "
                      f"'{title}' - '{artist}'")
                for q in cached:
                    log_incoming_question("CACHE HIT", q)
                    year = q.get("release_year")
                    if year:
                        self._maybe_arm_price_game(key, year)
                self._set_status(key, cached[0])
            else:
                self._set_status(key, None, "FETCHING FACTOID...")

        if len(cached) >= config.TRACK_QUESTIONS_PER_TRACK:
            return  # Cache limit reached (Section 1) -- no API requests.

        if not config.FACTOID_AI_ENABLED:
            self._set_status(key, None, "AI DISABLED (NO API KEY)")
            return
        if requests is None:
            self._set_status(key, None, "AI DISABLED (REQUESTS NOT INSTALLED)")
            return

        if key in self._inflight:
            return  # already filling the next slot for this track
        self._inflight.add(key)
        self._queue.put((key, title, artist))

    # ------------------------------------------------------------
    # State sync helpers -- these drive the DJ-mode panel3 AI-pipeline
    # status indicator (star/cat/coin) and the optional top-page factoid
    # preview, mirroring queue[0] without consuming it.
    # ------------------------------------------------------------
    def _set_status(self, key, first_question, reason=""):
        state.factoid_track_key = key
        if first_question:
            state.factoid_headline = first_question.get("headline", "")
            state.factoid_full = first_question.get("full", "")
            state.factoid_status = ""
        else:
            state.factoid_headline = ""
            state.factoid_full = ""
            state.factoid_status = reason

    # ------------------------------------------------------------
    # 70s/80s Price Game trigger: arms once per track key (never re-armed
    # for the same key this session) the first time an AI-tagged
    # release_year for that track lands in the 1970-1989 range. Btn6
    # (inputs/gamepad.py::handle_quiz_gate_button) checks state.price_game_
    # pending and hands off to drivers/price_game_engine.py instead of
    # pulling a normal question.
    # ------------------------------------------------------------
    def _maybe_arm_price_game(self, key, year):
        decade = _decade_label(year)
        if not decade or key in self._price_offered:
            return
        self._price_offered.add(key)
        state.price_game_pending = True
        state.price_game_decade = decade
        print(f"[PRICE GAME] Armed {decade.upper()} price game for '{key}' (release_year={year}).")

    # Question-style variety (Section 4.1 legacy numbering): each of the 3
    # buffered slots uses a different style hint so a track's 3 questions
    # actually vary instead of drifting toward whatever the model defaults
    # to. Each hint carries a "type" -- "multiple_choice" (4 options) or
    # "true_false" (Options 1/2 = True/False, Options 3/4 disabled -- see
    # _call_ai and inputs/gamepad.py's select_quiz_answer bounds check) --
    # so _call_ai and the response parser know which shape to build/expect.
    # 6 categories total (one is True/False) -- 3 of them get rolled per
    # track (see _style_hints_for_track), so geography/real-name/etc show up
    # periodically rather than every single track.
    # "category" tags each style so the Mystery Band question-priority
    # hierarchy (config.MYSTERY_CATEGORY_PRIORITY, Section 2) can re-sort a
    # track's cached queue once Game Mode is entered from a live Mystery
    # Band window. Categories not present in that priority map (career_stat,
    # song_meaning) simply sort last, in original order.
    _QUESTION_STYLE_HINTS = [
        {"type": "multiple_choice", "category": "date", "hint":
            "Ask what year this track was released. Guardrail: work out how "
            "long ago that real year was relative to today. If the track is "
            "MORE than 20 years old, the three wrong-answer years MUST span "
            "noticeably different decades/eras from the correct year and "
            "from each other (e.g. correct=1974 -> wrong answers could be "
            "1959, 1965, 1988) rather than clustering close together. If the "
            "track is LESS than 20 years old, a tighter spread is fine (e.g. "
            "correct=2014 -> wrong answers 2011, 2017, 2019)."},
        {"type": "multiple_choice", "category": "career_stat", "hint":
            "Ask a numeric fact about the artist's career -- album count, chart "
            "position, band member count, etc -- phrased like a punchy short "
            "news headline (e.g. \"How many albums did AC/DC make?\")."},
        {"type": "multiple_choice", "category": "real_name", "hint":
            "Ask a one-word-answer question about the artist -- a real name, "
            "who started the band, etc -- phrased like a punchy short news "
            "headline (e.g. \"What is Eminem's real last name?\", \"Who "
            "started this band?\"). Guardrail: ONLY ask a \"what is their "
            "real name\" question if this artist actually performs under a "
            "stage name/pseudonym distinct from their legal name (e.g. "
            "Eminem, Lady Gaga, Sting, Elton John). If the artist already "
            "performs under their own real/legal name (e.g. Adele, Ed "
            "Sheeran, Bruno Mars, Dolly Parton), do NOT ask a real-name "
            "question -- ask who started the band, a band member's name, or "
            "a similar one-word artist fact instead."},
        {"type": "multiple_choice", "category": "song_meaning", "hint":
            "Ask what THIS SONG is about / its meaning or subject matter, in "
            "simple terms. The correct answer must be a short, simple phrase "
            "summarizing the song's theme (e.g. \"heartbreak\", \"a breakup\", "
            "\"partying all night\", \"losing a friend\")."},
        {"type": "multiple_choice", "category": "geography", "hint":
            "Ask a music-geography question about the artist or song -- e.g. "
            "what city or country the artist/band is from, what city they "
            "were formed in, where they first performed live, or where a "
            "famous recording/performance took place -- phrased like a "
            "punchy short news headline (e.g. \"What city did the Beatles "
            "first play in?\", \"Where was this band formed?\")."},
        {"type": "true_false", "category": "true_false", "hint":
            "Ask a True/False question about fan gossip, rumors, or "
            "pop-culture trivia tied to this artist or song (e.g. \"T/F: "
            "Morrissey loves meat.\"). Keep it fun and juicy, but only state "
            "claims whose truth value you actually know -- never invent a "
            "specific rumor you're not confident is true or false. Roughly "
            "half the time, deliberately make the statement a plausible-"
            "sounding claim that is actually FALSE (a believable rumor, a "
            "common misconception, or a specific-sounding date/fact that "
            "didn't actually happen) rather than defaulting to a true "
            "statement -- the goal is a genuine 50/50 mix of True and False "
            "answers over many questions, not a bias toward True."},
    ]

    def _style_hints_for_track(self, key):
        """Deterministic per-track shuffle (seeded on the track's cache key)
        of _QUESTION_STYLE_HINTS, so the 3 buffered slots for one track are
        always distinct styles, but which 3 of the 6 categories show up
        (including the newer song-meaning / geography / true-false ones)
        varies track to track instead of the same first 3 winning every
        time."""
        order = list(range(len(self._QUESTION_STYLE_HINTS)))
        random.Random(key).shuffle(order)
        return [self._QUESTION_STYLE_HINTS[i] for i in order]

    # ------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------
    def _worker_loop(self):
        while True:
            key, title, artist = self._queue.get()
            try:
                self._fill_one_slot(key, title, artist)
            finally:
                self._inflight.discard(key)

    def _fill_one_slot(self, key, title, artist):
        """Fetches exactly one question and appends it to the disk cache +
        the live runtime queue (if this track is still the active one).
        Retries a couple of times in place if the model happens to repeat a
        question already in the cache; ensure_prefetch()'s per-frame polling
        picks up filling any remaining slots on the next call."""
        with self._cache_lock:
            existing = list(self._cache.get(key, []))
        if len(existing) >= config.TRACK_QUESTIONS_PER_TRACK:
            return
        style_hint = self._style_hints_for_track(key)[len(existing) % len(self._QUESTION_STYLE_HINTS)]

        for _attempt in range(3):
            result, reason = self._call_ai(title, artist, style_hint)
            if not result:
                print(f"[TRACK PREFETCH] Question {len(existing) + 1}/{config.TRACK_QUESTIONS_PER_TRACK} "
                      f"for '{title}' - '{artist}' failed: {reason}")
                if state.factoid_track_key == key and not existing:
                    self._set_status(key, None, reason)
                _print_failure_banner(title, artist, reason)
                return

            with self._cache_lock:
                existing = list(self._cache.get(key, []))
                if any(q.get("question") == result.get("question") for q in existing):
                    continue  # duplicate -- retry within the attempt budget
                existing = existing + [result]
                self._cache[key] = existing

            self._save_cache()
            log_incoming_question("FRESH FETCH", result)
            print(f"[TRACK PREFETCH] Cached question {len(existing)}/{config.TRACK_QUESTIONS_PER_TRACK} "
                  f"for '{title}' - '{artist}'")

            if state.factoid_track_key == key:
                state.track_question_queue.append(result)
                if len(existing) == 1:
                    self._set_status(key, result)
                if result.get("release_year"):
                    self._maybe_arm_price_game(key, result["release_year"])
            return

    def _call_ai(self, title, artist, style_hint):
        """Returns (result_dict, None) on success, or (None, reason_str) on failure."""
        if not config.ANTHROPIC_API_KEY:
            return None, "AI DISABLED (NO API KEY)"

        is_true_false = style_hint["type"] == "true_false"
        hint = style_hint["hint"]
        prompt = (_build_true_false_prompt(title, artist, hint) if is_true_false
                  else _build_mc_prompt(title, artist, hint))

        text, reason = _post_haiku(prompt)
        if text is None:
            if reason and reason.startswith("NETWORK ERROR"):
                # Offline/network-down: fall back to a local question rather
                # than surfacing a bare failure.
                fallback = _load_fallback_question()
                if fallback:
                    return fallback, None
                return None, "NETWORK ERROR: no internet connection and no local fallback available"
            return None, reason

        if text.strip().upper().startswith("UNKNOWN"):
            return None, "AI_NOT_CONFIDENT"

        return _parse_question_json(text, is_true_false, category=style_hint.get("category", ""))


def _post_haiku(prompt, max_tokens=500, timeout=None):
    """Shared Anthropic Haiku call + response-text extraction, reused by
    every AI prompt in this module (track trivia and the Price Game pricing
    question below). Returns (text, None) on success, or (None, reason_str)
    on failure -- reason strings match the taxonomy this module has always
    surfaced (timeout, network, HTTP status, malformed body)."""
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": config.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                # Runtime question generation is cost-gated to Haiku only
                # (Section 1) -- AI_CLEANUP_MODEL is pinned to Haiku, never
                # Sonnet/Opus, in config.py.
                "model": config.AI_CLEANUP_MODEL,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=timeout or config.FACTOID_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        return None, "REQUEST TIMED OUT"
    except requests.exceptions.ConnectionError as e:
        return None, f"NETWORK ERROR: no internet connection ({e})"
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        detail = _http_error_detail(e.response)
        if status == 429:
            return None, f"AI QUOTA/RATE LIMIT EXHAUSTED (HTTP 429): {detail}"
        if status in (401, 403):
            return None, f"AI AUTH ERROR (HTTP {status}) -- CHECK API KEY: {detail}"
        return None, f"AI HTTP ERROR ({status}): {detail}"
    except requests.exceptions.RequestException as e:
        return None, f"NETWORK ERROR: {e}"

    try:
        data = resp.json()
        text = "".join(
            block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
        ).strip()
    except Exception:
        return None, "MALFORMED AI RESPONSE"

    if not text:
        return None, "EMPTY AI RESPONSE"

    return text, None


def _build_true_false_prompt(title, artist, hint):
    # True/False shape: choices are fixed to ["True", "False"] by
    # _parse_question_json (Options 1/2) -- Options 3/4 are never
    # populated, which matrix_canvas.py already renders blank and
    # inputs/gamepad.py::select_quiz_answer already refuses to select
    # (index >= len(choices)), so no extra button-disable logic is needed.
    #
    # Guardrail against a "True" bias: real trivia sets skew heavily toward
    # writing statements that happen to be true, since false statements take
    # more deliberate effort to construct. The prompt below explicitly asks
    # for a genuine mix, and `hint` (see _QUESTION_STYLE_HINTS) reinforces it.
    return (
        "You are a music trivia assistant for a live DJ show's LED "
        f"display. Song title: {title!r}. Artist: {artist!r}. "
        "If you are NOT at least 90% confident this is a real, "
        "specific song/artist you actually know true facts about, "
        "reply with EXACTLY the single word UNKNOWN and nothing "
        "else. Otherwise reply with ONLY a single-line JSON object "
        "(no markdown fences, no commentary) with these keys: "
        "\"headline\" (a punchy factoid teaser, max 40 characters, "
        "for a scrolling LED sign), \"full\" (a fuller interesting "
        "factoid, max 200 characters), \"release_year\" (the 4-digit "
        "real-world calendar year this exact song/track was originally "
        "released, as a plain numeral string e.g. \"1984\" -- empty "
        "string \"\" only if you genuinely don't know), \"question\" "
        f"(a True/False statement, max 100 characters, starting with "
        f"\"T/F: \". {hint} IMPORTANT: do not default to writing a true "
        "statement -- across many questions the correct answer should be "
        "False about as often as it's True, so actively consider writing a "
        "plausible-sounding but FALSE claim (a believable rumor, a common "
        "misconception, or a specific-sounding but incorrect date/fact) "
        "rather than always reaching for something you know to be true), "
        "\"correct\" -- the single word \"True\" or \"False\" (exactly, "
        "capitalized, nothing else, and must truthfully match the "
        "statement in \"question\"), and \"correction\" -- ONLY when "
        "\"correct\" is \"False\": a short statement of the actual true "
        "fact (max 100 characters, e.g. \"Morrissey is actually a strict "
        "vegan\"), so the reveal can show the room what's really true "
        "instead of just the word FALSE. If \"correct\" is \"True\", set "
        "\"correction\" to an empty string."
    )


def _build_mc_prompt(title, artist, hint):
    return (
        "You are a music trivia assistant for a live DJ show's LED display. "
        f"Song title: {title!r}. Artist: {artist!r}. "
        "If you are NOT at least 90% confident this is a real, specific "
        "song you actually know true facts about, reply with EXACTLY the "
        "single word UNKNOWN and nothing else. Otherwise reply with ONLY "
        "a single-line JSON object (no markdown fences, no commentary) "
        "with these keys: \"headline\" (a punchy factoid teaser, max 40 "
        "characters, for a scrolling LED sign), \"full\" (a fuller "
        "interesting factoid, max 200 characters), \"release_year\" (the "
        "4-digit real-world calendar year this exact song/track was "
        "originally released, as a plain numeral string e.g. \"1984\" -- "
        "empty string \"\" only if you genuinely don't know), \"question\" "
        f"(a trivia question, max 100 characters. {hint} Since answers are "
        "shown on a small LED display, the correct answer must be "
        "naturally short), \"correct\" (the correct answer -- a "
        "number/year/short word or phrase, max 24 characters), and "
        "\"wrong1\", \"wrong2\", \"wrong3\" (three plausible but "
        "incorrect answers in the same short style/format as the "
        "correct answer, max 24 characters each). \"correct\", "
        "\"wrong1\", \"wrong2\", and \"wrong3\" must all be distinct "
        "strings -- never reuse the same name/word/number across "
        "more than one of those four fields."
    )


def _parse_question_json(text, is_true_false, category=""):
    """Shared response parser for both the true_false and multiple_choice
    track-trivia shapes. Returns (result_dict, None) on success or
    (None, reason_str) on failure. `category` tags the result for the
    Mystery Band question-priority hierarchy (Section 2, config.py)."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', cleaned).strip()

    # Defensive: with thinking disabled, stray tags can occasionally leak
    # into the visible text. Slice out the JSON object itself rather than
    # trusting the response to be pure JSON.
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        cleaned = cleaned[start:end + 1]

    try:
        obj = json.loads(cleaned)
    except Exception:
        return None, "JSON PARSE ERROR"

    headline = str(obj.get("headline", "")).strip()
    full = str(obj.get("full", "")).strip()
    question = str(obj.get("question", "")).strip()
    correct = str(obj.get("correct", "")).strip()
    release_year = _parse_release_year(obj.get("release_year", ""))

    if not headline or not question or not correct:
        return None, "INCOMPLETE AI RESPONSE"

    correction = ""
    if is_true_false:
        correct_lower = correct.lower()
        if correct_lower not in ("true", "false"):
            return None, "MALFORMED TRUE/FALSE ANSWER"
        # Options 1/2 = True/False; Options 3/4 intentionally absent.
        choices = ["True", "False"]
        correct_index = 0 if correct_lower == "true" else 1
        if correct_index == 1:
            correction = str(obj.get("correction", "")).strip()
    else:
        wrong = [str(obj.get(k, "")).strip() for k in ("wrong1", "wrong2", "wrong3")]
        if not all(wrong):
            return None, "INCOMPLETE AI RESPONSE"
        # Guardrail: the model can still repeat a name/word across
        # choices despite the prompt instruction -- dedupe defensively
        # rather than trust it.
        wrong = _dedupe_distractors(correct, wrong)
        choices = [correct] + wrong
        random.shuffle(choices)
        correct_index = choices.index(correct)

    result = {
        "headline": headline[:40],
        "full": full[:200],
        "question": question[:100],
        "choices": [c[:24] for c in choices],
        "correct_index": correct_index,
        "correction": correction[:100],
        "release_year": release_year,
        "category": category,
        "ts": time.time(),
    }
    # Price Game product-cooldown tracking (Section 3) -- only present when
    # the prompt asked for it (see _build_price_prompt); harmless no-op key
    # for every other question shape.
    product = str(obj.get("product", "")).strip()
    if product:
        result["product"] = product[:40]
    return result, None


# ------------------------------------------------------------
# 70s/80s "Price Game" pricing trivia (see drivers/price_game_engine.py for
# the strobe/banner/question-wait state machine that calls this).
# ------------------------------------------------------------
def _build_price_prompt(decade_label, era_years, category, avoid_products=None):
    avoid_clause = ""
    if avoid_products:
        avoid_list = ", ".join(avoid_products)
        avoid_clause = (
            f" Do NOT pick any of these products -- they were asked about "
            f"recently and are on cooldown: {avoid_list}."
        )
    return (
        "You are running a 'Price Is Right'-style pricing trivia bonus "
        f"round for a live DJ show's LED display, themed to the "
        f"{decade_label} ({era_years}). Pick ONE real, era-appropriate "
        f"everyday consumer product from the '{category['label']}' category "
        f"(examples: {category['examples']}), and ask what it cost in that "
        f"decade.{avoid_clause} Reply with ONLY a "
        "single-line JSON object (no markdown fences, no commentary) with "
        "these keys: \"product\" (a short canonical name for the specific "
        "product chosen, e.g. \"Gallon of Milk\", max 40 characters), "
        "\"headline\" (a punchy teaser naming the product, "
        "max 40 characters, for a scrolling LED sign), \"full\" (a fuller "
        "sentence naming the specific product and decade, max 200 "
        "characters), \"question\" (e.g. \"How much was a box of Kellogg's "
        f"Corn Flakes in 1984?\" -- must name a specific product and a "
        f"specific year within {era_years}, max 100 characters), "
        "\"correct\" (the real historical price formatted like \"$1.49\", "
        "max 24 characters), and \"wrong1\", \"wrong2\", \"wrong3\" "
        "(three other realistic era-appropriate prices in the same "
        "\"$X.XX\" format -- plausible but not equal to the correct price "
        "or to each other, max 24 characters each)."
    )


def fetch_price_question(decade_label, category_index=0, avoid_products=None):
    """Fetches a single 70s/80s Price Game pricing-trivia question from
    Haiku, in the same result shape _call_ai produces for a normal
    multiple_choice question (so it can be applied via apply_price_question
    -> _apply_active_question like any other quiz question). `category_index`
    rotates round-robin through config.PRICE_GAME_CATEGORIES (driven by
    drivers/price_game_engine.py's session-wide occurrence counter) so
    consecutive rounds don't keep landing on the same product type.
    `avoid_products` (Section 3: product repetition cooldown) is a list of
    product names still on cooldown -- drivers/price_game_engine.py builds
    this from state.price_game_product_history and the prompt instructs the
    model not to pick any of them. Returns (result_dict, None) on success or
    (None, reason_str) on failure."""
    if not config.ANTHROPIC_API_KEY:
        return None, "AI DISABLED (NO API KEY)"

    era_years = "1970-1979" if decade_label == "70s" else "1980-1989"
    category = config.PRICE_GAME_CATEGORIES[category_index % len(config.PRICE_GAME_CATEGORIES)]
    prompt = _build_price_prompt(decade_label, era_years, category, avoid_products)

    text, reason = _post_haiku(prompt, max_tokens=400)
    if text is None:
        return None, reason

    result, reason = _parse_question_json(text, is_true_false=False)
    if result is None:
        return None, reason
    result["release_year"] = None  # metadata field is about "this song", not relevant here
    return result, None


def apply_price_question(data):
    """Public hook for drivers/price_game_engine.py -- loads a fetched
    price-question dict as the active round question exactly like a normal
    Btn6 pull."""
    _apply_active_question(data, "PRICE_GAME")


def apply_mystery_identify_question(data):
    """Public hook for drivers/mystery_band_engine.py -- loads the forced
    "identify this band" question as the active round question exactly like
    a normal Btn6 pull (this also marks the artist as asked, same as any
    other question source, via _apply_active_question)."""
    _apply_active_question(data, "MYSTERY_IDENTIFY")


def _load_fallback_question():
    """Offline fallback: picks a random pre-written question from
    fallback_questions.json so quiz mode still gets a playable question when
    an Anthropic API call fails due to a network outage. Returns a result
    dict shaped like a normal successful _call_ai() response, or None if the
    file is missing/unreadable."""
    try:
        with open(config.FALLBACK_QUESTIONS_PATH, "r", encoding="utf-8") as f:
            entries = json.load(f)
        if not entries:
            return None
        entry = random.choice(entries)
        return {
            "headline": "offline trivia",
            "full": "",
            "question": entry["question"],
            "choices": [str(c)[:24] for c in entry["choices"]],
            "correct_index": int(entry["correct_index"]),
            "ts": time.time(),
        }
    except Exception as e:
        print(f"[FACTOID] Could not load fallback_questions.json: {e}")
        return None


track_engine = TrackQuestionEngine()


def ensure_prefetch(title, artist, confident):
    track_engine.ensure_prefetch(title, artist, confident)


# ------------------------------------------------------------
# Placeholder quiz content, used when no real AI-sourced question is
# available yet (no confident track ID, AI disabled, network down, etc.)
# so the select -> grade -> DMX/sound flow can still be tested end to end.
# ------------------------------------------------------------
_MOCK_QUESTION_TEXT = "test mode: pick any answer to try the flow"
_MOCK_CHOICES = ["option a", "option b", "option c", "option d"]


def build_mock_question():
    choices = list(_MOCK_CHOICES)
    correct_index = random.randrange(len(choices))
    return {
        "question": _MOCK_QUESTION_TEXT,
        "choices": choices,
        "correct_index": correct_index,
    }
