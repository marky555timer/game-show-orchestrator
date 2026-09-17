"""drivers/light_prefs_engine.py
Per-track memory for the operator's manual DJ-mode lightshow tuning (tap
tempo, uplight color, uplight pattern -- independently per fixture type
since 2026-09-17, see Btn9/handle_feature_select in inputs/gamepad.py).
inputs/gamepad.py's handle_tempo_tap()/handle_color_cycle()/
handle_theme_cycle() call mark_dirty() on every manual adjustment;
update(), polled every frame, waits config.LIGHT_PREFS_SAVE_DEBOUNCE_SECONDS
with no further adjustment before actually persisting -- a burst of
tap-tempo presses or a few quick Btn7/Btn8 taps writes to disk once, not on
every single press.

drivers/factoid_engine.py::ensure_prefetch() applies a track's saved prefs
(if any) the moment that track is confidently re-identified, so a song the
operator already dialed in a look for starts that way again instead of
just carrying over whatever the previous track happened to leave
state.dj_tempo_period/dmx_color_index/marquee_color_index/etc. at.

CSV (config.LIGHT_PREFS_CACHE_PATH), one row per track_key
(drivers/factoid_engine.py::_sanitize_track_key's "artist - title" format,
the same per-track identity already used for question caching and Price
Game), hand-editable like announcement_text.csv.

Schema migrated 2026-09-17 from one shared color_index/theme_index pair to
three independent per-fixture-type pairs (dmx_*/marquee_*/accent_*). The
legacy color_index/theme_index columns are kept (mirroring the DMX pair on
write) rather than dropped, both so older rows on disk still load
correctly and so anything still reading just those two columns keeps
seeing something sensible."""
import csv
import os
import time
import threading

import config
from state import state

_HEADER = ["track_key", "tempo_period", "color_index", "theme_index",
           "dmx_color_index", "dmx_theme_index",
           "marquee_color_index", "marquee_theme_index",
           "accent_color_index", "accent_theme_index",
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
                        # Same tolerance covers the 2026-09-17 per-fixture-
                        # type columns: a pre-migration row (or one from a
                        # partially-migrated file) just has them default to
                        # the legacy shared color_index/theme_index below --
                        # migration-on-read, no one-time file rewrite needed.
                        legacy_color = _to_int(row.get("color_index"))
                        legacy_theme = _to_int(row.get("theme_index"))
                        entries[key] = {
                            "tempo_period": tempo_period,
                            "color_index": legacy_color,
                            "theme_index": legacy_theme,
                            "dmx_color_index": _to_int(row.get("dmx_color_index"), legacy_color),
                            "dmx_theme_index": _to_int(row.get("dmx_theme_index"), legacy_theme),
                            "marquee_color_index": _to_int(row.get("marquee_color_index"), legacy_color),
                            "marquee_theme_index": _to_int(row.get("marquee_theme_index"), legacy_theme),
                            "accent_color_index": _to_int(row.get("accent_color_index"), legacy_color),
                            "accent_theme_index": _to_int(row.get("accent_theme_index"), legacy_theme),
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
                    # Legacy color_index/theme_index mirror the DMX pair
                    # specifically on write -- DMX is the fixture type the
                    # old shared field always drove, so anything still
                    # reading just these two columns keeps seeing something
                    # sensible rather than a frozen/stale value.
                    writer.writerow({
                        "track_key": key,
                        "tempo_period": prefs["tempo_period"],
                        "color_index": prefs.get("dmx_color_index", NO_LOOK),
                        "theme_index": prefs.get("dmx_theme_index", NO_LOOK),
                        "dmx_color_index": prefs.get("dmx_color_index", NO_LOOK),
                        "dmx_theme_index": prefs.get("dmx_theme_index", NO_LOOK),
                        "marquee_color_index": prefs.get("marquee_color_index", NO_LOOK),
                        "marquee_theme_index": prefs.get("marquee_theme_index", NO_LOOK),
                        "accent_color_index": prefs.get("accent_color_index", NO_LOOK),
                        "accent_theme_index": prefs.get("accent_theme_index", NO_LOOK),
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
                "dmx_color_index": NO_LOOK,
                "dmx_theme_index": NO_LOOK,
                "marquee_color_index": NO_LOOK,
                "marquee_theme_index": NO_LOOK,
                "accent_color_index": NO_LOOK,
                "accent_theme_index": NO_LOOK,
                "tempo_source": "online",
                "energy": "",
            })
            entry.update(fields)
            self._entries[track_key] = entry
        self._save_cache()

    def save_prefs_for(self, track_key, tempo_period,
                        dmx_color_index, dmx_theme_index,
                        marquee_color_index, marquee_theme_index,
                        accent_color_index, accent_theme_index):
        self._upsert(track_key, tempo_period=tempo_period,
                     # Legacy pair mirrors DMX -- see _save_cache()'s comment.
                     color_index=dmx_color_index, theme_index=dmx_theme_index,
                     dmx_color_index=dmx_color_index, dmx_theme_index=dmx_theme_index,
                     marquee_color_index=marquee_color_index, marquee_theme_index=marquee_theme_index,
                     accent_color_index=accent_color_index, accent_theme_index=accent_theme_index,
                     tempo_source="operator")
        print(f"[LIGHT PREFS] Saved for {track_key!r}: tempo={tempo_period:.3f}s, "
              f"dmx=({dmx_color_index},{dmx_theme_index}), "
              f"marquee=({marquee_color_index},{marquee_theme_index}), "
              f"accent=({accent_color_index},{accent_theme_index})")

    def save_fixture_look_for(self, track_key, **fields):
        """Partial per-fixture-type look save from the library's per-song
        editor (web/remote_server.py's /api/library/light-prefs) -- accepts
        any subset of dmx_color_index/dmx_theme_index/marquee_color_index/
        marquee_theme_index/accent_color_index/accent_theme_index, merged
        via _upsert() same as every other write path here (editing just the
        marquee fields doesn't touch DMX/accent/tempo/energy). Editing a
        song that isn't the one currently playing only touches this row on
        disk -- apply_prefs_for() is what actually pushes a saved look into
        live state, and that only fires when the track becomes current.

        Also mirrors a dmx_* field into the legacy color_index/theme_index
        columns if present, matching save_prefs_for()'s own convention (see
        _save_cache()'s comment on why the legacy pair mirrors DMX)."""
        if not track_key or not fields:
            return
        if "dmx_color_index" in fields:
            fields.setdefault("color_index", fields["dmx_color_index"])
        if "dmx_theme_index" in fields:
            fields.setdefault("theme_index", fields["dmx_theme_index"])
        self._upsert(track_key, **fields)
        print(f"[LIGHT PREFS] Library edit saved for {track_key!r}: {fields}")

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
        light_prefs_engine.save_prefs_for(
            key, state.dj_tempo_period,
            state.dmx_color_index, state.dmx_theme_index,
            state.marquee_color_index, state.marquee_theme_index,
            state.accent_color_index, state.accent_theme_index,
        )


def save_online_tempo(track_key, tempo_period):
    """Module-level wrapper, matching mark_dirty()/apply_prefs_for()'s shape
    so callers don't reach through to the singleton."""
    return light_prefs_engine.save_online_tempo_for(track_key, tempo_period)


def save_fixture_look(track_key, **fields):
    """Module-level wrapper, same convention as save_online_tempo()."""
    light_prefs_engine.save_fixture_look_for(track_key, **fields)


def get_prefs_for(track_key):
    """Module-level wrapper, same convention as save_online_tempo()."""
    return light_prefs_engine.get_prefs_for(track_key)


def save_energy(track_key, energy):
    """Module-level wrapper, same convention as save_online_tempo()."""
    light_prefs_engine.save_energy_for(track_key, energy)


def get_energy_for(track_key):
    """Module-level wrapper, same convention as save_online_tempo()."""
    return light_prefs_engine.get_energy_for(track_key)


def apply_prefs_for(track_key):
    """Called the moment a track becomes the confidently-identified current
    track (drivers/factoid_engine.py::ensure_prefetch()): restores a
    previously-tuned tempo, and independently restores each of DMX/marquee/
    accent's saved color+pattern for a track that's played before (this
    session or an earlier one), overriding whatever the BPM-tag auto-set or
    the previous track's cycling left state.dj_tempo_period/dmx_*/
    marquee_*/accent_* at.

    Each fixture type restores independently -- a row saved before the
    2026-09-17 per-fixture-type migration has all three inherited from the
    old shared look (see _load_cache()'s migration-on-read defaulting) and
    so restores identically on all three; a row saved after where the
    operator only touched, say, the marquee color restores marquee alone,
    leaving DMX/accent to their normal per-song resolution.

    Restoring DMX specifically also cancels the song transition's pending
    random DMX color pick (drivers/lighting_engine.py::note_look_recalled,
    drivers/lighting_engine.py::_pick_implied_look) -- deliberately DMX-only
    since that pending-pick mechanism only ever exists for DMX; calling it
    on a marquee/accent-only restore would cancel DMX's own resolution
    without ever actually giving DMX a look. A track with every pair at
    NO_LOOK carries a tempo only -- typically auto-cached from the online
    BPM lookup -- and deliberately leaves all three looks alone.

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

    restored_any = False

    dmx_color = prefs.get("dmx_color_index", NO_LOOK)
    dmx_theme = prefs.get("dmx_theme_index", NO_LOOK)
    if dmx_color >= 0 and dmx_theme >= 0:
        state.dmx_color_index = dmx_color % len(config.DJ_COLOR_PALETTE)
        state.dmx_theme_index = dmx_theme % config.DJ_THEME_COUNT
        lighting_engine.note_look_recalled()
        restored_any = True
        print(f"[LIGHT PREFS] Restored DMX for {track_key!r}: "
              f"color={state.dmx_color_index}, theme={state.dmx_theme_index}")

    marquee_color = prefs.get("marquee_color_index", NO_LOOK)
    marquee_theme = prefs.get("marquee_theme_index", NO_LOOK)
    if marquee_color >= 0 and marquee_theme >= 0:
        state.marquee_color_index = marquee_color % len(config.DJ_COLOR_PALETTE)
        state.marquee_theme_index = marquee_theme % len(config.MARQUEE_THEME_NAMES)
        restored_any = True
        print(f"[LIGHT PREFS] Restored marquee for {track_key!r}: "
              f"color={state.marquee_color_index}, theme={state.marquee_theme_index}")

    accent_color = prefs.get("accent_color_index", NO_LOOK)
    accent_theme = prefs.get("accent_theme_index", NO_LOOK)
    if accent_color >= 0 and accent_theme >= 0:
        state.accent_color_index = accent_color % len(config.DJ_COLOR_PALETTE)
        state.accent_theme_index = accent_theme % len(config.ACCENT_THEME_TO_FX)
        restored_any = True
        print(f"[LIGHT PREFS] Restored accent for {track_key!r}: "
              f"color={state.accent_color_index}, theme={state.accent_theme_index}")

    if not restored_any:
        print(f"[LIGHT PREFS] Restored tempo only for {track_key!r}: "
              f"{state.dj_tempo_period:.3f}s (no saved look -- color/pattern stay random)")
