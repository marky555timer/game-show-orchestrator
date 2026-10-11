"""drivers/look_matcher.py
"Like songs look alike" (2026-10-07): a song the operator has never lit
borrows the whole look -- DMX, marquee (+ dials) and outline (effect,
palette/preset, Fx/Bg, speed, brightness) -- of the most similar song the
operator HAS hand-designed, instead of carrying over whatever the previous
song left on (which is how the outline kept landing on Solid/Blink).

Donors are the songs the operator edited themselves (the change log's
"operator"/"library" rows, plus every save since startup). Nothing is
written for the borrowing song -- the defaults-reset/store-exceptions rule
stands: the borrowed look is worked out fresh on every play, so it keeps
following the operator's newer edits, and the moment the operator tweaks a
borrowed song it becomes that song's own saved look, as before.

Similarity uses music_metadata (genre, decade, energy_rank 1-5, slow_dance),
the light-prefs energy tag (slow/fast/dark), BPM and shared artists. The
best LOOK_BORROW_TOP_N candidates are kept and one is chosen by a stable hash
of the song's key -- the same song always gets the same look, but a run of
similar songs doesn't all come out identical.
"""
import csv
import os
import re
import zlib

import config

NO_LOOK = -1

DMX_FIELDS = ("dmx_color_index", "dmx_theme_index")
# marquee_off is never borrowed -- a whole dark marquee is a one-song decision.
MARQUEE_FIELDS = ("marquee_color_index", "marquee_theme_index",
                  "marquee_brightness", "marquee_speed", "marquee_depth")
ACCENT_FIELDS = ("accent_color_index", "accent_theme_index", "accent_preset_id",
                 "accent_preset_color_index", "accent_palette_index", "accent_speed",
                 "accent_brightness", "accent_fx_rgb", "accent_bg_rgb")

_donors = None  # set of track keys the operator designed by hand


def _load_donors():
    keys = set()
    try:
        if os.path.exists(config.LIGHT_PREFS_LOG_PATH):
            with open(config.LIGHT_PREFS_LOG_PATH, "r", encoding="utf-8", newline="") as f:
                for row in csv.DictReader(f):
                    if row.get("source") in ("operator", "library") and row.get("track_key"):
                        keys.add(row["track_key"])
    except Exception as e:
        print(f"[LOOK MATCH] Could not read the change log: {e}")
    return keys


def note_operator_edit(track_key):
    """light_prefs_engine calls this on every operator/library save."""
    global _donors
    if _donors is None:
        _donors = _load_donors()
    if track_key:
        _donors.add(track_key)


def is_legacy_look(track_key, prefs):
    """True for a look left over from before the 2026-09-17 per-fixture split:
    one shared color/pattern copied into DMX, marquee AND outline (so the
    outline got WLED effect ids 0-12 -- Solid, Blink, Breathe, Wipe... -- and
    the marquee got out-of-range pattern numbers). ~350 songs, never actually
    designed for the marquee/outline; they borrow like a song with no look.
    A song the operator edited since the change log began is never legacy."""
    global _donors
    if not prefs:
        return False
    if _donors is None:
        _donors = _load_donors()
    if track_key in _donors:
        return False
    colors = {prefs.get(f, NO_LOOK) for f in ("dmx_color_index", "marquee_color_index", "accent_color_index")}
    themes = {prefs.get(f, NO_LOOK) for f in ("dmx_theme_index", "marquee_theme_index", "accent_theme_index")}
    return (len(colors) == 1 and len(themes) == 1 and NO_LOOK not in colors | themes
            and prefs.get("accent_preset_id", NO_LOOK) < 0 and prefs.get("accent_palette_index", NO_LOOK) < 0)


def _artists(key):
    part = key.split(" - ", 1)[0]
    return {a.strip() for a in re.split(r",|&| and | feat\.? | ft\.? |/", part) if len(a.strip()) > 2}


def _bpm(prefs):
    try:
        p = float(prefs.get("tempo_period") or 0)
    except (TypeError, ValueError):
        return None
    if p <= 0 or abs(p - config.TEMPO_DEFAULT_PERIOD_SECONDS) < 1e-6:
        return None  # the neutral fallback, not a known tempo
    return 60.0 / p


def _decade(meta):
    d = (meta or {}).get("decade") or ""
    return int(d[:4]) if d[:4].isdigit() else None


def _score(key, meta, prefs, dkey, dmeta, dprefs):
    s = 0.0
    if _artists(key) & _artists(dkey):
        s += 4.0
    if meta.get("genre") and dmeta.get("genre"):
        s += 3.0 if meta["genre"] == dmeta["genre"] else 0.0
    a, b = _decade(meta), _decade(dmeta)
    if a and b:
        gap = abs(a - b) // 10
        s += 2.0 if gap == 0 else (1.0 if gap == 1 else -0.75 * (gap - 1))
    ra, rb = meta.get("energy_rank"), dmeta.get("energy_rank")
    if ra and rb:
        s -= 1.0 * abs(ra - rb)
    sa, sb = meta.get("slow_dance"), dmeta.get("slow_dance")
    if sa is not None and sb is not None:
        s += 3.0 if (sa and sb) else (-3.0 if sa != sb else 0.0)
    ea, eb = prefs.get("energy"), dprefs.get("energy")
    if ea and eb:
        s += 1.5 if ea == eb else 0.0
    ba, bb = _bpm(prefs), _bpm(dprefs)
    if ba and bb:
        diff = min(abs(ba - bb), abs(ba - 2 * bb), abs(2 * ba - bb))  # half/double time reads alike
        s -= min(diff, 40.0) / 20.0
    return s


def _complete(p):
    return (all(p.get(f, NO_LOOK) >= 0 for f in DMX_FIELDS)
            and all(p.get(f, NO_LOOK) >= 0 for f in ("marquee_color_index", "marquee_theme_index"))
            and (p.get("accent_preset_id", NO_LOOK) >= 0
                 or all(p.get(f, NO_LOOK) >= 0 for f in ("accent_color_index", "accent_theme_index"))))


def find_donor(track_key, all_prefs):
    """Returns (donor_key, donor_prefs, score) or None. all_prefs is
    light_prefs_engine's {track_key: prefs} snapshot."""
    global _donors
    if not config.LOOK_BORROW_ENABLED or not track_key:
        return None
    if _donors is None:
        _donors = _load_donors()
    from drivers.music_metadata_engine import get_metadata_for
    meta = get_metadata_for(track_key) or {}
    prefs = all_prefs.get(track_key) or {}
    scored = []
    for dkey in _donors:
        if dkey == track_key:
            continue
        dprefs = all_prefs.get(dkey)
        if not dprefs or not _complete(dprefs):
            continue
        dmeta = get_metadata_for(dkey) or {}
        scored.append((_score(track_key, meta, prefs, dkey, dmeta, dprefs), dkey))
    if not scored:
        return None
    scored.sort(key=lambda t: (-t[0], t[1]))
    top = scored[:max(1, config.LOOK_BORROW_TOP_N)]
    score, dkey = top[zlib.crc32(track_key.encode("utf-8")) % len(top)]
    return dkey, all_prefs[dkey], score
