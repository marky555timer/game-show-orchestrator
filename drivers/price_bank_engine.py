import collections
import csv
import random
import re
import time

import config


def _ensure_dollar_prefix(s):
    """Guarantees a price string starts with "$" -- defends against a
    hand-edited CSV row (this bank is documented as hand-editable) losing
    its "$" during a typo/copy-paste, which would otherwise show up on the
    board as a dollar-sign-less price with no other symptom. Local, tiny
    duplicate of drivers/factoid_engine.py's _ensure_dollar_prefix (kept
    separate rather than imported, to avoid pulling that module's much
    larger dependency chain into this one for a 4-line helper)."""
    s = str(s).strip()
    if s.startswith("$"):
        return s
    if s and re.fullmatch(r"[0-9.,]+", s):
        return f"${s}"
    return s

# Loaded once at import time -- config.PRICE_GAME_BANK_PATH is a small,
# hand-editable CSV (3 rows per year across PRICE_GAME_MIN_YEAR..
# PRICE_GAME_MAX_YEAR as generated, though nothing here requires an even
# count) checked in as show content, not something that changes mid-run. If
# the operator edits it while the app is live, restart to pick it up --
# same tradeoff as fallback_questions.json.
_ROWS = []
_BY_YEAR = {}
_YEARS = []

# Session no-repeat: a question the room has already been asked never comes
# back, no matter how many Price Games get played, until the app restarts.
# Keyed by (year, product) rather than row index so hand-editing/reordering
# the CSV mid-session can't accidentally resurrect an already-used question.
_used_keys = set()


def _row_key(row):
    return (row["year"], row["product"])


def _load_rows():
    rows = []
    try:
        with open(config.PRICE_GAME_BANK_PATH, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                year = (row.get("year") or "").strip()
                correct = (row.get("correct_price") or "").strip()
                if not year.isdigit() or not correct:
                    continue
                row["year"] = int(year)
                rows.append(row)
    except Exception as e:
        print(f"[PRICE BANK] Failed to read {config.PRICE_GAME_BANK_PATH}: {e}")
    return rows


def _reload():
    global _ROWS, _BY_YEAR, _YEARS, _used_keys
    _ROWS = _load_rows()
    grouped = collections.defaultdict(list)
    for row in _ROWS:
        grouped[row["year"]].append(row)
    _BY_YEAR = dict(grouped)
    _YEARS = sorted(_BY_YEAR)
    _used_keys = set()
    if _YEARS:
        print(f"[PRICE BANK] Loaded {len(_ROWS)} price-game question(s) covering "
              f"{_YEARS[0]}-{_YEARS[-1]} from {config.PRICE_GAME_BANK_PATH}.")
    else:
        print(f"[PRICE BANK] No usable rows in {config.PRICE_GAME_BANK_PATH} -- "
              f"Btn6 will fall back to a generic offline question.")


_reload()


def bank_size():
    return len(_ROWS)


def remaining():
    """How many questions are still unasked this session."""
    return len(_ROWS) - len(_used_keys)


def reset_session():
    _used_keys.clear()


def _years_by_proximity(target_year):
    """Years ordered nearest-first around `target_year`. Equidistant years
    (target-1 vs target+1) are broken randomly rather than always reaching
    the same direction, so repeat plays of songs from the same era don't
    march predictably backwards through the bank."""
    if target_year is None:
        return random.sample(_YEARS, len(_YEARS))
    return sorted(_YEARS, key=lambda y: (abs(y - target_year), random.random()))


def _pick_unused(target_year):
    for year in _years_by_proximity(target_year):
        pool = [r for r in _BY_YEAR[year] if _row_key(r) not in _used_keys]
        if pool:
            return random.choice(pool)
    return None


def draw_price_question(target_year=None):
    """Instant, network-free draw of exactly one Price Game question --
    returns the same result-dict shape drivers/factoid_engine.py's AI fetch
    path produces (consumable by factoid_engine.apply_price_question()), or
    None if the bank is empty/unreadable.

    `target_year` is the currently-playing song's release year: the question
    is drawn from that same year when possible, falling outward to the
    nearest year in either direction whose questions aren't all used up yet.
    Passing None (no year known for this track) draws from a random year
    instead. Nothing repeats within a session -- once the whole bank is
    exhausted the used-set resets and the cycle starts over, which is the
    only way the room ever sees the same question twice."""
    if not _ROWS:
        return None

    row = _pick_unused(target_year)
    if row is None:
        # Every question in the bank has been asked this session. Recycling
        # beats refusing to fire the cue.
        print(f"[PRICE BANK] All {len(_ROWS)} questions used this session -- "
              f"recycling the bank from the start.")
        _used_keys.clear()
        row = _pick_unused(target_year)
        if row is None:
            return None

    _used_keys.add(_row_key(row))

    year = row["year"]
    if target_year is None:
        print(f"[PRICE BANK] No release year known for this track -- drew "
              f"{year} {row['product']!r} at random ({remaining()} left).")
    else:
        drift = year - target_year
        how = "exact match" if drift == 0 else f"{abs(drift)}yr {'later' if drift > 0 else 'earlier'}"
        print(f"[PRICE BANK] Track year {target_year} -> drew {year} "
              f"{row['product']!r} ({how}, {remaining()} left).")

    short_name = (row.get("short_name") or "").strip()[:config.PRICE_GAME_ITEM_MAX_CHARS]
    product = (row.get("product") or "").strip()
    question = (row.get("question") or f"How much was {product} in {year}?").strip()
    correct = _ensure_dollar_prefix((row.get("correct_price") or "").strip())
    choices = [correct] + [_ensure_dollar_prefix((row.get(k) or "").strip())
                            for k in ("wrong1", "wrong2", "wrong3")]
    choices = [c for c in choices if c]
    random.shuffle(choices)
    correct_index = choices.index(correct)

    return {
        "headline": f"PRICE GAME: {product}"[:40],
        "full": question[:200],
        "question": question[:100],
        "choices": [c[:24] for c in choices],
        "correct_index": correct_index,
        "correction": "",
        "release_year": None,
        "category": "price_game",
        "product": product[:40],
        "short_name": short_name,
        "year": year,
        "ts": time.time(),
    }
