"""drivers/light_prefs_engine.py
Per-track memory for the operator's manual DJ-mode lightshow tuning (tap
tempo, uplight color, uplight pattern). inputs/gamepad.py's
handle_tempo_tap()/handle_color_cycle()/handle_theme_cycle() call
mark_dirty() on every manual adjustment; update(), polled every frame,
waits config.LIGHT_PREFS_SAVE_DEBOUNCE_SECONDS with no further adjustment
before actually persisting -- a burst of tap-tempo presses or a few quick
Btn7/Btn8 taps writes to disk once, not on every single press.

drivers/factoid_engine.py::ensure_prefetch() applies a track's saved prefs
(if any) the moment that track is confidently re-identified, so a song the
operator already dialed in a look for starts that way again instead of
just carrying over whatever the previous track happened to leave
state.dj_tempo_period/dj_color_index/dj_theme_index at.

CSV (config.LIGHT_PREFS_CACHE_PATH), one row per track_key
(drivers/factoid_engine.py::_sanitize_track_key's "artist - title" format,
the same per-track identity already used for question caching and Price
Game), hand-editable like announcement_text.csv."""
import csv
import os
import time
import threading

import config
from state import state

_HEADER = ["track_key", "tempo_period", "color_index", "theme_index",
           "tempo_source", "energy", "updated_at"]

# Valid values for the "energy" column -- drivers/factoid_engine.py fetches
# this from the AI alongside bpm/release_year and caches it here via
# save_energy_for(). Empty string ("" in the CSV, None from get_energy_for)
# means not yet classified.
VALID_ENERGIES = ("slow", "fast", "dark")

# color_index/theme_index sentinel meaning "no operator-chosen look for this
# track". Rows can exist for tempo alone -- an online BPM lookup caches one
# (see save_online_tempo_for) -- and those must NOT pin a color/pattern, or
# the random per-song look would get frozen to whatever happened to be up
# when the BPM answer landed.
NO_LOOK = -1


def _to_int(raw, default=NO_LOOK):
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


class LightPrefsEngine:
    def __init__(self):
        self._lock = threading.Lock()
        self._entries = self._load_cache()

    def _load_cache(self):
        entries = {}
        try:
            if os.path.exists(config.LIGHT_PREFS_CACHE_PATH):
                with open(config.LIGHT_PREFS_CACHE_PATH, "r", encoding="utf-8", newline="") as f:
                    for row in csv.DictReader(f):
                        key = (row.get("track_key") or "").strip()
                        if not key:
                            continue
                        try:
                            tempo_period = float(row["tempo_period"])
                        except (KeyError, TypeError, ValueError):
                            continue
                        # .get()/tolerant parsing rather than indexing: rows
                        # written before tempo_source/energy existed are
                        # still valid and must keep loading (they're all
                        # operator saves, and simply have no energy tag yet).
                        entries[key] = {
                            "tempo_period": tempo_period,
                            "color_index": _to_int(row.get("color_index")),
                            "theme_index": _to_int(row.get("theme_index")),
                            "tempo_source": (row.get("tempo_source") or "operator").strip(),
                            "energy": (row.get("energy") or "").strip(),
                        }
        except Exception as e:
            print(f"[LIGHT PREFS] Failed to read cache, starting empty: {e}")
        return entries

    def _save_cache(self):
        try:
            os.makedirs(config.LIGHT_PREFS_DIR, exist_ok=True)
            with open(config.LIGHT_PREFS_CACHE_PATH, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=_HEADER)
                writer.writeheader()
                now_str = time.strftime("%Y-%m-%d %H:%M:%S")
                for key, prefs in sorted(self._entries.items()):
                    writer.writerow({
                        "track_key": key,
                        "tempo_period": prefs["tempo_period"],
                        "color_index": prefs["color_index"],
                        "theme_index": prefs["theme_index"],
                        "tempo_source": prefs.get("tempo_source", "operator"),
                        "energy": prefs.get("energy", ""),
                        "updated_at": now_str,
                    })
        except Exception as e:
            print(f"[LIGHT PREFS] Failed to save cache: {e}")

    def get_prefs_for(self, track_key):
        with self._lock:
            return self._entries.get(track_key)

    def get_energy_for(self, track_key):
        with self._lock:
            entry = self._entries.get(track_key)
        return (entry or {}).get("energy") or None

    def _upsert(self, track_key, **fields):
        """Merges `fields` into whatever entry exists for track_key (or a
        blank template if there isn't one yet), leaving every field NOT
        passed untouched, then persists. Shared by every write path below
        so a save from one source (operator tap/cycle, online BPM lookup,
        AI energy classification) never clobbers a field owned by another
        -- e.g. an operator re-saving tempo/color/pattern must not erase
        this track's already-cached energy tag."""
        if not track_key:
            return
        with self._lock:
            entry = dict(self._entries.get(track_key) or {
                "tempo_period": config.TEMPO_DEFAULT_PERIOD_SECONDS,
                "color_index": NO_LOOK,
                "theme_index": NO_LOOK,
                "tempo_source": "online",
                "energy": "",
            })
            entry.update(fields)
            self._entries[track_key] = entry
        self._save_cache()

    def save_prefs_for(self, track_key, tempo_period, color_index, theme_index):
        self._upsert(track_key, tempo_period=tempo_period, color_index=color_index,
                     theme_index=theme_index, tempo_source="operator")
        print(f"[LIGHT PREFS] Saved for {track_key!r}: tempo={tempo_period:.3f}s, "
              f"color={color_index}, theme={theme_index}")

    def save_online_tempo_for(self, track_key, tempo_period):
        """Persists a BPM that came back from the online (AI) lookup. Writes
        a tempo-only row (color/theme = NO_LOOK) for a track with nothing
        saved, so the per-song random look stays random; if the operator
        already has a row here, their tempo wins and this is dropped."""
        if not track_key:
            return False
        with self._lock:
            existing = self._entries.get(track_key)
            if existing and existing.get("tempo_source", "operator") == "operator":
                return False
        self._upsert(track_key, tempo_period=tempo_period, tempo_source="online")
        print(f"[LIGHT PREFS] Cached online tempo for {track_key!r}: {tempo_period:.3f}s "
              f"({60.0 / tempo_period:.0f} BPM)")
        return True

    def save_energy_for(self, track_key, energy):
        """Persists the track's AI-classified energy ("slow"/"fast"/"dark"),
        consumed by drivers/lighting_engine.py::_pick_implied_look() for any
        track with no operator-saved color/pattern. Independent of
        tempo_source/color/theme -- classifying a track's energy doesn't
        touch its tempo or look, and an operator saving a look doesn't
        erase its energy tag. A no-op if already cached with this exact
        value, so re-fetching it (each of a track's up-to-3 prefetched
        questions asks the AI for it again) doesn't rewrite the file
        needlessly."""
        if not track_key or energy not in VALID_ENERGIES:
            return
        with self._lock:
            existing = self._entries.get(track_key)
            if existing and existing.get("energy") == energy:
                return
        self._upsert(track_key, energy=energy)
        print(f"[LIGHT PREFS] Cached energy for {track_key!r}: {energy}")


light_prefs_engine = LightPrefsEngine()

# Debounce state: the track key + timestamp of the most recent manual
# tempo/color/theme adjustment. None while nothing's pending.
_dirty_track_key = None
_dirty_since = None


def mark_dirty():
    """Called on every manual tap-tempo/color-cycle/theme-cycle adjustment.
    Debounced -- update() actually persists once
    config.LIGHT_PREFS_SAVE_DEBOUNCE_SECONDS pass with no further
    adjustment. No-op outside DJ mode or before a track's been confidently
    identified -- there's nothing to attach the preference to yet."""
    global _dirty_track_key, _dirty_since
    if state.mode != state.MODE_DJ or not state.factoid_track_key:
        return
    _dirty_track_key = state.factoid_track_key
    _dirty_since = time.time()


def update(now):
    """Per-frame poll (inputs/gamepad.py::process_events()): once the
    debounce window passes with no further adjustment, save the CURRENT
    tempo/color/theme -- not necessarily whatever mark_dirty() was called
    with, since the operator may have kept adjusting other controls in the
    meantime and every adjustment should land in one combined snapshot."""
    global _dirty_track_key, _dirty_since
    if _dirty_since is None:
        return
    if now - _dirty_since < config.LIGHT_PREFS_SAVE_DEBOUNCE_SECONDS:
        return
    key, _dirty_track_key = _dirty_track_key, None
    _dirty_since = None
    if key:
        light_prefs_engine.save_prefs_for(key, state.dj_tempo_period, state.dj_color_index, state.dj_theme_index)


def save_online_tempo(track_key, tempo_period):
    """Module-level wrapper, matching mark_dirty()/apply_prefs_for()'s shape
    so callers don't reach through to the singleton."""
    return light_prefs_engine.save_online_tempo_for(track_key, tempo_period)


def save_energy(track_key, energy):
    """Module-level wrapper, same convention as save_online_tempo()."""
    light_prefs_engine.save_energy_for(track_key, energy)


def get_energy_for(track_key):
    """Module-level wrapper, same convention as save_online_tempo()."""
    return light_prefs_engine.get_energy_for(track_key)


def apply_prefs_for(track_key):
    """Called the moment a track becomes the confidently-identified current
    track (drivers/factoid_engine.py::ensure_prefetch()): restores a
    previously-tuned tempo/color/pattern for a track that's played before
    (this session or an earlier one), overriding whatever the BPM-tag
    auto-set or the previous track's cycling left state.dj_tempo_period/
    dj_color_index/dj_theme_index at.

    Restoring a look also cancels the song transition's pending random
    color pick (drivers/lighting_engine.py::note_look_recalled), so a
    remembered track fades straight back up into its own look instead of
    flashing a random one first. A row with color/theme = NO_LOOK carries a
    tempo only -- typically auto-cached from the online BPM lookup -- and
    deliberately leaves the look alone.

    Lazy import of lighting_engine: drivers/factoid_engine.py imports this
    module at its own top level, and keeping the graphics/DMX side out of
    this module's import chain avoids tangling them."""
    from drivers import lighting_engine

    prefs = light_prefs_engine.get_prefs_for(track_key)
    if not prefs:
        return
    state.dj_tempo_period = max(config.TEMPO_PERIOD_MIN_SECONDS,
                                 min(config.TEMPO_PERIOD_MAX_SECONDS, prefs["tempo_period"]))
    # An operator-saved tempo outranks any online BPM that lands later.
    if prefs.get("tempo_source", "operator") == "operator":
        state.tempo_operator_set = True

    color_index = prefs.get("color_index", NO_LOOK)
    theme_index = prefs.get("theme_index", NO_LOOK)
    if color_index >= 0 and theme_index >= 0:
        state.dj_color_index = color_index % len(config.DJ_COLOR_PALETTE)
        state.dj_theme_index = theme_index % config.DJ_THEME_COUNT
        lighting_engine.note_look_recalled()
        print(f"[LIGHT PREFS] Restored for {track_key!r}: tempo={state.dj_tempo_period:.3f}s, "
              f"color={state.dj_color_index}, theme={state.dj_theme_index}")
    else:
        print(f"[LIGHT PREFS] Restored tempo only for {track_key!r}: "
              f"{state.dj_tempo_period:.3f}s (no saved look -- color/pattern stay random)")
