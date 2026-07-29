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
    state.quiz_is_test = False
    state.quiz_selected_index = -1
    state.quiz_locked = False
    state.quiz_graded_at = 0.0
    state.fixture1_mode = "off"  # Reset rule: new question -> Fixture 1 black


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
            return

        key = _sanitize_track_key(title, artist)

        with self._cache_lock:
            cached = list(self._cache.get(key, []))

        if key != state.factoid_track_key:
            state.factoid_track_key = key
            state.track_question_queue = list(cached)
            if cached:
                print(f"[TRACK CACHE] {len(cached)} cached question(s) already on disk for "
                      f"'{title}' - '{artist}'")
                for q in cached:
                    log_incoming_question("CACHE HIT", q)
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

    # Question-style variety (Section 4.1 legacy numbering): each of the 3
    # buffered slots uses a different style hint so a track's 3 questions
    # actually vary instead of drifting toward whatever the model defaults
    # to. Each hint carries a "type" -- "multiple_choice" (4 options) or
    # "true_false" (Options 1/2 = True/False, Options 3/4 disabled -- see
    # _call_ai and inputs/gamepad.py's select_quiz_answer bounds check) --
    # so _call_ai and the response parser know which shape to build/expect.
    _QUESTION_STYLE_HINTS = [
        {"type": "multiple_choice", "hint": "Ask what year this track was released."},
        {"type": "multiple_choice", "hint":
            "Ask a numeric fact about the artist's career -- album count, chart "
            "position, band member count, etc -- phrased like a punchy short "
            "news headline (e.g. \"How many albums did AC/DC make?\")."},
        {"type": "multiple_choice", "hint":
            "Ask a one-word-answer question about the artist -- a real name, "
            "who started the band, etc -- phrased like a punchy short news "
            "headline (e.g. \"What is Eminem's real last name?\", \"Who "
            "started this band?\")."},
        {"type": "multiple_choice", "hint":
            "Ask what THIS SONG is about / its meaning or subject matter, in "
            "simple terms. The correct answer must be a short, simple phrase "
            "summarizing the song's theme (e.g. \"heartbreak\", \"a breakup\", "
            "\"partying all night\", \"losing a friend\")."},
        {"type": "true_false", "hint":
            "Ask a True/False question about fan gossip, rumors, or "
            "pop-culture trivia tied to this artist or song (e.g. \"T/F: "
            "Morrissey loves meat.\"). Keep it fun and juicy, but only state "
            "claims whose truth value you actually know -- never invent a "
            "specific rumor you're not confident is true or false."},
    ]

    def _style_hints_for_track(self, key):
        """Deterministic per-track shuffle (seeded on the track's cache key)
        of _QUESTION_STYLE_HINTS, so the 3 buffered slots for one track are
        always distinct styles, but which 3 of the 5 categories show up
        (including the newer song-meaning / true-false ones) varies track
        to track instead of the same first 3 winning every time."""
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
            return

    def _call_ai(self, title, artist, style_hint):
        """Returns (result_dict, None) on success, or (None, reason_str) on failure."""
        if not config.ANTHROPIC_API_KEY:
            return None, "AI DISABLED (NO API KEY)"

        is_true_false = style_hint["type"] == "true_false"
        hint = style_hint["hint"]

        if is_true_false:
            # True/False shape: choices are fixed to ["True", "False"]
            # below (Options 1/2) -- Options 3/4 are never populated, which
            # matrix_canvas.py already renders blank and
            # inputs/gamepad.py::select_quiz_answer already refuses to
            # select (index >= len(choices)), so no extra button-disable
            # logic is needed for Options 3/4.
            prompt = (
                "You are a music trivia assistant for a live DJ show's LED "
                f"display. Song title: {title!r}. Artist: {artist!r}. "
                "If you are NOT at least 90% confident this is a real, "
                "specific song/artist you actually know true facts about, "
                "reply with EXACTLY the single word UNKNOWN and nothing "
                "else. Otherwise reply with ONLY a single-line JSON object "
                "(no markdown fences, no commentary) with these keys: "
                "\"headline\" (a punchy factoid teaser, max 40 characters, "
                "for a scrolling LED sign), \"full\" (a fuller interesting "
                "factoid, max 200 characters), \"question\" (a True/False "
                f"statement, max 100 characters, starting with \"T/F: \". "
                f"{hint}), and \"correct\" -- the single word \"True\" or "
                "\"False\" (exactly, capitalized, nothing else)."
            )
        else:
            prompt = (
                "You are a music trivia assistant for a live DJ show's LED display. "
                f"Song title: {title!r}. Artist: {artist!r}. "
                "If you are NOT at least 90% confident this is a real, specific "
                "song you actually know true facts about, reply with EXACTLY the "
                "single word UNKNOWN and nothing else. Otherwise reply with ONLY "
                "a single-line JSON object (no markdown fences, no commentary) "
                "with these keys: \"headline\" (a punchy factoid teaser, max 40 "
                "characters, for a scrolling LED sign), \"full\" (a fuller "
                "interesting factoid, max 200 characters), \"question\" (a trivia "
                f"question, max 100 characters. {hint} Since answers are "
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
                    "max_tokens": 500,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=config.FACTOID_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
        except requests.exceptions.Timeout:
            return None, "REQUEST TIMED OUT"
        except requests.exceptions.ConnectionError:
            # Offline/network-down: fall back to a local question rather
            # than surfacing a bare failure.
            fallback = _load_fallback_question()
            if fallback:
                return fallback, None
            return None, "NETWORK ERROR: no internet connection and no local fallback available"
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

        if text.strip().upper().startswith("UNKNOWN"):
            return None, "AI_NOT_CONFIDENT"

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

        if not headline or not question or not correct:
            return None, "INCOMPLETE AI RESPONSE"

        if is_true_false:
            correct_lower = correct.lower()
            if correct_lower not in ("true", "false"):
                return None, "MALFORMED TRUE/FALSE ANSWER"
            # Options 1/2 = True/False; Options 3/4 intentionally absent.
            choices = ["True", "False"]
            correct_index = 0 if correct_lower == "true" else 1
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
            "ts": time.time(),
        }
        return result, None


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
