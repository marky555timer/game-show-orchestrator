"""drivers/music_metadata_engine.py
Per-track show-curation metadata (2026-08-12 planning): content/mood tags
plus a few descriptive attributes, used to build audience-appropriate event
profiles (Corporate, Wedding, Bar Night, Kids Birthday, Ladies Night, ...)
that filter drivers/deck_orchestrator.py's next-track candidate pool
(Phase 3, not yet wired). CSV (config.MUSIC_METADATA_PATH), one row per
track_key, hand-editable like light_prefs.csv/announcement_text.csv.

Two field families, deliberately modeled differently:
  - Boolean content tags (explicit/slow_dance/never_charted) -- exclusion
    filters an event profile checks ("no explicit lyrics at the corporate
    gig"). slow_dance means specifically a romantic ballad meant for a
    couple's slow dance -- NOT the same concept as light_prefs.csv's
    "energy" column (a lighting-color classification), even though both use
    the word "slow".
  - Descriptive attributes (decade/genre/energy_rank) -- typed values for
    browsing/sorting, and finer profile constraints later
    ("energy_rank >= 3", "genre == Children's"). energy_rank is a 1-5
    crowd-intensity/floor-filler scale, distinct from light_prefs.csv's
    "energy" field too.

The "Tag Library" button (web/remote_server.py) runs a background job:
first a free, no-AI decade backfill from track_cache.json's already-cached
release_year (drivers/factoid_engine.py's trivia pipeline fetches this for
almost every track anyway), then a batched Haiku pass
(config.MUSIC_TAG_BATCH_SIZE tracks/call) over whatever's still incomplete.
Only ever processes tracks that need it -- see tracks_needing_tagging()."""
import csv
import json
import os
import re
import threading
import time

import config
from drivers.music_library import music_library, sanitize_track_key

_HEADER = ["track_key", "explicit", "slow_dance", "never_charted",
           "decade", "genre", "energy_rank", "tagged_at"]

# The 3 boolean content tags -- factored out so completeness checks and
# CSV (de)serialization don't repeat this list three different ways.
_BOOL_FIELDS = ("explicit", "slow_dance", "never_charted")

_DECADE_RE = re.compile(r"^\d{4}s$")  # e.g. "1980s" (4 digits + "s")


def _bool_to_csv(value):
    if value is True:
        return "true"
    if value is False:
        return "false"
    return ""


def _bool_from_csv(raw):
    raw = (raw or "").strip().lower()
    if raw == "true":
        return True
    if raw == "false":
        return False
    return None


class MusicMetadataEngine:
    def __init__(self):
        self._lock = threading.Lock()
        self._entries = self._load_cache()

    def _load_cache(self):
        entries = {}
        try:
            if os.path.exists(config.MUSIC_METADATA_PATH):
                with open(config.MUSIC_METADATA_PATH, "r", encoding="utf-8", newline="") as f:
                    for row in csv.DictReader(f):
                        key = (row.get("track_key") or "").strip()
                        if not key:
                            continue
                        entry = {field: _bool_from_csv(row.get(field)) for field in _BOOL_FIELDS}
                        entry["decade"] = (row.get("decade") or "").strip()
                        entry["genre"] = (row.get("genre") or "").strip()
                        rank = (row.get("energy_rank") or "").strip()
                        entry["energy_rank"] = int(rank) if rank.isdigit() else None
                        entry["tagged_at"] = (row.get("tagged_at") or "").strip()
                        entries[key] = entry
        except Exception as e:
            print(f"[MUSIC METADATA] Failed to read cache, starting empty: {e}")
        return entries

    def _save_cache(self):
        try:
            os.makedirs(config.MUSIC_METADATA_DIR, exist_ok=True)
            with open(config.MUSIC_METADATA_PATH, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=_HEADER)
                writer.writeheader()
                for key, entry in sorted(self._entries.items()):
                    row = {field: _bool_to_csv(entry.get(field)) for field in _BOOL_FIELDS}
                    row["track_key"] = key
                    row["decade"] = entry.get("decade") or ""
                    row["genre"] = entry.get("genre") or ""
                    row["energy_rank"] = entry.get("energy_rank") or ""
                    row["tagged_at"] = entry.get("tagged_at") or ""
                    writer.writerow(row)
        except Exception as e:
            print(f"[MUSIC METADATA] Failed to save cache: {e}")

    def get(self, track_key):
        with self._lock:
            entry = self._entries.get(track_key)
            return dict(entry) if entry else None

    def is_complete(self, entry):
        if entry is None:
            return False
        if any(entry.get(f) is None for f in _BOOL_FIELDS):
            return False
        if not entry.get("decade") or not entry.get("genre"):
            return False
        if entry.get("energy_rank") is None:
            return False
        return True

    def save(self, track_key, **fields):
        """Merges `fields` into whatever entry exists for track_key (or a
        blank template), leaving fields NOT passed untouched -- an operator
        correcting just the genre in the review UI must not blow away the
        AI's other answers for that track. Stamps tagged_at the moment the
        entry becomes fully complete (not before), so tracks_needing_tagging()
        stays accurate -- a partially-backfilled entry (decade only, from
        the cache pass) is still "needs tagging" until the rest lands."""
        if not track_key:
            return
        with self._lock:
            entry = dict(self._entries.get(track_key) or {
                "explicit": None, "slow_dance": None, "never_charted": None,
                "decade": "", "genre": "", "energy_rank": None, "tagged_at": "",
            })
            entry.update(fields)
            if self.is_complete(entry) and not entry.get("tagged_at"):
                entry["tagged_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._entries[track_key] = entry
        self._save_cache()

    def all_entries(self):
        with self._lock:
            return dict(self._entries)


music_metadata_engine = MusicMetadataEngine()


def get_metadata_for(track_key):
    return music_metadata_engine.get(track_key)


def save_metadata_for(track_key, **fields):
    music_metadata_engine.save(track_key, **fields)


def is_complete(entry):
    """Module-level wrapper, same convention as get/save_metadata_for --
    lets callers avoid reaching through music_metadata_engine.music_metadata_engine."""
    return music_metadata_engine.is_complete(entry)


def tracks_needing_tagging():
    """Every library track with no metadata row yet, or an incomplete one --
    what the Tag Library pass (and the decade backfill) actually process.
    Returns [{"track_key", "title", "artist"}, ...]."""
    out = []
    for t in music_library.all_tracks():
        key = sanitize_track_key(t["title"], t["artist"])
        entry = music_metadata_engine.get(key)
        if not music_metadata_engine.is_complete(entry):
            out.append({"track_key": key, "title": t["title"], "artist": t["artist"]})
    return out


# ------------------------------------------------------------
# Decade backfill: free, no AI call. drivers/factoid_engine.py's trivia
# pipeline already asks the AI for "release_year" on nearly every track it
# ever generates a question for (background prefetch runs continuously, no
# button needed) and caches it in track_cache.json -- reusing that spares
# the Tag Library pass from re-deriving a fact that's very likely already
# sitting on disk.
# ------------------------------------------------------------
def _decade_label(year):
    try:
        year = int(year)
    except (TypeError, ValueError):
        return None
    if not (1900 <= year <= 2100):
        return None
    return f"{(year // 10) * 10}s"


def _release_year_from_track_cache(track_key, track_cache):
    for question in track_cache.get(track_key, []):
        year = question.get("release_year")
        if year:
            return year
    return None


def backfill_decades_from_cache():
    """Fills in `decade` (only) for any track missing it, from whatever
    release_year track_cache.json already has cached -- does NOT touch
    explicit/slow_dance/never_charted/genre/energy_rank, and does NOT stamp
    tagged_at (the entry is still incomplete until the AI pass fills the
    rest). Safe/cheap to call every time a tagging job starts. Returns how
    many tracks got a decade filled in."""
    try:
        with open(config.TRACK_CACHE_PATH, "r", encoding="utf-8") as f:
            track_cache = json.load(f)
    except Exception as e:
        print(f"[MUSIC METADATA] Could not read {config.TRACK_CACHE_PATH} for decade backfill: {e}")
        return 0

    filled = 0
    for pending in tracks_needing_tagging():
        key = pending["track_key"]
        entry = music_metadata_engine.get(key)
        if entry and entry.get("decade"):
            continue
        year = _release_year_from_track_cache(key, track_cache)
        decade = _decade_label(year)
        if decade:
            music_metadata_engine.save(key, decade=decade)
            filled += 1
    if filled:
        print(f"[MUSIC METADATA] Backfilled decade for {filled} track(s) from track_cache.json (no AI call).")
    return filled


# ------------------------------------------------------------
# Batched AI tagging pass
# ------------------------------------------------------------
def _build_tag_prompt(batch):
    lines = []
    for i, item in enumerate(batch, start=1):
        key = item["track_key"]
        known_decade = (music_metadata_engine.get(key) or {}).get("decade")
        hint = f" [already known: released in the {known_decade}]" if known_decade else ""
        lines.append(f'{i}. "{item["title"]}" by "{item["artist"]}"{hint}')
    tracks_block = "\n".join(lines)
    genre_list = ", ".join(config.MUSIC_GENRES)

    return (
        "You are tagging tracks in a DJ's music library for audience-appropriate "
        "live-event show curation. For each NUMBERED track below, reply with ONLY "
        "a single-line JSON array (no markdown fences, no commentary) of objects "
        "IN THE SAME ORDER as the tracks, one object per track, each with exactly "
        "these keys:\n"
        '"explicit" (true/false -- does this track have explicit/adult lyrical '
        "content: profanity, sexual content, or similarly adult themes?), "
        '"slow_dance" (true/false -- is this SPECIFICALLY a slow, romantic ballad '
        "meant for a couple's slow dance, not just any calm/chill song?), "
        '"never_charted" (true/false -- your best guess: is this an obscure/deep-cut '
        "track that likely never charted on the Billboard Hot 100 or your region's "
        "equivalent, as opposed to a well-known hit?), "
        '"decade" (the decade this track was originally released, formatted exactly '
        'like "1980s" -- empty string "" only if you genuinely don\'t know; if a '
        "track's hint above already states its decade, just echo that back), "
        f'"genre" (exactly one of: {genre_list} -- pick whichever fits best even if '
        "imperfect, never invent a category not in that list), "
        '"energy_rank" (an integer 1-5 for how much this track energizes/fills a '
        "dance floor at a live event -- 1 = quiet background/mellow, 5 = an all-out "
        "floor-filler banger).\n\n"
        f"Tracks:\n{tracks_block}"
    )


def _coerce_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "yes", "1"):
            return True
        if v in ("false", "no", "0"):
            return False
    return None


def _coerce_energy_rank(value):
    try:
        rank = int(value)
    except (TypeError, ValueError):
        return None
    if config.MUSIC_ENERGY_RANK_MIN <= rank <= config.MUSIC_ENERGY_RANK_MAX:
        return rank
    return None


def _parse_tag_response(text, expected_count):
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, list) or len(data) != expected_count:
        return None
    return data


def _tag_batch(batch):
    """One Haiku call for up to config.MUSIC_TAG_BATCH_SIZE tracks. All-or-
    nothing per batch -- a malformed/short response saves nothing for this
    batch (every track in it stays incomplete and gets retried on the next
    Tag Library run) rather than risk silently mismatching answers to the
    wrong tracks. Returns how many tracks were successfully saved."""
    from drivers.factoid_engine import call_haiku  # lazy: avoid import-order coupling at module load

    prompt = _build_tag_prompt(batch)
    text, reason = call_haiku(prompt, max_tokens=200 + 120 * len(batch))
    if text is None:
        print(f"[MUSIC METADATA] Tag batch of {len(batch)} failed: {reason}")
        return 0

    parsed = _parse_tag_response(text, len(batch))
    if parsed is None:
        print(f"[MUSIC METADATA] Tag batch of {len(batch)} returned an unparseable/"
              f"mismatched response -- skipping this batch, will retry next run.")
        return 0

    saved = 0
    for item, obj in zip(batch, parsed):
        if not isinstance(obj, dict):
            continue
        key = item["track_key"]
        existing = music_metadata_engine.get(key) or {}
        fields = {
            "explicit": _coerce_bool(obj.get("explicit")),
            "slow_dance": _coerce_bool(obj.get("slow_dance")),
            "never_charted": _coerce_bool(obj.get("never_charted")),
            "genre": obj.get("genre") if obj.get("genre") in config.MUSIC_GENRES else existing.get("genre") or "",
            "energy_rank": _coerce_energy_rank(obj.get("energy_rank")),
        }
        # decade: an already-known value (backfilled from track_cache.json,
        # a verified fact from the trivia pipeline) always wins over the
        # AI's guess in this same call. Only accept the AI's answer when
        # there wasn't one already, and only if it's actually decade-shaped.
        if existing.get("decade"):
            fields["decade"] = existing["decade"]
        else:
            ai_decade = str(obj.get("decade") or "").strip()
            fields["decade"] = ai_decade if _DECADE_RE.match(ai_decade) else ""
        music_metadata_engine.save(key, **fields)
        saved += 1
    return saved


def _chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


# ------------------------------------------------------------
# Background job (web/remote_server.py's Tag Library button)
# ------------------------------------------------------------
_job_lock = threading.Lock()
_job_state = {"running": False, "total": 0, "done": 0, "last_error": ""}


def tagging_job_status():
    with _job_lock:
        return dict(_job_state)


def start_tagging_job():
    """Returns False (no-op) if a job is already running -- the button on
    the client is expected to disable itself while running, this is just
    the server-side guard against a duplicate trigger."""
    with _job_lock:
        if _job_state["running"]:
            return False
        _job_state.update({"running": True, "total": 0, "done": 0, "last_error": ""})
    threading.Thread(target=_run_tagging_job, daemon=True).start()
    return True


def _run_tagging_job():
    try:
        backfill_decades_from_cache()
        pending = tracks_needing_tagging()
        with _job_lock:
            _job_state["total"] = len(pending)

        if not pending:
            return
        if not config.ANTHROPIC_API_KEY:
            with _job_lock:
                _job_state["last_error"] = (
                    "AI DISABLED (no anthropic_key.txt) -- decade backfill ran, "
                    "but explicit/slow_dance/never_charted/genre/energy_rank need "
                    "a real API key to auto-tag."
                )
            return

        for batch in _chunks(pending, config.MUSIC_TAG_BATCH_SIZE):
            _tag_batch(batch)
            with _job_lock:
                _job_state["done"] += len(batch)
    except Exception as e:
        with _job_lock:
            _job_state["last_error"] = str(e)
        print(f"[MUSIC METADATA] Tagging job crashed: {e}")
    finally:
        with _job_lock:
            _job_state["running"] = False
