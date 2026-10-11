"""Question audit (web page /questions).

The per-song trivia questions live in track_cache.json (drivers/
factoid_engine.py, `track_engine`). This module adds, per question:

  * an AUDIT STAGE -- how far the question has got toward being trustworthy.
    A contested or wrong "fact" in front of a paying room is the one failure
    this business can't afford, so nothing is ever treated as checked until a
    stage says so:

        generated          red     AI generated, unverified
        edited             red     changed/added by hand, unverified
        audited_corrected  amber   AI audit found an error and fixed it;
                                   needs deep verification (Opus / research)
        audited_flagged    amber   AI audit could not confirm it; needs a human
        audited_ok         lime    AI audited blind, same answer, no error
        verified           green   a human verified it

    A song with fewer than TRACK_QUESTIONS_PER_TRACK questions is GREY (it
    needs questions made first); otherwise a song shows its most urgent
    question's colour.

  * a SHORT version (`question`/`choices`, the LED sign -- unchanged field
    names so nothing downstream moves) and a LONG version (`question_long`/
    `choices_long`, shown on players' phones).

The AI audit answers each question BLIND -- without being told which choice
is marked correct -- on a bigger model than wrote it, then compares. That is
what catches a flipped True/False, which a "please verify this" prompt tends
to wave through.

Everything here is operator-started from the page; nothing runs on its own.
"""
import concurrent.futures
import json
import os
import random
import re
import shutil
import threading
import time
import uuid
from collections import Counter

import config
from drivers import factoid_engine, token_tracker
from drivers.music_library import music_library, sanitize_track_key

# stage -> (colour, label, what-to-do)
STAGES = {
    "generated": ("red", "AI generated, unverified", "No check yet - possible error"),
    "edited": ("red", "Changed by hand, unverified", "Re-run the AI audit or verify it yourself"),
    "audited_flagged": ("amber", "AI audit could not confirm this", "Needs a human: research it, then fix or verify"),
    "audited_corrected": ("amber", "AI audit found an error and corrected it", "Needs deep verification (Opus) or your own research"),
    "audited_ok": ("lime", "AI audited - self-verified, no error found", "Optional: check it yourself"),
    "verified": ("green", "Verified by a human", "Done"),
}
# Order songs/questions are shown in: most in need of attention first.
COLOR_ORDER = ["grey", "amber", "red", "lime", "green"]
_QUESTION_ORDER = {"amber": 0, "red": 1, "lime": 2, "green": 3}

_CONTENT_FIELDS = ("question", "question_long", "choices", "choices_long",
                   "correct_index", "correction", "headline", "full")


class AIError(Exception):
    pass


# ------------------------------------------------------------------
# Small helpers
# ------------------------------------------------------------------
def _engine():
    return factoid_engine.track_engine


def _model_label(model):
    m = re.match(r"claude-([a-z]+)-(\d+)-(\d+)", model or "")
    return f"{m.group(1).title()} {m.group(2)}.{m.group(3)}" if m else (model or "AI")


def _norm(s):
    return re.sub(r"\s+", " ", str(s or "").strip())


def is_tf(q):
    return [str(c).strip().lower() for c in q.get("choices", [])] == ["true", "false"]


def audit_of(q):
    """The question's audit record; legacy questions (cached before this
    existed) count as AI generated, unverified."""
    a = q.get("audit")
    if not isinstance(a, dict) or a.get("stage") not in STAGES:
        return {"stage": "generated", "by": "Haiku 4.5", "ts": q.get("ts"), "note": "",
                "deep": False, "original": None, "history": []}
    return a


def color_of(q):
    return STAGES[audit_of(q)["stage"]][0]


def song_color(questions):
    if len(questions) < config.TRACK_QUESTIONS_PER_TRACK:
        return "grey"
    return min((color_of(q) for q in questions), key=lambda c: _QUESTION_ORDER[c])


def stamp_new(q, source="ai"):
    """Gives a freshly generated question its id and starting audit stage."""
    q.setdefault("qid", uuid.uuid4().hex[:12])
    q["source"] = source
    now = time.time()
    q["audit"] = {"stage": "generated", "by": "Haiku 4.5" if source != "manual" else "You",
                  "ts": now, "note": "Offline fallback question - not written for this song" if source == "fallback" else "",
                  "deep": False, "original": None,
                  "history": [{"ts": now, "who": "Haiku 4.5", "action": "generated", "note": ""}]}
    return q


def _history(q, who, action, note=""):
    a = q["audit"]
    a.setdefault("history", []).append({"ts": time.time(), "who": who, "action": action, "note": note})
    del a["history"][:-30]


def _content(q):
    return {f: q.get(f, "" if f not in ("choices", "choices_long") else []) for f in _CONTENT_FIELDS}


def _sig(q):
    return json.dumps(_content(q), sort_keys=True)


def serialize_question(q):
    a = audit_of(q)
    color, label, action = STAGES[a["stage"]]
    return {
        "qid": q.get("qid"), "type": "tf" if is_tf(q) else "mc",
        "question": q.get("question", ""), "question_long": q.get("question_long", ""),
        "choices": q.get("choices", []), "choices_long": q.get("choices_long", []),
        "correct_index": q.get("correct_index", -1), "correction": q.get("correction", ""),
        "headline": q.get("headline", ""), "full": q.get("full", ""),
        "category": q.get("category") or "", "release_year": q.get("release_year"),
        "source": q.get("source") or "ai",
        "audit": {"stage": a["stage"], "color": color, "label": label, "action": action,
                  "by": a.get("by", ""), "ts": a.get("ts"), "note": a.get("note", ""),
                  "deep": bool(a.get("deep")), "original": a.get("original"),
                  "history": a.get("history", [])},
    }


# ------------------------------------------------------------------
# Master-database safety: daily backup, deleted-question log, small meta file
# ------------------------------------------------------------------
_backup_lock = threading.Lock()
_backup_day = None


def _data_dir():
    return os.path.dirname(config.TRACK_CACHE_PATH)


def _backup_once():
    """One dated copy of track_cache.json per day before the first change
    (keeps the last 7). The audit edits a master database -- keep a way back."""
    global _backup_day
    day = time.strftime("%Y%m%d")
    with _backup_lock:
        if _backup_day == day:
            return
        try:
            if os.path.exists(config.TRACK_CACHE_PATH):
                dest = os.path.join(_data_dir(), f"track_cache.backup-{day}.json")
                if not os.path.exists(dest):
                    shutil.copy2(config.TRACK_CACHE_PATH, dest)
                olds = sorted(f for f in os.listdir(_data_dir())
                              if f.startswith("track_cache.backup-") and f.endswith(".json"))
                for f in olds[:-7]:
                    os.remove(os.path.join(_data_dir(), f))
            _backup_day = day
        except Exception as e:
            print(f"[QUESTION AUDIT] backup failed: {e}")


def _log_deleted(key, q):
    try:
        with open(os.path.join(_data_dir(), "track_cache_deleted.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"deleted_at": time.time(), "key": key, "question": q}) + "\n")
    except Exception:
        pass


_META_PATH = os.path.join(os.path.dirname(config.TRACK_CACHE_PATH), "question_audit_meta.json")
_meta_lock = threading.Lock()


def _load_meta():
    try:
        with open(_META_PATH, "r", encoding="utf-8") as f:
            m = json.load(f)
        if isinstance(m, dict):
            m.setdefault("unknown", {})
            return m
    except Exception:
        pass
    return {"unknown": {}}


def _mark_unknown(key, on=True):
    with _meta_lock:
        m = _load_meta()
        if on:
            m["unknown"][key] = time.time()
        else:
            m["unknown"].pop(key, None)
        tmp = _META_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(m, f)
        os.replace(tmp, _META_PATH)


# ------------------------------------------------------------------
# AI plumbing
# ------------------------------------------------------------------
def _json_obj(text):
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', cleaned).strip()
    s, e = cleaned.find("{"), cleaned.rfind("}")
    if s != -1 and e > s:
        cleaned = cleaned[s:e + 1]
    try:
        obj = json.loads(cleaned)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _ask_json(prompt, model=None, max_tokens=1200, timeout=None):
    """model None = Haiku (writing the long/short wording); otherwise the
    audit model. Returns a dict or raises AIError."""
    timeout = timeout or (config.QUESTION_AUDIT_TIMEOUT_SECONDS if model else 25.0)
    text = None
    for _attempt in range(2):     # one retry: an occasional reply arrives as prose / cut off
        if model:
            text, reason = factoid_engine.call_model(prompt, model, max_tokens=max_tokens, timeout=timeout)
        else:
            text, reason = factoid_engine.call_haiku(prompt, max_tokens=max_tokens, timeout=timeout)
        if text is None:
            raise AIError(reason or "AI call failed")
        obj = _json_obj(text)
        if obj is not None:
            return obj
    print(f"[QUESTION AUDIT] unreadable AI reply: {text[:400]!r}")
    raise AIError(f"unreadable AI reply: {_norm(text)[:120]}")


_lib_cache = {"at": 0.0, "idx": {}}


def _library_index():
    """{cache key: (title, artist)} for the library, refreshed every 30 s."""
    now = time.time()
    if now - _lib_cache["at"] > 30:
        idx = {}
        for t in music_library.all_tracks():
            idx.setdefault(sanitize_track_key(t["title"], t["artist"]), (t["title"], t["artist"]))
        _lib_cache.update(at=now, idx=idx)
    return _lib_cache["idx"]


def song_ref(key):
    t = _library_index().get(key)
    if t:
        return f"Song title: {t[0]!r}. Artist: {t[1]!r}."
    return f"Song (written as 'artist - title'): {key!r}."


# ------------------------------------------------------------------
# Long (phone) and short (LED) wording
# ------------------------------------------------------------------
def generate_long(song, question, choices):
    """Short LED wording -> phone wording. Returns {question_long, choices_long}."""
    tf = [c.strip().lower() for c in choices] == ["true", "false"]
    prompt = (
        "You write trivia for a live music-trivia night. "
        f"{song}\n"
        "Below is a trivia question in SHORT form, written for a tiny LED sign. "
        "Rewrite it in LONG form for players' phones.\n"
        "Rules: keep exactly the same meaning, the same facts and the same correct answer. "
        "Add NO new information of any kind: no descriptions of the artist or song (no nationality, "
        "genre, band members, dates, places, chart positions) unless that exact detail is already "
        "in the short version. Add no hints; do not make it easier or harder. Only expand "
        "abbreviations and make the wording read naturally in one or two sentences. "
        f"The question must be at most {config.QUESTION_LONG_MAX_CHARS} characters"
        + ("; since it is a true/false statement, start it with \"True or False: \"" if tf else "")
        + f". Rewrite each answer choice in a clearer, fuller form where that helps (a full name, "
        f"a unit), at most {config.QUESTION_LONG_CHOICE_MAX_CHARS} characters each; keep the same "
        "order and the same number of choices; if a choice is already clear, keep it as is.\n"
        f"SHORT question: {question}\nSHORT choices: {json.dumps(choices)}\n"
        "Reply with ONLY a single-line JSON object: {\"question_long\": \"...\", \"choices_long\": [\"...\"]}"
    )
    obj = _ask_json(prompt)
    q_long = _norm(obj.get("question_long"))[:config.QUESTION_LONG_MAX_CHARS]
    if tf:
        c_long = ["True", "False"]
    else:
        c_long = [_norm(c)[:config.QUESTION_LONG_CHOICE_MAX_CHARS] for c in (obj.get("choices_long") or [])]
    if not q_long or len(c_long) != len(choices) or not all(c_long):
        raise AIError("AI returned an unusable long version")
    return {"question_long": q_long, "choices_long": c_long}


def generate_short(song, question_long, choices_long, tf):
    """Phone wording -> LED wording: a terse headline, not a sentence.
    Returns {question, choices}, or raises AIError if it can't be made to
    fit the LED limits."""
    qmax, cmax = config.QUESTION_SHORT_MAX_CHARS, config.QUESTION_SHORT_CHOICE_MAX_CHARS
    target = config.QUESTION_SHORT_TARGET_CHARS
    feedback = ""
    for _ in range(3):
        prompt = (
            "You write trivia for a live music-trivia night. "
            f"{song}\n"
            "Below is a trivia question in LONG form for players' phones. Write the SHORT form for a "
            "tiny LED sign the whole room reads at a glance.\n"
            "The SHORT form is a terse headline, NOT a sentence: drop articles, verbs like 'was/is/did', "
            "'what', 'which', 'this song', the song title and the artist name (the room already knows "
            "the song). Use symbols and abbreviations: #1, yr, UK, US, vs, &, 'No.'. Examples:\n"
            "  'What was the top-selling toy in 1999?' -> '#1 toy in 1999?'\n"
            "  'In what year was this song released?' -> 'Year released?'\n"
            "  'Which country did the band come from?' -> 'Band's home country?'\n"
            "  'Who produced this song for the artist?' -> 'Producer?'\n"
            "  'True or False: This song reached number one in the UK.' -> 'T/F: Hit UK #1'\n"
            f"Aim for about {target} characters; never more than {qmax}. Keep exactly the same meaning "
            "and correct answer; add nothing"
            + ("; it is a true/false statement, so start it with \"T/F: \" and use choices [\"True\", \"False\"]"
               if tf else
               f"; each answer choice at most {cmax} characters (keep a choice as is if it already fits); "
               "keep the same order and number of choices")
            + f". {feedback}\n"
            f"LONG question: {question_long}\nLONG choices: {json.dumps(choices_long)}\n"
            "Reply with ONLY a single-line JSON object: {\"question\": \"...\", \"choices\": [\"...\"]}"
        )
        obj = _ask_json(prompt)
        q = _norm(obj.get("question"))
        if tf:
            if not q.lower().startswith("t/f"):
                q = "T/F: " + q
            choices = ["True", "False"]
        else:
            choices = [_norm(c) for c in (obj.get("choices") or [])]
            # A choice that already fits the LED stays word-for-word.
            if len(choices) == len(choices_long):
                choices = [_norm(l) if len(_norm(l)) <= cmax else c for c, l in zip(choices, choices_long)]
        problems = []
        if not q:
            problems.append("empty question")
        if len(q) > qmax:
            problems.append(f"question was {len(q)} characters (max {qmax})")
        if len(choices) != len(choices_long) or not all(choices):
            problems.append("wrong number of choices")
        problems += [f"choice {i + 1} was {len(c)} characters (max {cmax})" for i, c in enumerate(choices) if len(c) > cmax]
        if not problems:
            return {"question": q, "choices": choices}
        feedback = "Your previous attempt failed: " + "; ".join(problems) + ". Fix that."
    raise AIError("couldn't fit the LED limits - shorten it by hand")


def needs_wording(q):
    """True for an AI question whose LED wording hasn't been made yet. The
    AI writes questions as full sentences; that text IS the phone version,
    and the terse LED headline is made from it. Hand-written/edited ones are
    left to the person (Generate short button)."""
    return (not q.get("led_short") and q.get("source", "ai") == "ai"
            and audit_of(q)["stage"] not in ("edited", "verified"))


def ensure_wording(key, title, artist, qid):
    """Full AI sentence -> phone (long) fields; terse headline -> LED (short)
    fields. Best effort; called right after a question is generated, and by
    the Generate job's backfill."""
    q = next((x for x in _engine().peek(key) if x.get("qid") == qid), None)
    if not q or not needs_wording(q):
        return False
    sig = _sig(q)
    long_q, long_c = q["question"], list(q["choices"])
    got = generate_short(song_ref(key), long_q, long_c, is_tf(q))

    def apply(lst):
        cur = next((x for x in lst if x.get("qid") == qid), None)
        if cur is None or _sig(cur) != sig:
            return False        # changed (or deleted) while the AI was working
        cur["question_long"], cur["choices_long"] = long_q, long_c
        cur["question"], cur["choices"] = got["question"], got["choices"]
        cur["led_short"] = True
        a = cur["audit"] = dict(audit_of(cur))
        if a["stage"] == "audited_ok":
            # The room reads the LED text - a new wording needs its own check.
            a.update(stage="generated", ts=time.time(), note="LED wording rewritten - audit again")
        _history(cur, "Haiku 4.5", "LED wording", got["question"])
        return True
    return _engine().edit(key, apply)


ensure_long_version = ensure_wording   # old name


# ------------------------------------------------------------------
# The AI audit
# ------------------------------------------------------------------
def _prompt_blind(song, q_text, choices, tf):
    opts = "\n".join(f"{i}) {c}" for i, c in enumerate(choices))
    return (
        "You are a meticulous fact-checker for a paid, live music-trivia night. A wrong 'correct "
        "answer' in front of a paying room is a serious failure, so be skeptical and rely only on "
        "facts you are genuinely sure of. Never guess.\n\n"
        f"{song}\n\n"
        "Answer this trivia question yourself. You are NOT told which choice is marked correct.\n"
        + ("This is a True/False statement: decide whether it is true.\n" if tf else "")
        + f"Question: {q_text}\nChoices (0-based):\n{opts}\n\n"
        "Reply with ONLY a single-line JSON object (no markdown) with keys: "
        "\"answer_index\" (0-based index of the correct choice, or -1 if you cannot tell), "
        "\"confidence\" (\"high\" only if you are certain of the specific fact, otherwise \"medium\" or \"low\"), "
        "\"ambiguous\" (true if the question is unclear, more than one choice could be argued correct, "
        "or the fact is disputed or differs between reliable sources, e.g. different release years in "
        "different countries), "
        "\"on_topic\" (true if the question is really about this song, this artist, or the era the song comes from), "
        "\"reasoning\" (at most 25 words: the specific fact you relied on)."
    )


def _prompt_text_check(song, q, final_idx, tf):
    choices = q["choices"]
    lines = [
        "You are a meticulous fact-checker for a paid, live music-trivia night. Be skeptical; rely only "
        "on facts you are genuinely sure of.", "", song, "",
        f"Question (SHORT, LED sign): {q['question']}",
        f"Choices (SHORT): {json.dumps(choices)}",
    ]
    if q.get("question_long"):
        lines += [f"Question (LONG, phones): {q['question_long']}",
                  f"Choices (LONG): {json.dumps(q.get('choices_long', []))}"]
    lines += [
        f"The correct answer is: {choices[final_idx]}",
        f"Headline shown to the room: {q.get('headline', '')}",
        f"Factoid shown to the room: {q.get('full', '')}",
        f"Correction shown when a true/false statement is FALSE: {q.get('correction', '')}",
        "",
        "Tasks: (1) \"consistent\": do the SHORT and LONG versions ask the same thing with the same "
        "correct answer, and does the LONG version avoid adding any claim that is false or not in the "
        "SHORT version (e.g. a wrong nationality or date)? (true if there is no LONG version). (2) Check every factual claim in the "
        "headline, factoid and correction. If any claim is wrong, unsupported or contested, rewrite it "
        "using only facts you are sure of (headline max 40 characters, factoid max 200, correction max "
        "100); otherwise return it unchanged. "
        + ("The correction is needed ONLY because the answer is False: it must state the actual true fact."
           if tf and final_idx == 1 else
           "The correction must be an empty string."),
        "(3) \"unsure\": true if there is anything here you could not confirm and a human should research.",
        "",
        "Reply with ONLY a single-line JSON object: {\"consistent\": true, \"headline\": \"...\", "
        "\"full\": \"...\", \"correction\": \"...\", \"unsure\": false, \"note\": \"at most 25 words: what you changed and why, or empty\"}",
    ]
    return "\n".join(lines)


def _shown(q):
    """The wording the blind answer is given: the phone version when there is one."""
    choices = q["choices"]
    shown_q = q.get("question_long") or q["question"]
    shown_c = q.get("choices_long") if len(q.get("choices_long") or []) == len(choices) else choices
    return shown_q, shown_c


def _run_audit(key, q, model):
    """Pure decision step: returns (stage, note, changes) where changes is a
    dict of content fields to overwrite ({} for none). Raises AIError."""
    song = song_ref(key)
    shown_q, shown_c = _shown(q)
    if not (0 <= q.get("correct_index", -1) < len(q["choices"])):
        return "audited_flagged", "No correct answer is marked on this question - mark one.", {}
    a = _ask_json(_prompt_blind(song, shown_q, shown_c, is_tf(q)), model)
    return _decide(q, a, lambda final: _ask_json(
        _prompt_text_check(song, dict(q, correct_index=final), final, is_tf(q)), model))


def _decide(q, a, text_check):
    """The audit rules, shared by the API audit and the Claude Code import.
    `a` is the blind answer {answer_index, confidence, ambiguous, on_topic,
    reasoning}; text_check(final_index) returns the text check {consistent,
    headline, full, correction, unsure, note}."""
    tf = is_tf(q)
    choices = q["choices"]
    stored = q.get("correct_index", -1)
    if not (0 <= stored < len(choices)):
        return "audited_flagged", "No correct answer is marked on this question - mark one.", {}
    try:
        ans = int(a.get("answer_index", -1))
    except (TypeError, ValueError):
        ans = -1
    conf = str(a.get("confidence", "low")).lower()
    amb = bool(a.get("ambiguous"))
    on_topic = a.get("on_topic", True) is not False
    why = _norm(a.get("reasoning"))

    if not on_topic:
        return "audited_flagged", f"Question doesn't seem to be about this song, artist or era. {why}", {}
    if not (0 <= ans < len(choices)):
        return "audited_flagged", f"Auditor could not determine the answer. {why}", {}
    sure = conf == "high" and not amb
    if ans == stored:
        if not sure:
            return "audited_flagged", (f"Auditor agrees with the answer but is not certain "
                                       f"({conf}{', disputed/ambiguous' if amb else ''}). {why}"), {}
        final, flipped = stored, False
    else:
        if not sure:
            return "audited_flagged", (f"Auditor thinks the answer is {choices[ans]!r}, not {choices[stored]!r}, "
                                       f"but is not sure enough to change it ({conf}{', disputed' if amb else ''}). {why}"), {}
        final, flipped = ans, True

    b = text_check(final)
    if b.get("consistent") is False:
        return "audited_flagged", (f"The phone (long) wording doesn't match the LED wording or adds a wrong "
                                   f"detail - fix or regenerate it. {_norm(b.get('note'))}"), {}
    if bool(b.get("unsure")):
        return "audited_flagged", f"Auditor could not confirm the supporting text. {_norm(b.get('note'))}", {}

    changes = {}
    if flipped:
        changes["correct_index"] = final
    for field, cap in (("headline", 40), ("full", 200), ("correction", 100)):
        new = _norm(b.get(field))[:cap]
        if field == "correction":
            new = new if (tf and final == 1) else ""
            if tf and final == 1 and not new:
                return "audited_flagged", "Answer is False but the auditor gave no correction text - add one by hand.", {}
        elif not new:
            continue
        if new != _norm(q.get(field)):
            changes[field] = new
    if not changes:
        return "audited_ok", f"Independently answered the same way. {why}", {}
    parts = []
    if flipped:
        parts.append(f"answer changed from {choices[stored]!r} to {choices[final]!r}")
    parts += [f"{f} reworded" for f in changes if f != "correct_index"]
    return "audited_corrected", f"Corrected: {'; '.join(parts)}. {why} {_norm(b.get('note'))}".strip(), changes


def audit_question(key, qid, deep=False):
    """Audits one question (blocking, ~10-30 s). Returns the serialized
    question. Raises AIError if the AI could not be reached/understood
    (the question is left untouched), ValueError if it vanished."""
    model = config.QUESTION_DEEP_AUDIT_MODEL if deep else config.QUESTION_AUDIT_MODEL
    q = next((x for x in _engine().peek(key) if x.get("qid") == qid), None)
    if q is None:
        raise ValueError("question not found")
    sig = _sig(q)
    stage, note, changes = _run_audit(key, q, model)
    _backup_once()
    out = _apply_audit(key, qid, sig, stage, note, changes, _model_label(model),
                       "deep audit" if deep else "audit", deep)
    if out is None:
        raise ValueError("question not found")
    if out == "changed":
        raise ValueError("question was edited while the audit ran - run it again")
    return out


def _apply_audit(key, qid, sig, stage, note, changes, who, action, deep):
    """Writes an audit result. Returns the serialized question, None if it
    is gone, or "changed" if its content moved on since `sig` was taken."""
    def apply(lst):
        cur = next((x for x in lst if x.get("qid") == qid), None)
        if cur is None:
            return None
        if _sig(cur) != sig:
            return "changed"
        cur["audit"] = dict(audit_of(cur))
        a = cur["audit"]
        if changes:
            if not a.get("original"):
                a["original"] = _content(cur)
            cur.update(changes)
        a.update(stage=stage, by=who, ts=time.time(), note=note, deep=bool(deep))
        _history(cur, who, action, f"{STAGES[stage][1]}. {note}")
        return serialize_question(cur)
    return _engine().edit(key, apply)


# ------------------------------------------------------------------
# Offline audit by Claude Code (the operator's subscription, no API
# credits): export a batch, Claude answers blind and checks the text,
# import applies the same rules as the API audit (_decide).
# ------------------------------------------------------------------
_OFFLINE_WHO = "Claude Code (Opus 5.5)"


def export_batch(n_songs=35, seed=None):
    """Unaudited questions of up to `n_songs` random library songs whose LED
    wording is done. Each item carries a `sig` so the import refuses a
    question that changed in between. Includes the stored answer - the
    caller splits it off so the first pass stays blind."""
    snap = _engine().snapshot()
    lib = _library_index()
    keys = [k for k in sorted(snap) if k in lib and any(
        audit_of(q)["stage"] in ("generated", "edited") and q.get("source") != "fallback"
        and not needs_wording(q) and q.get("qid") for q in snap[k])]
    random.Random(seed).shuffle(keys)
    items = []
    for k in keys[:n_songs]:
        for q in snap[k]:
            if (audit_of(q)["stage"] not in ("generated", "edited") or q.get("source") == "fallback"
                    or needs_wording(q) or not q.get("qid")):
                continue
            shown_q, shown_c = _shown(q)
            items.append({
                "key": k, "qid": q["qid"], "sig": _sig(q), "song": song_ref(k), "tf": is_tf(q),
                "question": shown_q, "choices": shown_c,
                "led_question": q["question"], "led_choices": q["choices"],
                "correct_index": q.get("correct_index", -1),
                "headline": q.get("headline", ""), "full": q.get("full", ""),
                "correction": q.get("correction", ""),
            })
    return {"ok": True, "songs": min(n_songs, len(keys)), "songs_waiting": len(keys), "items": items}


def import_results(results):
    """results: [{key, qid, sig, blind: {...}, text: {...} or null}].
    Same decision rules as the API audit. Returns per-stage counts."""
    _backup_once()
    counts, problems = Counter(), []
    for r in results:
        key, qid = r.get("key"), r.get("qid")
        q = next((x for x in _engine().peek(key) if x.get("qid") == qid), None)
        if q is None:
            problems.append(f"{key}: question gone")
            continue
        if _sig(q) != r.get("sig"):
            problems.append(f"{key}: changed since export - skipped")
            continue
        text = r.get("text")

        def text_check(final, text=text):
            if not isinstance(text, dict):
                return {"unsure": True, "note": "Text check missing from the import."}
            return text
        stage, note, changes = _decide(q, r.get("blind") or {}, text_check)
        out = _apply_audit(key, qid, r["sig"], stage, note, changes, _OFFLINE_WHO, "audit", False)
        if isinstance(out, dict):
            counts[stage] += 1
        else:
            problems.append(f"{key}: {'changed' if out else 'gone'}")
    return {"ok": True, "counts": dict(counts), "problems": problems}


# ------------------------------------------------------------------
# Editor operations (human)
# ------------------------------------------------------------------
def _validate(p):
    """Normalises an editor payload; returns (clean_fields, error)."""
    tf = p.get("type") == "tf"
    f = {
        "question": _norm(p.get("question")),
        "question_long": _norm(p.get("question_long")),
        "correction": _norm(p.get("correction")),
        "headline": _norm(p.get("headline")),
        "full": _norm(p.get("full")),
    }
    if not f["question"]:
        return None, "The short (LED) question is empty - write it, or tap Generate short."
    if len(f["question"]) > config.QUESTION_SHORT_MAX_CHARS:
        return None, (f"The short question is {len(f['question'])} characters; the LED limit is "
                      f"{config.QUESTION_SHORT_MAX_CHARS}. Tap Generate short.")
    if len(f["question_long"]) > config.QUESTION_LONG_MAX_CHARS:
        return None, f"The long question is over {config.QUESTION_LONG_MAX_CHARS} characters."
    if len(f["headline"]) > 40 or len(f["full"]) > 200 or len(f["correction"]) > 100:
        return None, "Headline max 40, factoid max 200, correction max 100 characters."
    if tf:
        f["choices"], f["choices_long"] = ["True", "False"], ["True", "False"]
        f["question"] = f["question"] if f["question"].lower().startswith("t/f") else "T/F: " + f["question"]
    else:
        choices = [_norm(c) for c in (p.get("choices") or [])]
        if not (2 <= len(choices) <= 4) or not all(choices):
            return None, "A multiple-choice question needs 2 to 4 filled-in choices."
        if len({c.lower() for c in choices}) != len(choices):
            return None, "Two choices are the same."
        too_long = [i + 1 for i, c in enumerate(choices) if len(c) > config.QUESTION_SHORT_CHOICE_MAX_CHARS]
        if too_long:
            return None, (f"Choice {too_long[0]} is over {config.QUESTION_SHORT_CHOICE_MAX_CHARS} characters "
                          "for the LED. Shorten it or tap Generate short.")
        f["choices"] = choices
        cl = [_norm(c) for c in (p.get("choices_long") or [])]
        if any(cl):
            if len(cl) != len(choices) or not all(cl):
                return None, "Fill in every long choice, or clear them all."
            if any(len(c) > config.QUESTION_LONG_CHOICE_MAX_CHARS for c in cl):
                return None, f"A long choice is over {config.QUESTION_LONG_CHOICE_MAX_CHARS} characters."
        f["choices_long"] = cl if any(cl) else []
    try:
        ci = int(p.get("correct_index", -1))
    except (TypeError, ValueError):
        ci = -1
    if not (0 <= ci < len(f["choices"])):
        return None, "Mark which choice is correct."
    f["correct_index"] = ci
    if tf and ci == 0:
        f["correction"] = ""
    # Long wording must come as a pair.
    if bool(f["question_long"]) != bool(f["choices_long"]):
        f["question_long"], f["choices_long"] = "", []
    return f, None


def save_question(key, payload):
    """Creates (no qid) or updates one question from the editor. A change to
    any content drops it back to 'changed by hand, unverified' (red) unless
    `verify` is set; an unchanged save leaves its stage alone."""
    f, err = _validate(payload)
    if err:
        return {"ok": False, "reason": err}
    qid = payload.get("qid")
    verify = bool(payload.get("verify"))
    _backup_once()

    def apply(lst):
        now = time.time()
        if not qid:
            q = {"qid": uuid.uuid4().hex[:12], "category": "manual", "ts": now, "release_year": None}
            q.update(f)
            q["source"] = "manual"
            q["audit"] = {"stage": "edited", "by": "You", "ts": now, "note": "Added by hand",
                          "deep": False, "original": None, "history": []}
            _history(q, "You", "added", "")
            lst.append(q)
        else:
            q = next((x for x in lst if x.get("qid") == qid), None)
            if q is None:
                return None
            if _sig(dict(q, **f)) != _sig(q):
                q["audit"] = dict(audit_of(q))
                if not q["audit"].get("original"):
                    q["audit"]["original"] = _content(q)     # what it said before the first change
                q.update(f)
                q["audit"].update(stage="edited", by="You", ts=now, note="Changed by hand", deep=False)
                _history(q, "You", "edited", "")
            elif "audit" not in q:
                q["audit"] = dict(audit_of(q))
        if verify:
            q["audit"] = dict(audit_of(q))
            q["audit"].update(stage="verified", by="You", ts=now, note="Verified by a human", deep=False)
            _history(q, "You", "verified", "")
        return serialize_question(q)
    out = _engine().edit(key, apply)
    if out is None:
        return {"ok": False, "reason": "That question no longer exists - reload the page."}
    return {"ok": True, "question": out}


def set_verified(key, qid, verified):
    _backup_once()

    def apply(lst):
        q = next((x for x in lst if x.get("qid") == qid), None)
        if q is None:
            return None
        q["audit"] = dict(audit_of(q))
        now = time.time()
        if verified:
            q["audit"].update(stage="verified", by="You", ts=now, note="Verified by a human", deep=False)
            _history(q, "You", "verified", "")
        else:
            q["audit"].update(stage="edited", by="You", ts=now, note="Verification cleared", deep=False)
            _history(q, "You", "verification cleared", "")
        return serialize_question(q)
    out = _engine().edit(key, apply)
    if out is None:
        return {"ok": False, "reason": "That question no longer exists - reload the page."}
    return {"ok": True, "question": out}


def delete_question(key, qid):
    _backup_once()

    def apply(lst):
        for i, q in enumerate(lst):
            if q.get("qid") == qid:
                _log_deleted(key, q)
                del lst[i]
                return True
        return False
    return {"ok": bool(_engine().edit(key, apply))}


def ai_add_question(key):
    """One AI-written question for a song with room (the editor's 'AI: add
    one'). Blocking ~5-15 s."""
    t = _library_index().get(key)
    if not t:
        t = _split_key(key)
    title, artist = t
    if len(_engine().peek(key)) >= config.TRACK_QUESTIONS_PER_TRACK:
        return {"ok": False, "reason": "This song already has all its questions."}
    if key in _engine()._inflight:
        return {"ok": False, "reason": "That song is being worked on right now - try again in a moment."}
    _engine()._inflight.add(key)
    try:
        _backup_once()
        reason = _engine()._fill_one_slot(key, title, artist)
    finally:
        _engine()._inflight.discard(key)
    if reason == "AI_NOT_CONFIDENT":
        _mark_unknown(key)
        return {"ok": False, "reason": "The AI doesn't know this song well enough to write a question - add one by hand."}
    if reason:
        return {"ok": False, "reason": f"AI request failed: {reason}"}
    return {"ok": True}


def _split_key(key):
    """Best-effort (title, artist) from an 'artist - title' cache key whose
    song isn't in the library."""
    if " - " in key:
        artist, title = key.split(" - ", 1)
        return title, artist
    return key, ""


# ------------------------------------------------------------------
# Overview for the page
# ------------------------------------------------------------------
def overview():
    snap = _engine().snapshot()
    unknown = _load_meta()["unknown"]
    songs = {}
    for t in music_library.all_tracks():
        key = sanitize_track_key(t["title"], t["artist"])
        songs.setdefault(key, {"key": key, "title": t["title"], "artist": t["artist"], "in_library": True})
    for key in snap:
        songs.setdefault(key, {"key": key, "title": key, "artist": "", "in_library": False})
    out, by_stage, total_q, long_done, n_full = [], Counter(), 0, 0, 0
    for key, s in songs.items():
        qs = snap.get(key, [])
        counts = Counter(color_of(q) for q in qs)
        s.update(n=len(qs), counts={c: counts.get(c, 0) for c in ("amber", "red", "lime", "green")},
                 color=song_color(qs), unknown=key in unknown,
                 can_generate=factoid_engine._looks_like_real_track(s["title"], s["artist"]))
        out.append(s)
        total_q += len(qs)
        long_done += sum(1 for q in qs if not needs_wording(q))
        by_stage.update(audit_of(q)["stage"] for q in qs)
        if s["in_library"] and len(qs) >= config.TRACK_QUESTIONS_PER_TRACK:
            n_full += 1
    out.sort(key=lambda s: (COLOR_ORDER.index(s["color"]), s["artist"].lower(), s["title"].lower()))
    lib_songs = sum(1 for s in out if s["in_library"])
    return {
        "songs": out,
        "stats": {
            "library_songs": lib_songs, "songs_full": n_full,
            "questions": total_q, "long_done": long_done,
            "by_stage": dict(by_stage),
            "by_color": dict(Counter(s["color"] for s in out)),
            "unaudited": by_stage.get("generated", 0) + by_stage.get("edited", 0),
            "amber": by_stage.get("audited_flagged", 0) + by_stage.get("audited_corrected", 0),
            "verified": by_stage.get("verified", 0),
        },
        "stage_info": {k: {"color": v[0], "label": v[1], "action": v[2]} for k, v in STAGES.items()},
        "target": config.TRACK_QUESTIONS_PER_TRACK,
        "models": {"audit": _model_label(config.QUESTION_AUDIT_MODEL), "deep": _model_label(config.QUESTION_DEEP_AUDIT_MODEL)},
    }


def song_detail(key):
    qs = _engine().peek(key)
    qs.sort(key=lambda q: _QUESTION_ORDER[color_of(q)])
    t = _library_index().get(key)
    title, artist = t if t else (key, "")
    return {"key": key, "title": title, "artist": artist, "in_library": bool(t),
            "unknown": key in _load_meta()["unknown"],
            "questions": [serialize_question(q) for q in qs], "target": config.TRACK_QUESTIONS_PER_TRACK}


# ------------------------------------------------------------------
# Bulk jobs (one at a time): generate missing questions / audit
# ------------------------------------------------------------------
_job_lock = threading.Lock()
_job = {"kind": "", "state": "idle"}
_stop = threading.Event()
# On app shutdown Python joins every thread-pool worker until its queue is
# empty (hundreds of AI calls); stop the job first so they drain instantly.
# Runs before concurrent.futures' own exit hook (reverse registration order).
threading._register_atexit(_stop.set)


def job_status():
    with _job_lock:
        j = dict(_job)
    j["tokens"] = {m: {"in": v[0], "out": v[1]} for m, v in token_tracker.get_other().items()}
    return j


def _job_set(**kw):
    with _job_lock:
        _job.update(kw)


def _job_add(field, n=1):
    with _job_lock:
        _job[field] = _job.get(field, 0) + n


def _job_msg(text):
    with _job_lock:
        _job.setdefault("log", []).append(f"{time.strftime('%H:%M:%S')} {text}")
        del _job["log"][:-12]


def stop_job():
    _stop.set()
    return True


def _begin(kind, total, **extra):
    with _job_lock:
        if _job.get("state") == "running":
            return False
        _job.clear()
        _job.update(kind=kind, state="running", total=total, done=0, errors=0, current="",
                    started=time.time(), finished=None, log=[], last_error="", **extra)
    _stop.clear()
    return True


def _finish(state, error=""):
    _job_set(state=state, finished=time.time(), current="", last_error=error or _job.get("last_error", ""))


def start_generate(wording_only=False):
    """Fill every library song up to 3 questions, then write the LED
    wording for every question that lacks it (only the latter with
    `wording_only`)."""
    if not config.FACTOID_AI_ENABLED:
        return {"ok": False, "reason": "No Anthropic API key configured."}
    eng = _engine()
    snap = eng.snapshot()
    unknown = _load_meta()["unknown"]
    target = config.TRACK_QUESTIONS_PER_TRACK
    songs = []
    for key, (title, artist) in sorted(_library_index().items()):
        n = len(snap.get(key, []))
        if n < target and key not in unknown and factoid_engine._looks_like_real_track(title, artist):
            songs.append((key, title, artist, target - n))
    if wording_only:
        songs = []
    new_total = sum(s[3] for s in songs)
    long_todo = [(k, q["qid"]) for k, lst in snap.items() for q in lst
                 if needs_wording(q) and q.get("qid")]
    if not songs and not long_todo:
        return {"ok": False, "reason": "Nothing to generate - every song has its questions and phone wording."}
    if not _begin("generate", new_total + len(long_todo), phase="questions", new_questions=0,
                  skipped_songs=0, songs=len(songs), long_todo=len(long_todo)):
        return {"ok": False, "reason": "Another job is already running."}
    threading.Thread(target=_generate_worker, args=(songs, long_todo), daemon=True).start()
    return {"ok": True, "songs": len(songs), "new_questions": new_total, "long_versions": len(long_todo)}


def _generate_worker(songs, long_todo):
    eng = _engine()
    fatal = [""]

    def do_song(item):
        key, title, artist, need = item
        _job_set(current=f"{artist} - {title}" if artist else title)
        eng._inflight.add(key)
        try:
            made, abort = _fill_song(key, title, artist, need)
        finally:
            eng._inflight.discard(key)
        _job_add("done", need)           # unknown/failed songs still advance the bar
        _job_add("new_questions", made)
        return abort

    try:
        _backup_once()
        retry = []
        for item in songs:
            if _stop.is_set():
                break
            if item[0] in eng._inflight:      # the live show is fetching it right now
                retry.append(item)
                continue
            abort = do_song(item)
            if abort:
                fatal[0] = abort
                break
        if retry and not fatal[0] and not _stop.is_set():
            time.sleep(3)
            for item in retry:
                if _stop.is_set() or item[0] in eng._inflight:
                    _job_add("done", item[3])
                    continue
                abort = do_song(item)
                if abort:
                    fatal[0] = abort
                    break
        if not fatal[0] and not _stop.is_set() and long_todo:
            _job_set(phase="LED wording", current="")
            consecutive = [0]

            def one(item):
                if _stop.is_set():
                    return
                k, qid = item
                try:
                    title, artist = _library_index().get(k) or _split_key(k)
                    ensure_wording(k, title, artist, qid)
                    consecutive[0] = 0
                except Exception as e:
                    consecutive[0] += 1
                    _job_add("errors")
                    _job_set(last_error=str(e))
                    if "AUTH" in str(e) or "QUOTA" in str(e) or consecutive[0] >= 8:
                        fatal[0] = str(e)
                        _stop.set()
                finally:
                    _job_add("done")
            with concurrent.futures.ThreadPoolExecutor(max_workers=config.QUESTION_AUDIT_WORKERS) as pool:
                list(pool.map(one, long_todo))
        if fatal[0]:
            _finish("error", fatal[0])
        else:
            _finish("stopped" if _stop.is_set() else "done")
    except Exception as e:
        print(f"[QUESTION AUDIT] generate job crashed: {e}")
        _finish("error", str(e))


def _fill_song(key, title, artist, need):
    """Returns (questions_made, abort_reason_or_None)."""
    eng = _engine()
    made = 0
    for _ in range(need):
        if _stop.is_set():
            break
        before = eng.peek(key)
        reason = eng._fill_one_slot(key, title, artist)
        after = eng.peek(key)
        new = [q for q in after if q.get("qid") not in {b.get("qid") for b in before}]
        if new and new[0].get("source") == "fallback":
            # Offline fallback got cached instead of a real question: undo it
            # and stop - the internet is down.
            eng.edit(key, lambda lst: lst.remove(next(x for x in lst if x.get("qid") == new[0]["qid"])))
            return made, "No internet connection - stopped."
        if reason == "AI_NOT_CONFIDENT":
            _mark_unknown(key)
            _job_add("skipped_songs")
            _job_msg(f"AI doesn't know '{title}' - add its questions by hand")
            break
        if reason:
            _job_add("errors")
            _job_set(last_error=str(reason))
            if "AUTH" in reason or "QUOTA" in reason:
                return made, reason
            break
        made += 1
    return made, None


_AUDIT_SCOPES = {"unaudited": ("generated", "edited"), "amber": ("audited_flagged", "audited_corrected")}
# Per-question defaults until a real run has been measured (2026-10-10 test,
# after shortening the explanations): tokens in/out, wall seconds per question
# with QUESTION_AUDIT_WORKERS running at once.
_DEFAULT_COST = {"in": 1000, "out": 800, "sec": 4.0}


def _audit_todo(scope):
    """{song key: [qid, ...]} of questions waiting for this kind of audit."""
    stages = _AUDIT_SCOPES[scope]
    snap = _engine().snapshot()
    out = {}
    for k in sorted(snap):
        ids = [q["qid"] for q in snap[k] if q.get("qid") and audit_of(q)["stage"] in stages
               and q.get("source") != "fallback"]
        if ids:
            out[k] = ids
    return out


def audit_estimate(scope):
    if scope not in _AUDIT_SCOPES:
        return {"ok": False, "reason": "unknown scope"}
    deep = scope == "amber"
    model = config.QUESTION_DEEP_AUDIT_MODEL if deep else config.QUESTION_AUDIT_MODEL
    price = config.QUESTION_DEEP_AUDIT_PRICE_PER_M if deep else config.QUESTION_AUDIT_PRICE_PER_M
    todo = _audit_todo(scope)
    measured = _load_meta().get("cost", {}).get(model)
    c = measured or _DEFAULT_COST
    sec = c["sec"] * (3.0 if deep and not measured else 1.0)
    per_q = (c["in"] * price[0] + c["out"] * price[1]) / 1e6
    return {"ok": True, "scope": scope, "model": _model_label(model),
            "songs": len(todo), "questions": sum(len(v) for v in todo.values()),
            "per_question_usd": per_q, "per_question_sec": sec, "measured": bool(measured)}


def _record_cost(model, tokens_before, done, elapsed):
    """After a bulk run: remember the real per-question tokens/time so the
    next estimate is based on this Pi's actual numbers."""
    if done < 3:
        return
    after = token_tracker.get_other().get(model, (0, 0))
    tin, tout = after[0] - tokens_before[0], after[1] - tokens_before[1]
    if tin <= 0:
        return
    with _meta_lock:
        m = _load_meta()
        m.setdefault("cost", {})[model] = {"in": round(tin / done), "out": round(tout / done),
                                           "sec": round(elapsed / done, 2)}
        tmp = _META_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(m, f)
        os.replace(tmp, _META_PATH)


def start_audit(scope="unaudited", percent=100):
    """scope 'unaudited': red questions, Sonnet. 'amber': re-check amber ones
    with the deep (Opus) model. `percent` = share of the waiting SONGS to do,
    picked at random across the library (so a small run is a fair sample of
    how accurate the whole library is); every waiting question of a picked
    song is audited, so that song's colour settles."""
    if not config.FACTOID_AI_ENABLED:
        return {"ok": False, "reason": "No Anthropic API key configured."}
    if scope not in _AUDIT_SCOPES:
        return {"ok": False, "reason": "unknown scope"}
    deep = scope == "amber"
    by_song = _audit_todo(scope)
    if not by_song:
        return {"ok": False, "reason": "No questions are waiting for that kind of audit."}
    try:
        percent = max(0.1, min(100.0, float(percent)))
    except (TypeError, ValueError):
        percent = 100.0
    keys = list(by_song)
    n = max(1, round(len(keys) * percent / 100.0))
    if n < len(keys):
        keys = random.sample(keys, n)
    todo = [(k, qid) for k in keys for qid in by_song[k]]
    if not _begin("deep audit" if deep else "audit", len(todo), deep=deep, percent=percent, song_count=len(keys),
                  results={"audited_ok": 0, "audited_corrected": 0, "audited_flagged": 0}):
        return {"ok": False, "reason": "Another job is already running."}
    threading.Thread(target=_audit_worker, args=(todo, deep), daemon=True).start()
    return {"ok": True, "songs": len(keys), "questions": len(todo)}


def _audit_worker(todo, deep):
    consecutive = [0]
    fatal = [""]

    def one(item):
        if _stop.is_set():
            return
        key, qid = item
        _job_set(current=key)
        try:
            out = audit_question(key, qid, deep=deep)
            consecutive[0] = 0
            with _job_lock:
                _job["results"][out["audit"]["stage"]] = _job["results"].get(out["audit"]["stage"], 0) + 1
        except ValueError as e:
            _job_msg(f"{key}: {e}")
        except Exception as e:
            consecutive[0] += 1
            _job_add("errors")
            _job_set(last_error=str(e))
            if "AUTH" in str(e) or "QUOTA" in str(e) or consecutive[0] >= 6:
                fatal[0] = str(e)
                _stop.set()
        finally:
            _job_add("done")
    model = config.QUESTION_DEEP_AUDIT_MODEL if deep else config.QUESTION_AUDIT_MODEL
    tokens_before = token_tracker.get_other().get(model, (0, 0))
    t0 = time.time()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=config.QUESTION_AUDIT_WORKERS) as pool:
            list(pool.map(one, todo))
        with _job_lock:
            finished_ok = sum(_job.get("results", {}).values())
        _record_cost(model, tokens_before, finished_ok, time.time() - t0)
        if fatal[0]:
            _finish("error", fatal[0])
        else:
            _finish("stopped" if _stop.is_set() else "done")
    except Exception as e:
        print(f"[QUESTION AUDIT] audit job crashed: {e}")
        _finish("error", str(e))
