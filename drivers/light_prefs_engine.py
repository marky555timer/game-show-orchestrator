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
           "accent_preset_id", "accent_preset_color_index",
           "accent_palette_index",
           "accent_speed", "accent_brightness", "show_lower_marquees",
           "accent_fx_rgb", "accent_bg_rgb",
           "tempo_source", "energy",
           "marquee_brightness", "marquee_speed", "marquee_depth", "marquee_off",
           "dmx_speed_div", "panel_leds_off", "idle_theme",
           "updated_at"]

# Append-only change log (config.LIGHT_PREFS_LOG_PATH, added 2026-09-28): one
# row per field that actually changed on a write, timestamped, so usage
# patterns over time can be analysed -- light_prefs.csv itself is only the
# latest state per song and its updated_at is re-stamped on every save.
# Bookkeeping/legacy columns are excluded (the legacy pair just mirrors DMX).
_LOG_HEADER = ["timestamp", "track_key", "source", "field", "old", "new"]
_LOG_SKIP_FIELDS = ("color_index", "theme_index", "tempo_source")

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
                            # 2026-09-25 columns -- no legacy fallback, a
                            # row without them simply has no preset.
                            "accent_preset_id": _to_int(row.get("accent_preset_id")),
                            "accent_preset_color_index": _to_int(row.get("accent_preset_color_index")),
                            # 2026-09-26 -- same "no legacy fallback" as the preset pair above.
                            "accent_palette_index": _to_int(row.get("accent_palette_index")),
                            # 2026-09-28 -- per-song outline speed/brightness; NO_LOOK = none saved.
                            "accent_speed": _to_int(row.get("accent_speed")),
                            "accent_brightness": _to_int(row.get("accent_brightness")),
                            # 2026-10-02 -- 1 = this song shows the lower marquees (panels 3-6);
                            # NO_LOOK (the default) = blanked, like every song.
                            "show_lower_marquees": _to_int(row.get("show_lower_marquees")),
                            # 2026-10-02 -- outline WLED slot 1 (Fx) / slot 2 (Bg) as 0xRRGGBB; NO_LOOK = auto
                            "accent_fx_rgb": _to_int(row.get("accent_fx_rgb")),
                            "accent_bg_rgb": _to_int(row.get("accent_bg_rgb")),
                            "tempo_source": (row.get("tempo_source") or "operator").strip(),
                            "energy": (row.get("energy") or "").strip(),
                            # 2026-10-05 marquee dials (persisted since 2026-10-07); NO_LOOK = default.
                            **{k: _to_int(row.get(k)) for k in config.MARQUEE_DIAL_DEFAULTS},
                            # 2026-10-08 -- DMX pattern at 1/2 (2) or 1/4 (4) tempo; NO_LOOK = full.
                            "dmx_speed_div": _to_int(row.get("dmx_speed_div")),
                            # 2026-10-09 -- 1 = panel-button LED animation off for this song; NO_LOOK = on.
                            "panel_leds_off": _to_int(row.get("panel_leds_off")),
                            # 2026-10-11 -- panels 3-6 animation theme for this song
                            # ("" = automatic; see resolve_idle_theme).
                            "idle_theme": (row.get("idle_theme") or "").strip(),
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
                        "accent_preset_id": prefs.get("accent_preset_id", NO_LOOK),
                        "accent_preset_color_index": prefs.get("accent_preset_color_index", NO_LOOK),
                        "accent_palette_index": prefs.get("accent_palette_index", NO_LOOK),
                        "accent_speed": prefs.get("accent_speed", NO_LOOK),
                        "accent_brightness": prefs.get("accent_brightness", NO_LOOK),
                        "show_lower_marquees": prefs.get("show_lower_marquees", NO_LOOK),
                        "accent_fx_rgb": prefs.get("accent_fx_rgb", NO_LOOK),
                        "accent_bg_rgb": prefs.get("accent_bg_rgb", NO_LOOK),
                        "tempo_source": prefs.get("tempo_source", "operator"),
                        "energy": prefs.get("energy", ""),
                        **{k: prefs.get(k, NO_LOOK) for k in config.MARQUEE_DIAL_DEFAULTS},
                        "dmx_speed_div": prefs.get("dmx_speed_div", NO_LOOK),
                        "panel_leds_off": prefs.get("panel_leds_off", NO_LOOK),
                        "idle_theme": prefs.get("idle_theme", ""),
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

    def _upsert(self, track_key, _source="operator", **fields):
        """Merges `fields` into whatever entry exists for track_key (or a
        blank template if there isn't one yet), leaving every field NOT
        passed untouched, then persists. Shared by every write path below
        so a save from one source (operator tap/cycle, online BPM lookup,
        AI energy classification) never clobbers a field owned by another
        -- e.g. an operator re-saving tempo/color/pattern must not erase
        this track's already-cached energy tag.

        `_source` labels who made the change in the change log ("operator",
        "library", "online", "ai_energy")."""
        if not track_key:
            return
        with self._lock:
            existing = self._entries.get(track_key)
            entry = dict(existing or {
                "tempo_period": config.TEMPO_DEFAULT_PERIOD_SECONDS,
                "color_index": NO_LOOK,
                "theme_index": NO_LOOK,
                "dmx_color_index": NO_LOOK,
                "dmx_theme_index": NO_LOOK,
                "marquee_color_index": NO_LOOK,
                "marquee_theme_index": NO_LOOK,
                "accent_color_index": NO_LOOK,
                "accent_theme_index": NO_LOOK,
                "accent_preset_id": NO_LOOK,
                "accent_preset_color_index": NO_LOOK,
                "accent_palette_index": NO_LOOK,
                "accent_speed": NO_LOOK,
                "accent_brightness": NO_LOOK,
                "show_lower_marquees": NO_LOOK,
                "accent_fx_rgb": NO_LOOK,
                "accent_bg_rgb": NO_LOOK,
                **{k: NO_LOOK for k in config.MARQUEE_DIAL_DEFAULTS},
                "dmx_speed_div": NO_LOOK,
                "panel_leds_off": NO_LOOK,
                "idle_theme": "",
                "tempo_source": "online",
                "energy": "",
            })
            old = dict(existing) if existing else None
            entry.update(fields)
            self._entries[track_key] = entry
        self._save_cache()
        self._log_changes(track_key, _source, old, entry)
        if _source in ("operator", "library"):
            from drivers import look_matcher
            look_matcher.note_operator_edit(track_key)

    def snapshot(self):
        with self._lock:
            return dict(self._entries)

    def _log_changes(self, track_key, source, old, new):
        """Appends one row per changed field to the change log. old is None
        for a brand-new track row (logged with an empty "old"). Never raises
        -- a logging failure must not affect the live show."""
        try:
            rows = []
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            for field, new_val in new.items():
                if field in _LOG_SKIP_FIELDS:
                    continue
                old_val = "" if old is None else old.get(field, "")
                if old is not None and old_val == new_val:
                    continue
                if old is None and new_val in (NO_LOOK, ""):
                    continue  # a default the write didn't actually set
                if (old is None and field == "tempo_period"
                        and source in ("ai_energy", "library")):
                    continue  # template default, not a tempo anyone chose
                rows.append([ts, track_key, source, field, old_val, new_val])
            if not rows:
                return
            os.makedirs(config.LIGHT_PREFS_DIR, exist_ok=True)
            new_file = not os.path.exists(config.LIGHT_PREFS_LOG_PATH)
            with open(config.LIGHT_PREFS_LOG_PATH, "a", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                if new_file:
                    w.writerow(_LOG_HEADER)
                w.writerows(rows)
        except Exception as e:
            print(f"[LIGHT PREFS] Failed to write change log: {e}")

    def save_prefs_for(self, track_key, tempo_period,
                        dmx_color_index, dmx_theme_index,
                        marquee_color_index, marquee_theme_index,
                        accent_color_index, accent_theme_index,
                        accent_preset_id, accent_preset_color_index,
                        accent_palette_index, accent_speed, accent_brightness,
                        show_lower_marquees=NO_LOOK, accent_fx_rgb=NO_LOOK, accent_bg_rgb=NO_LOOK,
                        marquee_dials=None, dmx_speed_div=NO_LOOK, panel_leds_off=NO_LOOK):
        self._upsert(track_key, tempo_period=tempo_period,
                     # Legacy pair mirrors DMX -- see _save_cache()'s comment.
                     color_index=dmx_color_index, theme_index=dmx_theme_index,
                     dmx_color_index=dmx_color_index, dmx_theme_index=dmx_theme_index,
                     marquee_color_index=marquee_color_index, marquee_theme_index=marquee_theme_index,
                     accent_color_index=accent_color_index, accent_theme_index=accent_theme_index,
                     accent_preset_id=accent_preset_id, accent_preset_color_index=accent_preset_color_index,
                     accent_palette_index=accent_palette_index,
                     accent_speed=accent_speed, accent_brightness=accent_brightness,
                     show_lower_marquees=show_lower_marquees,
                     accent_fx_rgb=accent_fx_rgb, accent_bg_rgb=accent_bg_rgb,
                     **(marquee_dials or {}),
                     dmx_speed_div=dmx_speed_div,
                     panel_leds_off=panel_leds_off,
                     tempo_source="operator", _source="operator")
        print(f"[LIGHT PREFS] Saved for {track_key!r}: tempo={tempo_period:.3f}s, "
              f"dmx=({dmx_color_index},{dmx_theme_index}), "
              f"marquee=({marquee_color_index},{marquee_theme_index}), "
              f"accent=({accent_color_index},{accent_theme_index}), "
              f"accent_preset=({accent_preset_id},{accent_preset_color_index}), "
              f"accent_palette={accent_palette_index}, "
              f"accent_speed={accent_speed}, accent_brightness={accent_brightness}")

    def save_fixture_look_for(self, track_key, **fields):
        """Partial per-fixture-type look save from the library's per-song
        editor (web/remote_server.py's /api/library/light-prefs) -- accepts
        any subset of dmx_color_index/dmx_theme_index/marquee_color_index/
        marquee_theme_index/accent_color_index/accent_theme_index/
    accent_preset_id/accent_preset_color_index/accent_palette_index/
        accent_speed/accent_brightness, merged
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
        self._upsert(track_key, _source="library", **fields)
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
        self._upsert(track_key, tempo_period=tempo_period, tempo_source="online",
                     _source="online")
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
        self._upsert(track_key, energy=energy, _source="ai_energy")
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
    if state.medley_active:
        return  # the medley owns the lights; never re-save a song's look from its hook
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
    if key and state.now_playing_path:
        try:
            from drivers import track_record
            track_record.touch_edited(state.now_playing_path)   # File Manager "Last edited"
        except Exception as e:
            print(f"[LIGHT PREFS] last-edited stamp failed: {e}")
    if key:
        light_prefs_engine.save_prefs_for(
            key, state.dj_tempo_period,
            state.dmx_color_index, state.dmx_theme_index,
            state.marquee_color_index, state.marquee_theme_index,
            state.accent_color_index, state.accent_theme_index,
            state.accent_preset_id, state.accent_preset_color_index,
            state.accent_palette_index,
            state.accent_speed, state.accent_brightness,
            # Only the exception is stored; blanked (the default) saves as NO_LOOK.
            1 if not state.blank_lower_marquees else NO_LOOK,
            state.accent_fx_rgb, state.accent_bg_rgb,
            marquee_dials=_marquee_dial_exceptions(),
            # Only the exception is stored; full speed (1) saves as NO_LOOK.
            dmx_speed_div=state.dmx_speed_div if state.dmx_speed_div != 1 else NO_LOOK,
            # Only the exception is stored; animation on (the default) saves as NO_LOOK.
            panel_leds_off=1 if not state.panel_leds_fx else NO_LOOK,
        )


def _marquee_dial_exceptions():
    """Marquee brightness/speed/depth/off as stored: only values that differ
    from config.MARQUEE_DIAL_DEFAULTS, everything else NO_LOOK."""
    out = {}
    for k, default in config.MARQUEE_DIAL_DEFAULTS.items():
        v = int(getattr(state, k))
        out[k] = v if v != default else NO_LOOK
    return out


def reset_marquee_dials():
    """Every new song starts from the default marquee dials; a saved
    exception is re-applied by apply_prefs_for() once the song is known."""
    for k, default in config.MARQUEE_DIAL_DEFAULTS.items():
        setattr(state, k, bool(default) if k == "marquee_off" else default)


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


def decade_for(track_key):
    """The song's release decade as a number (1980, 1990, ...): the library
    tag if it has one, else the year the AI reported for the song playing
    now. None if neither is known yet."""
    from drivers import music_metadata_engine
    label = (music_metadata_engine.get_metadata_for(track_key) or {}).get("decade") or ""
    if label[:4].isdigit():
        return int(label[:4])
    if track_key and track_key == state.factoid_track_key and state.track_release_year:
        return state.track_release_year // 10 * 10
    return None


def resolve_idle_theme(track_key):
    """(theme, source) for panels 3-6 on this song: its own saved theme
    ("song"); else, with auto on, Dark for a "dark" song, then the theme for
    its release decade ("decade": 50s to 90s), then the one for its energy
    tag ("slow" -> Love, "fast" -> Technical Dreams); else the operator's
    general theme. A saved "@general" pins the general theme regardless."""
    prefs = light_prefs_engine.get_prefs_for(track_key) or {}
    own = prefs.get("idle_theme", "")
    if own == config.IDLE_THEME_GENERAL:
        return state.idle_theme_base, "song"
    if own in config.IDLE_ANIMATION_THEMES:
        return own, "song"
    if state.idle_theme_auto:
        energy = prefs.get("energy") or ""
        by_energy = config.IDLE_THEME_BY_ENERGY.get(energy)
        if by_energy and energy in config.IDLE_THEME_ENERGY_BEFORE_DECADE:
            return by_energy, energy
        by_decade = config.IDLE_THEME_BY_DECADE.get(decade_for(track_key))
        if by_decade:
            return by_decade, "decade"
        if by_energy:
            return by_energy, energy
    return state.idle_theme_base, "general"


def apply_idle_theme_for(track_key):
    from graphics.animations import set_effective_theme
    theme, source = resolve_idle_theme(track_key) if track_key else (state.idle_theme_base, "general")
    state.idle_theme_source = source
    if theme != state.idle_theme:
        print(f"[LIGHT PREFS] {track_key!r}: panel theme {theme!r} ({source})")
    set_effective_theme(theme, (get_energy_for(track_key) or "") if track_key else "")


def save_song_theme(track_key, theme):
    """Remote's "Theme for this song": "" = automatic (nothing stored),
    "@general", or a theme name. Only the exception is stored."""
    if not track_key:
        return
    if theme not in ("", config.IDLE_THEME_GENERAL) and theme not in config.IDLE_ANIMATION_THEMES:
        raise ValueError(f"unknown theme {theme!r}")
    existing = light_prefs_engine.get_prefs_for(track_key)
    if not existing and not theme:
        return
    extra = {} if existing else {"tempo_period": state.dj_tempo_period, "tempo_source": "online"}
    light_prefs_engine._upsert(track_key, idle_theme=theme, _source="theme", **extra)
    print(f"[LIGHT PREFS] {track_key!r}: song theme set to {theme or 'automatic'!r}")


def save_energy(track_key, energy):
    """Module-level wrapper, same convention as save_online_tempo()."""
    light_prefs_engine.save_energy_for(track_key, energy)
    if track_key and track_key == state.factoid_track_key:
        apply_idle_theme_for(track_key)  # a slow/fast tag can arrive mid-song


def get_energy_for(track_key):
    """Module-level wrapper, same convention as save_online_tempo()."""
    return light_prefs_engine.get_energy_for(track_key)


def _calm_carried_outline_dials(prefs):
    """A song with no saved outline speed/brightness inherits whatever the last
    song left on the dials. If that was cranked (speed or brightness above
    config.ACCENT_CARRYOVER_HOT_PCT), drop it to the calm defaults instead
    (speed 50%, brightness 35%). Dials this song saved itself are never touched."""
    hot = 255 * config.ACCENT_CARRYOVER_HOT_PCT / 100.0
    prefs = prefs or {}
    if prefs.get("accent_speed", NO_LOOK) < 0 and state.accent_speed > hot:
        state.accent_speed = round(255 * config.ACCENT_CARRYOVER_SPEED_PCT / 100.0)
        print(f"[LIGHT PREFS] Outline speed carried over hot -> {config.ACCENT_CARRYOVER_SPEED_PCT}%")
    if prefs.get("accent_brightness", NO_LOOK) < 0 and state.accent_brightness > hot:
        state.accent_brightness = round(255 * config.ACCENT_CARRYOVER_BRIGHTNESS_PCT / 100.0)
        print(f"[LIGHT PREFS] Outline brightness carried over hot -> {config.ACCENT_CARRYOVER_BRIGHTNESS_PCT}%")


def _with_borrowed_look(track_key, own):
    """own prefs with each fixture group this song never got a look for
    (DMX / marquee + dials / outline) filled from the most similar
    hand-designed song (drivers/look_matcher.py). Tempo is never borrowed.
    Returns own unchanged (possibly None) when nothing needed or found."""
    from drivers import look_matcher
    state.look_borrowed_from = ""
    own = own or {}
    if look_matcher.is_legacy_look(track_key, own):
        # Pre-split shared look: never designed for these fixtures -- borrow all three.
        need = [look_matcher.DMX_FIELDS, look_matcher.MARQUEE_FIELDS, look_matcher.ACCENT_FIELDS]
    else:
        need = []
        if any(own.get(f, NO_LOOK) < 0 for f in look_matcher.DMX_FIELDS):
            need.append(look_matcher.DMX_FIELDS)
        if any(own.get(f, NO_LOOK) < 0 for f in ("marquee_color_index", "marquee_theme_index")):
            need.append(look_matcher.MARQUEE_FIELDS)
        if (own.get("accent_preset_id", NO_LOOK) < 0 and own.get("accent_palette_index", NO_LOOK) < 0
                and any(own.get(f, NO_LOOK) < 0 for f in ("accent_color_index", "accent_theme_index"))):
            need.append(look_matcher.ACCENT_FIELDS)
    if not need:
        return own or None
    try:
        found = look_matcher.find_donor(track_key, light_prefs_engine.snapshot())
    except Exception as e:
        print(f"[LOOK MATCH] Lookup failed for {track_key!r}: {e}")
        found = None
    if not found:
        return own or None
    dkey, dprefs, score = found
    merged = dict(own)
    for group in need:
        for f in group:
            merged[f] = dprefs.get(f, NO_LOOK)
    state.look_borrowed_from = dkey
    print(f"[LOOK MATCH] {track_key!r} borrows the look of {dkey!r} (score {score:.1f}; "
          f"{len(need)} fixture group(s))")
    return merged


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

    # Unlike the color/pattern pairs below, an outline preset (or gradient
    # palette, 2026-09-26) never carries over from the previous track -- a
    # saved WLED preset/palette is a deliberate per-song pick (Fire1, or
    # Autumn, for one specific song), so every new track starts from "none"
    # and only gets one if its own row has it.
    state.accent_preset_id = NO_LOOK
    state.accent_preset_color_index = NO_LOOK
    state.accent_palette_index = NO_LOOK
    # Lower marquees (panels 3-6) are blanked unless THIS song saved an
    # exception -- never inherited from the previous song.
    state.blank_lower_marquees = True
    # Same for the outline's custom Fx/Bg colors: auto unless this song saved them.
    state.accent_fx_rgb = NO_LOOK
    state.accent_bg_rgb = NO_LOOK
    reset_marquee_dials()
    state.dmx_speed_div = 1  # full tempo unless this song saved 1/2 or 1/4
    state.panel_leds_fx = True  # button-LED animation on unless this song saved "off"
    apply_idle_theme_for(track_key)  # panels 3-6 theme: song's own / slow-fast auto / general

    own = light_prefs_engine.get_prefs_for(track_key)
    prefs = _with_borrowed_look(track_key, own)
    if not prefs:
        _calm_carried_outline_dials(None)
        return
    if own:
        state.dj_tempo_period = max(config.TEMPO_PERIOD_MIN_SECONDS,
                                     min(config.TEMPO_PERIOD_MAX_SECONDS, own["tempo_period"]))
        # An operator-saved tempo outranks any online BPM that lands later.
        if own.get("tempo_source", "operator") == "operator":
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
        state.accent_theme_index = accent_theme % len(config.ACCENT_EFFECT_NAMES)
        restored_any = True
        print(f"[LIGHT PREFS] Restored accent for {track_key!r}: "
              f"color={state.accent_color_index}, theme={state.accent_theme_index}")

    accent_preset = prefs.get("accent_preset_id", NO_LOOK)
    if accent_preset >= 0:
        state.accent_preset_id = accent_preset
        state.accent_preset_color_index = prefs.get("accent_preset_color_index", NO_LOOK)
        restored_any = True
        print(f"[LIGHT PREFS] Restored outline preset for {track_key!r}: "
              f"preset={state.accent_preset_id}, color_override={state.accent_preset_color_index}")

    accent_palette = prefs.get("accent_palette_index", NO_LOOK)
    if accent_palette >= 0:
        state.accent_palette_index = accent_palette % len(config.ACCENT_PALETTE_NAMES)
        restored_any = True
        print(f"[LIGHT PREFS] Restored outline gradient palette for {track_key!r}: "
              f"palette={state.accent_palette_index}")

    # Outline speed/brightness (2026-09-28) -- unlike the preset/palette
    # above these carry over from the previous track when this one has none
    # saved, same as the color/pattern pairs (a live operator dial, not a
    # deliberate per-song pick, until the operator actually saves one).
    accent_speed = prefs.get("accent_speed", NO_LOOK)
    if accent_speed >= 0:
        state.accent_speed = max(0, min(255, accent_speed))
        restored_any = True
    accent_brightness = prefs.get("accent_brightness", NO_LOOK)
    if accent_brightness >= 0:
        state.accent_brightness = max(0, min(255, accent_brightness))
        restored_any = True

    _calm_carried_outline_dials(prefs)
    for _f in ("accent_fx_rgb", "accent_bg_rgb"):
        if prefs.get(_f, NO_LOOK) >= 0:
            setattr(state, _f, prefs[_f] & 0xFFFFFF)
    if prefs.get("show_lower_marquees", NO_LOOK) == 1:
        state.blank_lower_marquees = False
        print(f"[LIGHT PREFS] {track_key!r}: lower marquees left ON (saved exception)")
    dials = {k: prefs[k] for k in config.MARQUEE_DIAL_DEFAULTS if prefs.get(k, NO_LOOK) >= 0}
    if dials:
        lo, hi = config.MARQUEE_SPEED_RANGE
        state.marquee_brightness = max(0, min(100, dials.get("marquee_brightness", state.marquee_brightness)))
        state.marquee_speed = max(lo, min(hi, dials.get("marquee_speed", state.marquee_speed)))
        state.marquee_depth = max(0, min(100, dials.get("marquee_depth", state.marquee_depth)))
        state.marquee_off = bool(dials.get("marquee_off", 0))
        print(f"[LIGHT PREFS] {track_key!r}: marquee dials {dials} (saved exception)")

    # DMX speed is the song's own (never borrowed), like its tempo.
    div = (own or {}).get("dmx_speed_div", NO_LOOK)
    if div in config.DMX_SPEED_DIVISORS:
        state.dmx_speed_div = div
        print(f"[LIGHT PREFS] {track_key!r}: DMX pattern at 1/{div} tempo (saved exception)")
    # Panel-button LED animation off is the song's own too (never borrowed).
    if (own or {}).get("panel_leds_off", NO_LOOK) == 1:
        state.panel_leds_fx = False
        print(f"[LIGHT PREFS] {track_key!r}: panel button animation OFF (saved exception)")

    if not restored_any:
        print(f"[LIGHT PREFS] Restored tempo only for {track_key!r}: "
              f"{state.dj_tempo_period:.3f}s (no saved look -- color/pattern stay random)")
