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
    """Large, hard-to-miss console echo of why an AI factoid/question
    request failed -- printed once per failure surfaced (not spammed
    every frame), so a bad API key, exhausted quota, or network outage
    is obvious in the terminal instead of silently falling back to the
    TEST placeholder question."""
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
    matter how it arrived (fresh AI call, disk cache hit, or local
    fallback_questions.json), before it ever reaches state/render."""
    print(f"=== INCOMING QUESTION DATA ({source}) ===")
    print(json.dumps({
        "question": data.get("question", ""),
        "choices": data.get("choices", []),
        "correct_index": data.get("correct_index", -1),
    }, indent=2))


def _apply_question_to_state(key, data, source):
    log_incoming_question(source, data)
    state.factoid_track_key = key
    state.factoid_headline = data.get("headline", "")
    state.factoid_full = data.get("full", "")
    state.factoid_question = data.get("question", "")
    state.factoid_choices = list(data.get("choices", []))
    state.factoid_correct_index = data.get("correct_index", -1)
    state.factoid_status = ""
    state.quiz_is_test = False
    state.quiz_selected_index = -1
    state.quiz_locked = False
    state.fixture1_mode = "off"  # Reset rule: new question -> Fixture 1 black


def load_forced_fallback_question(source_label="FORCED_FALLBACK"):
    """Bypasses the AI fetch and track-matching entirely: grabs a random
    question from fallback_questions.json (or the built-in mock question if
    that file is unavailable/empty) and applies it straight to state. Used by
    the Btn6 5s timeout tripwire and the Btn0 emergency override so the show
    is never left without a playable question."""
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
    key = f"{source_label}|{time.time()}"
    _apply_question_to_state(key, fallback, source_label)
    return fallback


def _make_key(title, artist):
    t = re.sub(r'\s+', ' ', str(title).strip().lower())
    a = re.sub(r'\s+', ' ', str(artist).strip().lower())
    return f"{t}|{a}"


def _looks_like_real_track(title, artist):
    t = str(title).strip()
    if len(t) < 3:
        return False
    low = t.lower()
    if any(low.startswith(p) for p in _PLACEHOLDER_PREFIXES):
        return False
    return True


class FactoidEngine:
    """Fetches a single AI 'did you know' factoid + quiz question/answers per
    confidently-identified track, caching to disk so replays and repeated
    frames never cost more than one API call per track (mirrors the AI
    cleanup worker pattern in drivers/rekordbox_driver.py)."""

    def __init__(self):
        self._cache = {}
        self._cache_lock = threading.Lock()
        self._queue = queue.Queue()
        self._inflight = set()
        self._worker_thread = None

        self._load_cache()

        if config.FACTOID_AI_ENABLED and requests is not None:
            self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
            self._worker_thread.start()

    # ------------------------------------------------------------
    # Disk cache
    # ------------------------------------------------------------
    def _load_cache(self):
        try:
            if os.path.exists(config.FACTOID_CACHE_PATH):
                with open(config.FACTOID_CACHE_PATH, "r", encoding="utf-8") as f:
                    self._cache = json.load(f)
        except Exception:
            self._cache = {}

    def _save_cache(self):
        try:
            with self._cache_lock:
                snapshot = dict(self._cache)
            with open(config.FACTOID_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, indent=2)
        except Exception:
            pass

    # ------------------------------------------------------------
    # Public entry point -- cheap, safe to call every frame.
    # ------------------------------------------------------------
    def request_factoid(self, title, artist, confident):
        key = None
        if confident and _looks_like_real_track(title, artist):
            key = _make_key(title, artist)

        if not key:
            if state.factoid_track_key:
                self._clear_state("NO CONFIDENT TRACK ID", "")
            return

        if key == state.factoid_track_key:
            return  # already showing (or pending) this track's factoid

        with self._cache_lock:
            cached = self._cache.get(key)

        if cached:
            if cached.get("status") == "failed":
                reason = cached.get("reason", "unknown failure")
                ttl = (config.FACTOID_UNKNOWN_RETRY_SECONDS if reason == "AI_NOT_CONFIDENT"
                       else config.FACTOID_FAILURE_RETRY_SECONDS)
                if time.time() - cached.get("ts", 0) < ttl:
                    self._clear_state(reason, key)
                    _print_failure_banner(title, artist, reason)
                    return
                # negative cache expired -- fall through and retry
            else:
                self._apply_result(key, cached)
                return

        if not config.FACTOID_AI_ENABLED:
            self._clear_state("AI DISABLED (NO API KEY)", key)
            _print_failure_banner(title, artist, "AI DISABLED (NO API KEY)")
            return
        if requests is None:
            self._clear_state("AI DISABLED (REQUESTS NOT INSTALLED)", key)
            _print_failure_banner(title, artist, "AI DISABLED (REQUESTS NOT INSTALLED)")
            return

        self._clear_state("FETCHING FACTOID...", key)
        if key not in self._inflight:
            self._inflight.add(key)
            self._queue.put((key, title, artist))

    # ------------------------------------------------------------
    # State sync helpers
    # ------------------------------------------------------------
    def _clear_state(self, reason, key):
        state.factoid_track_key = key
        state.factoid_headline = ""
        state.factoid_full = ""
        state.factoid_question = ""
        state.factoid_choices = []
        state.factoid_correct_index = -1
        state.factoid_status = reason
        state.quiz_is_test = False
        state.quiz_selected_index = -1
        state.quiz_locked = False
        state.fixture1_mode = "off"  # Reset rule: new question -> Fixture 1 black

    def _apply_result(self, key, data):
        _apply_question_to_state(key, data, "AI/CACHE")

    # ------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------
    def _worker_loop(self):
        while True:
            key, title, artist = self._queue.get()
            try:
                result, reason = self._call_ai(title, artist)

                if result:
                    with self._cache_lock:
                        self._cache[key] = result
                    self._save_cache()
                    if state.factoid_track_key == key:
                        self._apply_result(key, result)
                    print(f"[FACTOID] Got factoid for '{title}' - '{artist}'")
                else:
                    negative = {"status": "failed", "reason": reason, "ts": time.time()}
                    with self._cache_lock:
                        self._cache[key] = negative
                    self._save_cache()
                    if state.factoid_track_key == key:
                        state.factoid_status = reason
                    _print_failure_banner(title, artist, reason)
            except Exception as e:
                reason = f"ERROR: {e}"
                if state.factoid_track_key == key:
                    state.factoid_status = reason
                _print_failure_banner(title, artist, reason)
            finally:
                self._inflight.discard(key)

    # Question-style variety (Section 4.1): a style is picked client-side
    # per request rather than left entirely to the model's whim, so a run
    # of tracks actually alternates between year questions, numeric/career
    # facts, and one-word-answer headlines instead of drifting to whatever
    # the model defaults to.
    _QUESTION_STYLE_HINTS = [
        "Ask what year this track was released.",
        "Ask a numeric fact about the artist's career -- album count, chart "
        "position, band member count, etc -- phrased like a punchy short "
        "news headline (e.g. \"How many albums did AC/DC make?\").",
        "Ask a one-word-answer question about the artist -- a real name, "
        "who started the band, etc -- phrased like a punchy short news "
        "headline (e.g. \"What is Eminem's real last name?\", \"Who "
        "started this band?\").",
    ]

    def _call_ai(self, title, artist):
        """Returns (result_dict, None) on success, or (None, reason_str) on failure."""
        if not config.ANTHROPIC_API_KEY:
            return None, "AI DISABLED (NO API KEY)"

        style_hint = random.choice(self._QUESTION_STYLE_HINTS)
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
            f"question, max 100 characters. {style_hint} Since answers are "
            "shown on a small LED display, the correct answer must be "
            "naturally short), \"correct\" (the correct answer -- a "
            "number/year/single short word, max 24 characters), and "
            "\"wrong1\", \"wrong2\", \"wrong3\" (three plausible but "
            "incorrect answers in the same short style/format as the "
            "correct answer, max 24 characters each)."
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
            # Offline/network-down (Section 5.4): fall back to a local
            # question rather than surfacing a bare failure.
            fallback = _load_fallback_question()
            if fallback:
                return fallback, None
            return None, "NETWORK ERROR: no internet connection and no local fallback available"
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            # Always carry the API's own error message through. A bare
            # "AI HTTP ERROR (400)" is undebuggable -- the same status covers a
            # malformed request, an unsupported parameter for the chosen model,
            # and an exhausted credit balance, and only the body says which.
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
        wrong = [str(obj.get(k, "")).strip() for k in ("wrong1", "wrong2", "wrong3")]

        if not headline or not question or not correct or not all(wrong):
            return None, "INCOMPLETE AI RESPONSE"

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
    """Section 5.4 offline fallback: picks a random pre-written question from
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


factoid_engine = FactoidEngine()


def request_factoid(title, artist, confident):
    factoid_engine.request_factoid(title, artist, confident)


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
