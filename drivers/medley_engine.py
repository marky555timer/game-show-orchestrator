"""drivers/medley_engine.py
Dance Medley mode: a short-hook "medley machine" for floor-filler tracks.

Pipeline
  analysis   drivers/medley_analysis.py (offline) -> medley_analysis.json:
             per-track BPM/beat grid, key, best hook (chorus/drop) windows.
  planning   build_plan(): picks eligible floor-fillers and chains them by
             tempo (gentle drift, bounded pitch-shift), key and energy;
             each track contributes ONE hook of ~8 bars (~15s).
  mixing     render_chunk(): resamples each hook to its target tempo and
             beat-aligns it to the previous one; the incoming hook's
             pre-roll is crossfaded (equal-power, with a bass swap) under
             the outgoing hook's last beats. Chunks are pre-mixed PCM so
             the join between them is gapless (pygame Channel.queue).
  live       start()/stop()/update()/status(): plays the chunks on the
             inactive deck, feeds now-playing/tempo, suppresses questions
             and announcements, and hands back to normal Auto-DJ when the
             mixable tracks are used up.

Announcements are always off during a medley. Sweepers are optional:
config.MEDLEY_SWEEPER_MODE = "off" | "medley" (audio/MedleySweepers only) |
"all" (medley + the regular sweepers folder). With sweepers on, one plays at
every hook change (config.MEDLEY_SWEEPER_EVERY), dealt like a shuffled deck,
and every hook change runs a light transition paired with it (_hook_fx).
"""
import glob
import json
import os
import random
import subprocess
import threading
import time

import numpy as np

import config
from state import state

FS = 44100
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_analysis = {"mtime": 0.0, "data": {}}
_overrides = {"mtime": 0.0, "data": {}}
_hook_ov = {"mtime": 0.0, "data": {}}


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------
def _load(holder, name):
    path = os.path.join(_BASE, name)
    try:
        mt = os.path.getmtime(path)
        if mt != holder["mtime"]:
            with open(path, "r", encoding="utf-8") as f:
                holder["data"] = json.load(f)
            holder["mtime"] = mt
    except Exception:
        pass
    return holder["data"]


def _camelot_distance(a, b):
    """0 same, 1 adjacent / relative, 2+ further. Unknown -> 2."""
    if not a or not b:
        return 2
    na, la = int(a[:-1]), a[-1]
    nb, lb = int(b[:-1]), b[-1]
    ring = min((na - nb) % 12, (nb - na) % 12)
    if la == lb:
        return ring
    return 1 + ring if ring <= 1 else 3


def _tempo_options(bpm):
    """Ways this track's beat can be read: as-is, double time, half time."""
    out = []
    for m in (1.0, 2.0, 0.5):
        n = bpm * m
        if config.MEDLEY_TEMPO_MIN <= n <= config.MEDLEY_TEMPO_MAX:
            out.append(n)
    return out


def _exclusion_reason(name, key, a, meta, include, exclude, has_clip):
    """Why a track is NOT in the medley pool, or None if it is. Shared by
    eligible_candidates() and pool_report() so the File Manager's explanation
    can never drift from what the planner actually does."""
    if not a or "bpm" not in a or not a.get("hooks"):
        return "no beat analysis yet"
    if name.lower() in exclude or key in exclude:
        return "excluded by you"
    forced = has_clip or name.lower() in include or key in include
    if not forced:
        energy = int(meta.get("energy_rank") or 0)
        if a["grid_conf"] < config.MEDLEY_MIN_GRID_CONF:
            return f"beat grid unsure ({a['grid_conf']:.0f} < {config.MEDLEY_MIN_GRID_CONF:.0f})"
        if energy < config.MEDLEY_MIN_ENERGY:
            return "not tagged for energy yet" if energy == 0 else f"energy {energy} < {config.MEDLEY_MIN_ENERGY}"
        if meta.get("slow_dance") is True:
            return "slow dance"
        if meta.get("genre") in config.MEDLEY_EXCLUDE_GENRES:
            return f"genre {meta.get('genre')} is excluded"
        if state.show_exclude_explicit and meta.get("explicit") is True:
            return "explicit (filtered out)"
    if not _tempo_options(a["hooks"][0].get("bpm", a["bpm"])):
        return "tempo outside the medley range"
    return None


def _clip_hook(clip, a):
    """A saved clip as a hook dict (same shape as an analysed hook)."""
    bpm = a["hooks"][0].get("bpm", a["bpm"])
    bars = max(1, int(round((clip["out"] - clip["in"]) * bpm / 240.0)))
    return {**a["hooks"][0], "start": clip["in"], "bars": bars, "bpm": bpm}


def eligible_candidates():
    """Floor-filler tracks with a trustworthy beat grid, as dicts:
    {path, name, title, artist, bpm, key_cam, hook, energy, duration}.
    A track with a human-approved clip (drivers/track_record.py) is always
    in, and its clip replaces the auto-detected hook."""
    from drivers.music_library import music_library, sanitize_track_key
    from drivers import music_metadata_engine as mme
    from drivers import track_record
    analysis = _load(_analysis, config.MEDLEY_ANALYSIS_FILE)
    ov = _load(_overrides, config.MEDLEY_CANDIDATES_FILE)
    include = {str(x).lower() for x in ov.get("include", [])}
    exclude = {str(x).lower() for x in ov.get("exclude", [])}
    saved = track_record.clips()
    out = []
    for t in music_library.all_tracks():
        name = os.path.basename(t["path"])
        a = analysis.get(name)
        key = sanitize_track_key(t["title"], t["artist"])
        meta = mme.get_metadata_for(key) or {}
        clip = saved.get(name)
        if _exclusion_reason(name, key, a, meta, include, exclude, clip is not None):
            continue
        hook = _clip_hook(clip, a) if clip else a["hooks"][0]
        out.append({
            "path": t["path"], "name": name, "title": t["title"], "artist": t["artist"],
            "bpm": a["hooks"][0].get("bpm", a["bpm"]), "key_cam": a.get("camelot"), "hook": hook,
            "hooks": a["hooks"], "energy": int(meta.get("energy_rank") or 0), "duration": a["duration"],
            "key": key, "clip": clip,
        })
    return out


def pool_report():
    """{file name: {"state": "clip"|"auto"|"off", "reason": str|None,
    "included": bool, "excluded": bool}} for the File Manager's Medley column:
    clip = green (human-approved), auto = yellow (picked by the energy/grid
    test, points are AI estimates), off = grey (not in the pool, `reason`
    says why)."""
    from drivers.music_library import music_library, sanitize_track_key
    from drivers import music_metadata_engine as mme
    from drivers import track_record
    analysis = _load(_analysis, config.MEDLEY_ANALYSIS_FILE)
    ov = _load(_overrides, config.MEDLEY_CANDIDATES_FILE)
    include = {str(x).lower() for x in ov.get("include", [])}
    exclude = {str(x).lower() for x in ov.get("exclude", [])}
    saved = track_record.clips()
    rep = {}
    for t in music_library.all_tracks():
        name = os.path.basename(t["path"])
        key = sanitize_track_key(t["title"], t["artist"])
        meta = mme.get_metadata_for(key) or {}
        reason = _exclusion_reason(name, key, analysis.get(name), meta, include, exclude, name in saved)
        rep[name] = {"state": "clip" if (name in saved and not reason) else ("off" if reason else "auto"),
                     "reason": reason, "included": name.lower() in include, "excluded": name.lower() in exclude}
    return rep


def set_pool_override(name, mode):
    """mode: "include" (force in), "exclude" (keep out) or "auto" (clear)."""
    path = os.path.join(_BASE, config.MEDLEY_CANDIDATES_FILE)
    data = dict(_load(_overrides, config.MEDLEY_CANDIDATES_FILE))
    inc = [x for x in data.get("include", []) if str(x).lower() != name.lower()]
    exc = [x for x in data.get("exclude", []) if str(x).lower() != name.lower()]
    if mode == "include":
        inc.append(name)
    elif mode == "exclude":
        exc.append(name)
    data["include"], data["exclude"] = sorted(inc), sorted(exc)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)
    _overrides["data"], _overrides["mtime"] = data, os.path.getmtime(path)


# --------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------
def _step_tempo(cur, native):
    """Next playback tempo when moving toward `native`, drift-limited."""
    d = cur * config.MEDLEY_MAX_TEMPO_DRIFT
    return cur + max(-d, min(d, native - cur))


def _feasible(cur, bpm):
    """Best (native_used, new_tempo) reading of `bpm` for a track following a
    segment at tempo `cur`, or None."""
    best = None
    for n in _tempo_options(bpm):
        nt = _step_tempo(cur, n)
        stretch = abs(nt / n - 1.0)
        if stretch <= config.MEDLEY_MAX_STRETCH and abs(n - cur) / cur <= config.MEDLEY_MAX_JUMP:
            score = abs(n - cur) / cur
            if best is None or score < best[0]:
                best = (score, n, nt)
    return best


def _pick_bars(max_bars, tempo):
    """Even bar count (>=4, <= the analyzed hook) whose length at the playback
    tempo is closest to config.MEDLEY_HOOK_TARGET_SECONDS."""
    best = None
    for b in range(4, int(max_bars) + 1, 2):
        d = abs(b * 4 * 60.0 / tempo - config.MEDLEY_HOOK_TARGET_SECONDS)
        if best is None or d < best[0]:
            best = (d, b)
    return best[1] if best else int(max_bars)


def _make_seg(c, native, tempo, sb, eb):
    """One plan segment from candidate fields `c` at playback `tempo`, with the
    per-song start/end beat adjustments (`sb`, `eb`; positive = later)."""
    beat_native = 60.0 / native            # one felt beat, in the file's own seconds
    if c.get("clip"):
        # human-approved clip: exact in/out, no auto extra bars / beat nudges
        cl = c["clip"]
        hook_start = max(0.0, cl["in"])
        beats = max(config.MEDLEY_MIN_HOOK_BEATS, int(round((cl["out"] - cl["in"]) / beat_native)))
        room = c["duration"] - hook_start - 2.0
        while beats > config.MEDLEY_MIN_HOOK_BEATS and beats * beat_native > room:
            beats -= 1
        return {**c, "hook_start": hook_start, "bars": beats // 4, "beats": beats, "native": native,
                "tempo": tempo, "rate": tempo / native, "length": beats * 60.0 / tempo,
                "start_beats": 0, "end_beats": 0}
    hook_start = max(0.0, c["hook"]["start"] + sb * beat_native)
    beats = _pick_bars(c["hook"]["bars"], tempo) * 4 + config.MEDLEY_EXTRA_BARS * 4 + eb - sb
    room = c["duration"] - hook_start - 2.0  # keep it inside the file
    step = 1 if (sb or eb) else 4            # tuned songs trim beat by beat
    while beats > config.MEDLEY_MIN_HOOK_BEATS and (
            beats * 60.0 / tempo > config.MEDLEY_MAX_HOOK_SECONDS or beats * beat_native > room):
        beats -= step
    beats = max(beats, config.MEDLEY_MIN_HOOK_BEATS)
    return {**c, "hook_start": hook_start, "bars": beats // 4, "beats": beats, "native": native,
            "tempo": tempo, "rate": tempo / native, "length": beats * 60.0 / tempo,
            "start_beats": sb, "end_beats": eb}


_opts = {"mtime": 0.0, "data": {}}


def options():
    """The operator's medley settings (remote's radio buttons + song count):
    {"source": "latest"|"green"|"yellow", "songs": int}."""
    d = _load(_opts, config.MEDLEY_OPTIONS_FILE)
    src = d.get("source", "latest")
    try:
        n = int(d.get("songs", config.MEDLEY_DEFAULT_SONGS))
    except (TypeError, ValueError):
        n = config.MEDLEY_DEFAULT_SONGS
    return {"source": src if src in config.MEDLEY_SOURCES else "latest",
            "songs": max(config.MEDLEY_MIN_TRACKS, min(config.MEDLEY_MAX_TRACKS, n)),
            "next_only": bool(d.get("next_only", False))}


def set_options(source=None, songs=None, next_only=None):
    cur = options()
    if next_only is not None:
        cur["next_only"] = bool(next_only)
    if source is not None:
        if source not in config.MEDLEY_SOURCES:
            raise ValueError("source must be one of " + ", ".join(config.MEDLEY_SOURCES))
        cur["source"] = source
    if songs is not None:
        cur["songs"] = max(config.MEDLEY_MIN_TRACKS, min(config.MEDLEY_MAX_TRACKS, int(songs)))
    path = os.path.join(_BASE, config.MEDLEY_OPTIONS_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, indent=1)
    os.replace(tmp, path)
    _opts["data"], _opts["mtime"] = cur, os.path.getmtime(path)
    return cur


def next_only_paths():
    """Paths of the medley pool when "Next track plays medley songs only" is
    on (so the operator can dress the hottest tracks with lighting), else
    None. deck_orchestrator._pick_next_track() filters its candidates by it."""
    if not options()["next_only"]:
        return None
    return {c["path"] for c in eligible_candidates()}


def pool_counts():
    """How many songs each source option could draw from right now."""
    pool = eligible_candidates()
    green = sum(1 for c in pool if c.get("clip"))
    return {"latest": len(pool), "green": green, "yellow": len(pool) - green}


def build_plan(start_bpm=None, seed=None, first_name=None, source=None, songs=None):
    """Greedy tempo/key chain over the eligible pool. Returns a list of
    segment dicts (see render_chunk) -- empty if there aren't enough.
    source: "latest" = every eligible song, human-edited clips first and the
    most recently edited leading; "green" = human-edited clips only;
    "yellow" = AI-included songs only. songs = how many to plan (MEDLEY_MAX_SECONDS, if set,
    cap can still cut it shorter). Both default to the saved options()."""
    rng = random.Random(seed)
    o = options()
    source = source or o["source"]
    songs = songs or o["songs"]
    pool = eligible_candidates()
    if source == "green":
        pool = [c for c in pool if c.get("clip")]
    elif source == "yellow":
        pool = [c for c in pool if not c.get("clip")]
    if len(pool) < config.MEDLEY_MIN_TRACKS:
        return []
    named = [c for c in pool if first_name and first_name.lower() in (c["name"] + " " + c["key"]).lower()]
    if named:
        first = named[0]
        start_bpm = start_bpm or first["bpm"]
    elif start_bpm:
        opts = [c for c in pool if _feasible(start_bpm, c["bpm"])]
        first = min(opts, key=lambda c: _feasible(start_bpm, c["bpm"])[0]) if opts else rng.choice(pool)
    else:
        first = rng.choice(pool)
    # human-edited clips lead: they open the medley and win every pick they're
    # tempo-feasible for (see CLIP_PRIORITY below)
    # ...and among them the most recently edited come first (operator, 2026-10-01)
    clip_rank = {c["name"]: r for r, c in enumerate(
        sorted((c for c in pool if c.get("clip")), key=lambda c: c["clip"].get("edited_at", 0.0), reverse=True))}
    if not named and source == "latest":
        edited = [c for c in pool if c.get("clip")]
        if start_bpm:
            edited = [c for c in edited if _feasible(start_bpm, c["bpm"])]
        if edited:
            first = min(edited, key=lambda c: clip_rank[c["name"]])
    plan, used, total = [], set(), 0.0
    used_keys = set()
    # the last hook rings out MEDLEY_LAST_TAIL_SECONDS, so budget for it
    budget = (config.MEDLEY_MAX_SECONDS - config.MEDLEY_LAST_TAIL_SECONDS) if config.MEDLEY_MAX_SECONDS else float("inf")
    CLIP_PRIORITY = 1.0
    RECENCY_WEIGHT = 3.0        # newest clip gets this much extra pull, the oldest none
    n_clips = max(1, len(clip_rank) - 1)

    def clip_bonus(c):
        if source != "latest" or not c.get("clip"):
            return 0.0
        return CLIP_PRIORITY + RECENCY_WEIGHT * (1.0 - clip_rank[c["name"]] / n_clips)

    def add(c, native, tempo):
        nonlocal total
        ov = _load(_hook_ov, config.MEDLEY_HOOK_OVERRIDES_FILE).get(c["key"], {})
        seg = _make_seg(c, native, tempo, int(ov.get("start_beats", 0)), int(ov.get("end_beats", 0)))
        if plan and total + seg["length"] > budget:
            return False            # would push the medley past the cap
        plan.append(seg)
        used.add(c["name"])
        used_keys.add(c["key"])
        total += seg["length"]
        return True

    # songs with an instrumental are held back for the finale (vocal-drop
    # ending) so the chain can't use them up before the last hook
    reserved = {c["name"] for c in pool if c is not first and stem_path(c["path"])} \
        if config.MEDLEY_VOCAL_DROP and config.MEDLEY_DROP_PREFER_LAST else set()
    n0 = min(_tempo_options(first["bpm"]), key=lambda n: abs(n - (start_bpm or 118.0)))
    add(first, n0, n0)
    while total < budget and len(plan) < songs:
        cur = plan[-1]
        scored = []
        for c in pool:
            if c["name"] in used or c["name"] in reserved or c["key"] in used_keys \
                    or c["artist"].lower() == cur["artist"].lower():
                continue
            f = _feasible(cur["tempo"], c["bpm"])
            if not f:
                continue
            tempo_fit, n, nt = f
            key_pen = 0.08 * min(3, _camelot_distance(cur["key_cam"], c["key_cam"]))
            energy_bonus = -0.02 * c["energy"]
            score = tempo_fit + key_pen + energy_bonus + rng.random() * 0.06 - clip_bonus(c)
            scored.append((score, c, n, nt))
        if not scored:
            break
        scored.sort(key=lambda s: s[0])
        for _, c, n, nt in scored:      # best first; take the first one that still fits the cap
            if add(c, n, nt):
                break
        else:
            break
    if config.MEDLEY_VOCAL_DROP and config.MEDLEY_DROP_PREFER_LAST and len(plan) >= 2 \
            and not stem_path(plan[-1]["path"]):
        _end_on_stem(plan, pool, used, used_keys, rng)
    return plan if len(plan) >= config.MEDLEY_MIN_TRACKS else []


def _end_on_stem(plan, pool, used, used_keys, rng):
    """Make the final hook a song that has an instrumental (vocal-drop
    ending): swap it in for the last hook if it follows the one before by
    tempo, else append it after the last; failing both, swap in the closest
    tempo anyway (one bigger tempo step at the very end beats no drop)."""
    stems = [c for c in pool if stem_path(c["path"]) and c["name"] not in used and c["key"] not in used_keys]
    if not stems:
        return
    rng.shuffle(stems)
    ov = _load(_hook_ov, config.MEDLEY_HOOK_OVERRIDES_FILE)

    def seg_for(c, native, tempo):
        o = ov.get(c["key"], {})
        return _make_seg(c, native, tempo, int(o.get("start_beats", 0)), int(o.get("end_beats", 0)))

    prev = plan[-2]
    for c in stems:
        f = _feasible(prev["tempo"], c["bpm"])
        if f and c["artist"].lower() != prev["artist"].lower():
            plan[-1] = seg_for(c, f[1], f[2])
            return
    for c in stems:
        f = _feasible(plan[-1]["tempo"], c["bpm"])
        if f and c["artist"].lower() != plan[-1]["artist"].lower():
            plan.append(seg_for(c, f[1], f[2]))
            return
    cur = prev["tempo"]
    best = min(((abs(n - cur), c, n) for c in stems if c["artist"].lower() != prev["artist"].lower()
                for n in _tempo_options(c["bpm"])), key=lambda t: t[0], default=None)
    if best:
        _, c, n = best
        plan[-1] = seg_for(c, n, n)


# --------------------------------------------------------------------------
# mixing
# --------------------------------------------------------------------------
def _ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _decode_window(path, start, dur):
    """Stereo float32 at FS for [start, start+dur) (start may be negative ->
    zero-padded on the left)."""
    pad = 0
    if start < 0:
        pad = int(round(-start * FS))
        dur += start
        start = 0.0
    proc = subprocess.run(
        [_ffmpeg(), "-v", "error", "-ss", f"{start:.4f}", "-t", f"{max(dur, 0.05):.4f}", "-i", path,
         "-ac", "2", "-ar", str(FS), "-f", "s16le", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    a = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32).reshape(-1, 2) / 32768.0
    if pad:
        a = np.vstack([np.zeros((pad, 2), np.float32), a])
    return a


def _resample(a, rate):
    """Play `a` at speed `rate` (>1 = faster/shorter) with cubic interpolation."""
    if abs(rate - 1.0) < 1e-4:
        return a
    n_out = int((len(a) - 3) / rate)
    pos = np.arange(n_out) * rate + 1.0
    i = pos.astype(np.int64)
    f = (pos - i)[:, None].astype(np.float32)
    p0, p1, p2, p3 = a[i - 1], a[i], a[i + 1], a[i + 2]
    return p1 + 0.5 * f * (p2 - p0 + f * (2 * p0 - 5 * p1 + 4 * p2 - p3 + f * (3 * (p1 - p2) + p3 - p0)))


def _lowpass(a, fc=180.0):
    tau = FS / (2 * np.pi * fc)
    k = np.exp(-np.arange(int(tau * 6)) / tau).astype(np.float32)
    k /= k.sum()
    out = np.empty_like(a)
    for ch in range(a.shape[1]):
        out[:, ch] = np.convolve(a[:, ch], k, mode="full")[:len(a)]
    return out


def _xfade(out_tail, in_head):
    """Equal-power crossfade with a bass swap: the outgoing lows leave in the
    first half, the incoming lows arrive in the second half."""
    n = min(len(out_tail), len(in_head))
    out_tail, in_head = out_tail[:n], in_head[:n]
    p = np.linspace(0.0, 1.0, n, dtype=np.float32)[:, None]
    g_out = np.cos(p * np.pi / 2)
    g_in = np.sin(p * np.pi / 2)
    lo_out, lo_in = _lowpass(out_tail), _lowpass(in_head)
    hi_out, hi_in = out_tail - lo_out, in_head - lo_in
    low_out = np.clip(1.0 - 2.0 * p, 0.0, 1.0)
    low_in = np.clip(2.0 * p - 1.0, 0.0, 1.0)
    return (hi_out * g_out + lo_out * low_out) + (hi_in * g_in + lo_in * low_in)


def _seg_window(seg, xf_in_s, tail_extra_s=0.0, path=None, gain=None, info=None):
    """Decode + resample one segment's playback audio from (hook start -
    xf_in) to (hook end + tail_extra). Returns (audio, pre_samples) where
    pre_samples = number of leading pre-roll samples. `path`/`gain` let the
    vocal-drop ending read the song's instrumental through the exact same
    window, speed and level as the full mix; `info["gain"]` receives the
    level-match gain used."""
    r = seg["rate"]
    pre_native = xf_in_s * r
    body_native = seg["length"] * r
    tail_native = tail_extra_s * r
    start = seg["hook_start"] - pre_native
    a = _decode_window(path or seg["path"], start - 0.05, pre_native + body_native + tail_native + 0.15)
    a = a[int(0.05 * FS):]
    y = _resample(a, r)
    pre = int(round(xf_in_s * FS))
    # level-match: each hook's body is brought toward a common RMS (bounded)
    if gain is None:
        gain = 1.0
        body = y[pre:pre + int(round(seg["length"] * FS))]
        if len(body):
            rms = float(np.sqrt(np.mean(body ** 2)))
            if rms > 1e-4:
                gain = float(np.clip(config.MEDLEY_TARGET_RMS / rms, 0.7, 1.5))
    if info is not None:
        info["gain"] = gain
    return y * gain, pre


# --------------------------------------------------------------------------
# vocal-drop ending (see config.MEDLEY_VOCAL_DROP)
# --------------------------------------------------------------------------
def stem_path(name_or_path):
    """The song's precomputed instrumental, or None."""
    base = os.path.splitext(os.path.basename(name_or_path))[0]
    p = os.path.join(config.MEDLEY_STEMS_DIR, base + ".inst.flac")
    return p if os.path.exists(p) else None


def _drop_bed_seconds(seg):
    """Playback seconds of bed available after the out point (0 = no drop)."""
    if not config.MEDLEY_VOCAL_DROP or not stem_path(seg["path"]):
        return 0.0
    out_native = seg["hook_start"] + seg["length"] * seg["rate"]
    room = (seg["duration"] - out_native - 0.5) / seg["rate"]
    bed = min(config.MEDLEY_DROP_BED_SECONDS, room)
    return bed if bed >= 8.0 else 0.0


def _align_lag(ref, other, max_lag=4096):
    """Samples `other` lags `ref` (mono cross-correlation). The instrumental
    was decoded by a different MP3 decoder than the live mix, so a small
    fixed offset is possible; it's measured, not assumed."""
    a = ref.mean(axis=1) if ref.ndim == 2 else ref
    b = other.mean(axis=1) if other.ndim == 2 else other
    n = min(len(a), len(b))
    if n < 2 * max_lag:
        return 0
    a, b = a[:n] - a[:n].mean(), b[:n] - b[:n].mean()
    size = 1 << int(np.ceil(np.log2(2 * n)))
    xc = np.fft.irfft(np.fft.rfft(b, size) * np.conj(np.fft.rfft(a, size)), size)
    lags = np.concatenate([xc[:max_lag + 1], xc[-max_lag:]])
    k = int(np.argmax(lags))
    return k if k <= max_lag else k - len(lags)


def _render_drop(seg, chunk, y, pre, body_end, gain):
    """Replace the last chunk's ring-out with the vocal drop. `chunk` already
    holds the full mix (incl. the incoming crossfade head) out to the bed's
    end. Returns the new chunk; sets seg["drop_sweep_at"] (seconds into the
    chunk) for update() to fire the sweeper."""
    inst, _ = _seg_window(seg, pre / FS, _drop_bed_seconds(seg), path=stem_path(seg["path"]), gain=gain)
    win0, win1 = max(0, body_end - int(6 * FS)), body_end
    lag = _align_lag(y[win0:win1], inst[win0:win1])
    if lag > 0:
        inst = inst[lag:]
    elif lag < 0:
        inst = np.vstack([np.zeros((-lag, 2), np.float32), inst])
    n = min(len(chunk), len(inst))
    chunk, inst = chunk[:n], inst[:n]
    beat = int(round(60.0 / seg["tempo"] * FS))
    g = np.zeros(n, np.float32)
    g[body_end:] = 1.0
    ramp = min(beat, n - body_end)
    if ramp > 0:
        g[body_end:body_end + ramp] = np.linspace(0.0, 1.0, ramp, dtype=np.float32)
    p = g[:, None] * (np.pi / 2)
    out = chunk * np.cos(p) + inst * np.sin(p)        # equal-power vocal drop
    # the duck itself is a LIVE deck-volume ramp (update()), not baked in here
    duck_at = min(body_end + config.MEDLEY_DROP_SWEEPER_BARS * 4 * beat, n - 1)
    f = min(n, int(config.MEDLEY_DROP_FADE_SECONDS * FS))
    out[-f:] *= np.linspace(1.0, 0.0, f, dtype=np.float32)[:, None]
    seg["drop_at"] = body_end / FS
    seg["drop_sweep_at"] = duck_at / FS
    print(f"[MEDLEY] Vocal drop ready for {seg['artist']} - {seg['title']}: bed {(n - body_end) / FS:.0f}s, "
          f"align lag {lag} samples.")
    return out


def _xf_seconds(tempo):
    return config.MEDLEY_XFADE_BEATS * 60.0 / tempo


def render_chunk(plan, i, carry, first=0, last_limit=None):
    """Render chunk i (float32 stereo, gapless with chunk i+1).
    `carry` = the previous segment's faded-out tail array (or None).
    `first` = index of the run's first chunk (gets the short fade-in).
    `last_limit` = for the transition editor: stop the final chunk this many
    seconds after its hook begins instead of playing it out.
    Returns (chunk, next_carry)."""
    seg = plan[i]
    last = i == len(plan) - 1
    xf_in = 0.5 if i == first else _xf_seconds(seg["tempo"])
    xf_out = 0.0 if last else _xf_seconds(plan[i + 1]["tempo"])
    seg.pop("drop_at", None)
    seg.pop("drop_sweep_at", None)
    bed = _drop_bed_seconds(seg) if last and not last_limit else 0.0
    tail_extra = (bed or config.MEDLEY_LAST_TAIL_SECONDS) if last and not last_limit else 0.0
    info = {}
    y, pre = _seg_window(seg, xf_in, tail_extra, info=info)
    body_end = pre + int(round(seg["length"] * FS))
    # fade-in at the very start of the medley's first chunk (the deck
    # crossfade handles the rest); later chunks get their head from _xfade
    chunk = y[:body_end - int(round(xf_out * FS))].copy() if not last else y.copy()
    if i == first:
        n = min(len(chunk), int(0.4 * FS))
        chunk[:n] *= np.linspace(0.0, 1.0, n, dtype=np.float32)[:, None]
    if carry is not None:
        head_n = min(len(carry), len(chunk))
        chunk[:head_n] = _xfade(carry[:head_n], chunk[:head_n])
    next_carry = None
    if not last:
        next_carry = y[body_end - int(round(xf_out * FS)):body_end].copy()
    elif last_limit:
        chunk = chunk[:pre + int(last_limit * FS)]
        n = min(len(chunk), int(0.6 * FS))
        chunk[-n:] *= np.linspace(1.0, 0.0, n, dtype=np.float32)[:, None]
    elif bed:
        try:
            chunk = _render_drop(seg, chunk, y, pre, body_end, info["gain"])
        except Exception as e:
            print(f"[MEDLEY] Vocal drop failed ({e}) -- plain ending instead.")
            seg.pop("drop_sweep_at", None)
            seg.pop("drop_at", None)
            chunk = chunk[:body_end + int(config.MEDLEY_LAST_TAIL_SECONDS * FS)]
            n = min(len(chunk), int(config.MEDLEY_LAST_TAIL_SECONDS * FS))
            chunk[-n:] *= np.linspace(1.0, 0.0, n, dtype=np.float32)[:, None]
    else:
        n = min(len(chunk), int(config.MEDLEY_LAST_TAIL_SECONDS * FS))
        chunk[-n:] *= np.linspace(1.0, 0.0, n, dtype=np.float32)[:, None]
    # soft limiter: gentle tanh knee above 0.85 instead of rescaling the chunk
    # (a per-chunk gain change would make the level jump at the joins)
    knee = 0.85
    mag = np.abs(chunk)
    over = mag > knee
    if over.any():
        chunk = np.where(over, np.sign(chunk) * (knee + (1 - knee) * np.tanh((mag - knee) / (1 - knee))), chunk)
    if next_carry is not None:
        m2 = np.abs(next_carry)
        o2 = m2 > knee
        if o2.any():
            next_carry = np.where(o2, np.sign(next_carry) * (knee + (1 - knee) * np.tanh((m2 - knee) / (1 - knee))), next_carry)
    return chunk, next_carry


def to_int16(chunk):
    return (np.clip(chunk, -1.0, 1.0) * 32767.0).astype(np.int16)


# --------------------------------------------------------------------------
# sweepers
# --------------------------------------------------------------------------
def _sweeper_paths():
    mode = state.medley_sweeper_mode
    if mode == "off":
        return []
    paths = glob.glob(os.path.join(config.MEDLEY_SWEEPER_DIR, "*.wav")) \
        + glob.glob(os.path.join(config.MEDLEY_SWEEPER_DIR, "*.mp3"))
    if mode == "all":
        paths += glob.glob(os.path.join(config.SWEEPERS_DIR, "*.wav"))
    return paths


# --------------------------------------------------------------------------
# live engine
# --------------------------------------------------------------------------
_lock = threading.Lock()
_run = None  # the active run dict, or None
_last_plan = []  # the most recent medley's plan (segments), for REPLAY MEDLEY


def _deck_name(n):
    return "deck1" if n == 1 else "deck2"


def is_active():
    return _run is not None


def _render_worker(run):
    """Keeps a couple of chunks rendered ahead of playback."""
    plan = run["plan"]
    first = run["first"]
    try:
        carry = None
        for i in range(first, len(plan)):
            while not run["stop"] and i - run["played"] > 2:
                time.sleep(0.2)
            if run["stop"]:
                return
            chunk, carry = render_chunk(plan, i, carry, first=first)
            with _lock:
                run["chunks"][i] = to_int16(chunk).tobytes()
    except Exception as e:
        print(f"[MEDLEY] Render failed at chunk: {e}")
        run["error"] = str(e)


def start():
    """Begin a medley from the current moment. Returns {ok, error?, tracks?}."""
    global _run
    if _run is not None:
        return {"ok": False, "error": "a medley is already running"}
    if state.show_phase != "live" or state.mode != state.MODE_DJ:
        return {"ok": False, "error": "medley needs a live show in DJ mode"}
    from drivers import deck_orchestrator
    if deck_orchestrator.has_pending_move():
        return {"ok": False, "error": "a transition is already in progress"}
    start_bpm = None
    a = _load(_analysis, config.MEDLEY_ANALYSIS_FILE).get(os.path.basename(state.now_playing_path or ""))
    if a and "bpm" in a:
        opts = _tempo_options(a["bpm"])
        start_bpm = opts[0] if opts else None
    opt = options()
    plan = build_plan(start_bpm)
    if not plan:
        what = {"green": "human-edited (green)", "yellow": "AI-included (yellow)"}.get(opt["source"], "floor-filler")
        return {"ok": False, "error": f"not enough mixable {what} songs (need {config.MEDLEY_MIN_TRACKS}+ that fit by tempo)"}
    run = {"plan": plan, "requested": opt["songs"], "first": 0, "chunks": {}, "sounds": {}, "played": 0, "queued": -1, "stop": False,
           "started": False, "began_at": 0.0, "chunk_started_at": 0.0, "sweeps": 0,
           "deck": 2 if state.active_deck == 1 else 1, "from_deck": state.active_deck,
           "handoff_sent": False}
    _run = run
    _last_plan[:] = plan
    from drivers import medley_game
    medley_game.new_game(plan)
    state.medley_active = True
    state.medley_count, state.medley_pos = len(plan), 0
    state.trivia_confirm_active = False
    threading.Thread(target=_render_worker, args=(run,), daemon=True, name="medley-render").start()
    print(f"[MEDLEY] Planned {len(plan)} tracks, ~{sum(s['length'] for s in plan):.0f}s: "
          + " > ".join(f"{s['artist']} - {s['title']} ({s['tempo']:.0f})" for s in plan[:6]) + " ...")
    return {"ok": True, "tracks": len(plan)}


def _finish(run, reason, handoff):
    """Common teardown. handoff=True -> crossfade to a normal Auto-DJ track."""
    global _run
    if _run is not run:
        return
    run["stop"] = True
    _run = None
    state.medley_active = False
    if state.medley_bed_hold:
        state.medley_results_until = 0.0  # the held winner screen goes with the bed
    state.medley_bed_hold = False
    state.medley_count = state.medley_pos = 0
    from drivers import medley_game
    medley_game.finish(show_results=True)
    print(f"[MEDLEY] Ended ({reason}).")
    if handoff:
        from drivers import deck_orchestrator
        state.tempo_operator_set = False
        deck_orchestrator.trigger_track_move("next")


def stop(reason="stopped by operator"):
    """Exit the medley now and crossfade into a normal track."""
    run = _run
    if run is None:
        return False
    _finish(run, reason, handoff=run["started"])
    return True


def in_bed_hold():
    run = _run
    return run is not None and bool(run.get("bed_hold"))


def end_bed(reason="panel button"):
    """Host is done talking over the vocal-drop bed: clear the winner screen
    and crossfade back into normal play (whatever mode the show was in)."""
    run = _run
    if run is None or not run.get("bed_hold"):
        return False
    _finish(run, f"bed ended by {reason}", handoff=True)
    return True


def notify_external_move():
    """deck_orchestrator is starting its own move (next/back): the medley's
    deck is being crossfaded away, so just stop feeding it."""
    run = _run
    if run is not None:
        _finish(run, "normal track move", handoff=False)


def _set_now_playing(run, i):
    seg = run["plan"][i]
    n = run["deck"]
    tup = (seg["title"], seg["artist"])
    if n == 1:
        state.deck1_track, state.deck1_confident, state.deck1_track_source = tup, True, "native"
    else:
        state.deck2_track, state.deck2_confident, state.deck2_track_source = tup, True, "native"
    state.active_deck = n
    state.medley_pos = i + 1
    state.now_playing_path = seg["path"]
    state.now_playing_duration = seg["duration"]
    try:
        from drivers import track_record
        track_record.touch_played(seg["path"])
    except Exception:
        pass
    # ensure_prefetch() (which normally sets this and applies the song's saved
    # light show) is switched off during a medley, so do both here.
    state.factoid_track_key = seg["key"]
    try:
        from drivers import light_prefs_engine
        light_prefs_engine.apply_prefs_for(seg["key"])
    except Exception as e:
        print(f"[MEDLEY] Light prefs for {seg['key']!r} failed: {e}")
    # the saved look is applied above, but the LIGHT TEMPO follows what is
    # actually playing (the hook's playback tempo), not the song's saved tempo
    state.dj_tempo_period = 60.0 / seg["tempo"]
    state.tempo_operator_set = True  # keep online BPM lookups from overriding it
    state.deck_change_count += 1
    state.auto_dj_track_started_at = time.time()
    state.auto_dj_track_duration = 3600.0  # nothing else may fire a transition mid-medley
    from drivers.branding_engine import notify_deck_change
    notify_deck_change()
    from drivers import medley_game
    medley_game.begin_hook(i, seg, run.get("cur_len", seg["length"]))


_sweeper_deck = []


def _deal_sweeper(paths):
    """Shuffled-deck pick: every sweeper plays once before any repeats
    (a medley plays one per hook change, so plain random repeats fast)."""
    global _sweeper_deck
    _sweeper_deck = [p for p in _sweeper_deck if p in paths]
    if not _sweeper_deck:
        _sweeper_deck = list(paths)
        random.shuffle(_sweeper_deck)
    return _sweeper_deck.pop()


def _maybe_sweeper(run):
    """Hook change: a sweeper (every MEDLEY_SWEEPER_EVERY-th change, when
    sweepers are on) and, always, a light transition paired with it."""
    every = max(1, config.MEDLEY_SWEEPER_EVERY)
    run["sweeps"] += 1
    path = None
    paths = _sweeper_paths()
    if paths and not run["sweeps"] % every:
        path = _deal_sweeper(paths)  # still styles the light transition when SFX is off
    if path and state.sfx_enabled:
        try:
            from drivers import deck_orchestrator
            snd = deck_orchestrator.dj_engine.load(path)
            deck_orchestrator.dj_engine._play_at_full("sweeper", snd, 30)
        except Exception as e:
            print(f"[MEDLEY] Sweeper failed: {e}")
    _hook_fx(path)


def _hook_fx(sweeper_path):
    """Marquee + matrix transition for a hook change, styled by the
    sweeper's class (deck_orchestrator._transition_style_for). No dark hold
    like a normal song change -- clear, then straight into the reveal."""
    try:
        from drivers import deck_orchestrator
        from graphics import matrix_canvas
        now = time.time()
        state.transition_style = deck_orchestrator._transition_style_for(sweeper_path)
        state.transition_count += 1
        state.marquee_transition_start = now
        state.marquee_reveal_at = now + config.MARQUEE_TRANSITION_SECONDS
        matrix_canvas.start_reveal_at(state.marquee_reveal_at)
    except Exception as e:
        print(f"[MEDLEY] Hook transition failed: {e}")


def _drop_sweeper():
    """The vocal-drop sweeper: from the medley sweepers (whatever the
    every-Nth setting), falling back to the regular sweepers folder."""
    paths = _sweeper_paths() or glob.glob(os.path.join(config.SWEEPERS_DIR, "*.wav"))
    if not paths or not state.sfx_enabled:
        return
    try:
        from drivers import deck_orchestrator
        p = random.choice(paths)
        deck_orchestrator.dj_engine._play_at_full("sweeper", deck_orchestrator.dj_engine.load(p), 30)
        print(f"[MEDLEY] Vocal-drop sweeper: {os.path.basename(p)}")
    except Exception as e:
        print(f"[MEDLEY] Drop sweeper failed: {e}")


def update(now):
    """Per-frame pump (inputs/gamepad.py::process_events())."""
    run = _run
    if run is None:
        return
    import pygame
    from drivers import deck_orchestrator
    dj = deck_orchestrator.dj_engine
    plan = run["plan"]
    if run.get("error") and not run["started"]:
        _finish(run, f"render error: {run['error']}", handoff=False)
        return
    ch = dj._channels[_deck_name(run["deck"])]

    if run.get("edit"):
        if run["edit"]["playing"] and not ch.get_busy():
            run["edit"]["playing"] = False  # the audition reached its stop point
        return

    if not run["started"]:
        f0 = run["first"]
        with _lock:
            ready = f0 in run["chunks"] and (f0 + 1 in run["chunks"] or f0 == len(plan) - 1)
        if not ready:
            return
        with _lock:
            raw0 = run["chunks"].pop(f0)
        snd0 = pygame.mixer.Sound(buffer=raw0)
        if run["from_deck"] is None:  # resuming after an edit: same deck, no crossfade
            dj.play_deck(_deck_name(run["deck"]), snd0, volume=1.0)
        else:
            deck_from = _deck_name(run["from_deck"])
            dj.play_deck(_deck_name(run["deck"]), snd0, volume=0.0)
            dj.crossfade_decks(deck_from, _deck_name(run["deck"]), config.MEDLEY_ENTRY_FADE_SECONDS)
        run["started"] = True
        run["began_at"] = run["chunk_started_at"] = now
        run["queued"] = f0
        run["played"] = f0
        run["cur_len"] = len(raw0) / 4 / FS
        _set_now_playing(run, f0)
        print(f"[MEDLEY] Started: {f0 + 1}/{len(plan)} {plan[f0]['artist']} - {plan[f0]['title']}")
        return

    # queue the next rendered chunk once the queue slot is free
    if run["queued"] == run["played"] and run["queued"] + 1 < len(plan):
        k = run["queued"] + 1
        with _lock:
            raw = run["chunks"].pop(k, None)
        if raw is not None and ch.get_queue() is None:
            ch.queue(pygame.mixer.Sound(buffer=raw))
            run["queued"] = k
            run["queue_len"] = len(raw) / 4 / FS
    # a chunk boundary passed: the queued sound became the current one
    if run["queued"] > run["played"] and ch.get_queue() is None:
        run["played"] = run["queued"]
        run["chunk_started_at"] = now
        run["cur_len"] = run.get("queue_len", 15.0)
        _set_now_playing(run, run["played"])
        _maybe_sweeper(run)
    # vocal-drop ending: the last question closes (and reveals) when the
    # vocals drop; a couple of bars later the sweeper fires, the deck ducks
    # live and the winner screen goes up. It then HOLDS -- bed, then silence
    # -- until any panel button (end_bed()) returns to normal play.
    last = len(plan) - 1
    drop_at = plan[last].get("drop_at")
    sweep_at = plan[last].get("drop_sweep_at")
    into = now - run["chunk_started_at"]
    if run["played"] == last and drop_at is not None and sweep_at is not None:
        from drivers import medley_game
        if not run.get("drop_closed") and into >= drop_at:
            run["drop_closed"] = True
            medley_game.close_question()
        if not run.get("bed_hold") and into >= sweep_at:
            run["bed_hold"] = True
            state.medley_bed_hold = True
            _drop_sweeper()
            dj.ramp_volume(_deck_name(run["deck"]), 10 ** (config.MEDLEY_DROP_DUCK_DB / 20.0),
                           config.MEDLEY_DROP_DUCK_RAMP_SECONDS)
            medley_game.finish(show_results=True, hold=True)
            print("[MEDLEY] Vocal-drop bed: holding for a panel button.")
        if run.get("bed_hold"):
            return  # no auto-handoff: the host ends the bed
    # hand off to normal Auto-DJ shortly before the last chunk finishes
    if run["played"] == last and not run["handoff_sent"]:
        if now - run["chunk_started_at"] >= run["cur_len"] - config.MEDLEY_HANDOFF_LEAD_SECONDS:
            run["handoff_sent"] = True
            _finish(run, "mixable tracks exhausted -- resuming normal play", handoff=True)
            return
    if run["started"] and not ch.get_busy() and not deck_orchestrator.has_pending_move():
        _finish(run, "playback ended", handoff=True)


# --------------------------------------------------------------------------
# transition editor
#   EDIT TRANSITION pauses the medley and rewinds to the transition between
#   song A (the hook that just played) and song B (the next one). Both songs'
#   start (in) and end (out) can be nudged in beats; every tweak replays the
#   part of the mix it affects and stops. Nothing is saved until RESUME, which
#   saves both songs' edges and continues the medley from B.
# --------------------------------------------------------------------------
def _edit_segments(run):
    """(A', B', C) with the pending edits applied; C is B's successor (or None)."""
    ed = run["edit"]
    plan = run["plan"]
    a0, b0 = plan[ed["a"]], plan[ed["b"]]
    a_seg = _make_seg(a0, a0["native"], a0["tempo"], ed["sa"], ed["ea"])
    b_seg = _make_seg(b0, b0["native"], b0["tempo"], ed["sb"], ed["eb"])
    c_seg = plan[ed["b"] + 1] if ed["b"] + 1 < len(plan) else None
    return a_seg, b_seg, c_seg


def _edit_play(run, scope):
    """Render + play part of the A/B/C mix and stop.
    scope: "a_top"  A from its start -> into B (stops 6s into B)
           "a_exit" A from just before its exit -> into B
           "b_exit" B from just before its exit -> into C (or B's tail if last)"""
    import pygame
    from drivers import deck_orchestrator
    a_seg, b_seg, c_seg = _edit_segments(run)
    lead_n = int(config.MEDLEY_EDIT_LEAD_SECONDS * FS)
    if scope == "b_exit":
        if c_seg is not None:
            c0, carry = render_chunk([b_seg, c_seg], 0, None, first=0)
            c1, _ = render_chunk([b_seg, c_seg], 1, carry, first=0, last_limit=config.MEDLEY_EDIT_POST_SECONDS)
            clip = np.vstack([c0, c1])
        else:
            c0, _ = render_chunk([b_seg], 0, None, first=0)
            clip = c0
        clip = clip[max(0, len(c0) - lead_n):]
    else:
        c0, carry = render_chunk([a_seg, b_seg], 0, None, first=0)
        c1, _ = render_chunk([a_seg, b_seg], 1, carry, first=0, last_limit=config.MEDLEY_EDIT_POST_SECONDS)
        clip = np.vstack([c0, c1])
        if scope == "a_exit":
            clip = clip[max(0, len(c0) - lead_n):]
    snd = pygame.mixer.Sound(buffer=to_int16(clip).tobytes())
    deck_orchestrator.dj_engine._channels[_deck_name(run["deck"])].stop()  # never play over a queued chunk
    deck_orchestrator.dj_engine.play_deck(_deck_name(run["deck"]), snd, volume=1.0)
    run["edit"]["playing"] = True
    run["edit"]["scope"] = scope


def edit_begin():
    run = _run
    if run is None or not run["started"] or run.get("edit"):
        return {"ok": False, "error": "no medley hook is playing"}
    plan = run["plan"]
    i = run["played"]
    elapsed = time.time() - run["chunk_started_at"]
    if i >= 1 and (elapsed < 0.5 * run.get("cur_len", 15.0) or i + 1 >= len(plan)):
        a = i - 1          # the transition just heard: previous hook -> current
    elif i + 1 < len(plan):
        a = i              # late in a hook: the transition coming up
    else:
        return {"ok": False, "error": "nothing to edit yet"}
    run["stop"] = True      # stop the look-ahead renderer
    run["chunks"].clear()
    from drivers import medley_game
    medley_game.pause()     # no scoring while the transition editor is open
    sa, ea = int(plan[a].get("start_beats", 0)), int(plan[a].get("end_beats", 0))
    sb, eb = int(plan[a + 1].get("start_beats", 0)), int(plan[a + 1].get("end_beats", 0))
    run["edit"] = {"a": a, "b": a + 1, "sa": sa, "ea": ea, "sb": sb, "eb": eb, "playing": False}
    try:
        _edit_play(run, "a_top")
    except Exception as e:
        print(f"[MEDLEY] Edit preview failed: {e}")
        run["edit"] = None
        return {"ok": False, "error": "could not render the preview"}
    print(f"[MEDLEY] Edit transition: {plan[a]['artist']} - {plan[a]['title']} -> {plan[a + 1]['title']}")
    return {"ok": True}


_EDIT_FIELDS = {"a_start": ("sa", "a_top"), "a_end": ("ea", "a_exit"),
                "b_start": ("sb", "a_exit"), "b_end": ("eb", "b_exit")}


def edit_adjust(field, delta):
    run = _run
    if run is None or not run.get("edit"):
        return {"ok": False, "error": "not editing"}
    if field not in _EDIT_FIELDS:
        return {"ok": False, "error": "bad field"}
    key, scope = _EDIT_FIELDS[field]
    ed = run["edit"]
    ed[key] = max(-64, min(64, ed[key] + int(delta)))
    _edit_play(run, scope)
    return {"ok": True}


def edit_replay(scope="a_top"):
    """Replay without changing anything: a_top = A -> B, b_exit = B -> next."""
    run = _run
    if run is None or not run.get("edit"):
        return {"ok": False, "error": "not editing"}
    _edit_play(run, scope if scope in ("a_top", "b_exit") else "a_top")
    return {"ok": True}


def _save_override(key, sb, eb):
    """Persist one song's start/end beats (caller holds _lock)."""
    path = os.path.join(_BASE, config.MEDLEY_HOOK_OVERRIDES_FILE)
    data = dict(_load(_hook_ov, config.MEDLEY_HOOK_OVERRIDES_FILE))
    entry = {k: v for k, v in (("start_beats", sb), ("end_beats", eb)) if v}
    if entry:
        data[key] = entry
    else:
        data.pop(key, None)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.replace(tmp, path)
    _hook_ov["data"], _hook_ov["mtime"] = data, os.path.getmtime(path)


def edit_resume(save=True):
    """Leave the editor. save=True persists BOTH songs' start/end beats (and
    applies them to the running plan); the medley then continues from B."""
    global _run
    run = _run
    if run is None or not run.get("edit"):
        return {"ok": False, "error": "not editing"}
    ed = run["edit"]
    plan = run["plan"]
    if save:
        a_seg, b_seg, _ = _edit_segments(run)
        with _lock:
            _save_override(a_seg["key"], ed["sa"], ed["ea"])
            _save_override(b_seg["key"], ed["sb"], ed["eb"])
        plan[ed["a"]], plan[ed["b"]] = a_seg, b_seg
        _last_plan[:] = plan
        print(f"[MEDLEY] Saved: {a_seg['artist']} - {a_seg['title']} (start {ed['sa']:+d}, end {ed['ea']:+d}) | "
              f"{b_seg['artist']} - {b_seg['title']} (start {ed['sb']:+d}, end {ed['eb']:+d}) beats")
    from drivers import deck_orchestrator
    deck_orchestrator.dj_engine._channels[_deck_name(run["deck"])].stop()
    run["stop"] = True
    new = _new_run(plan, run["deck"], None, ed["b"], run["sweeps"])
    _run = new
    threading.Thread(target=_render_worker, args=(new,), daemon=True, name="medley-render").start()
    return {"ok": True}


def _new_run(plan, deck, from_deck, first, sweeps=0):
    return {"plan": plan, "first": first, "chunks": {}, "sounds": {}, "played": first, "queued": -1,
            "stop": False, "started": False, "began_at": 0.0, "chunk_started_at": 0.0, "sweeps": sweeps,
            "deck": deck, "from_deck": from_deck, "handoff_sent": False}


def replay():
    """REPLAY MEDLEY: play the last medley again, in the same order, with every
    saved edit applied -- from the top, whether or not one is running."""
    global _run
    if not _last_plan:
        return {"ok": False, "error": "no medley to replay yet"}
    ov = _load(_hook_ov, config.MEDLEY_HOOK_OVERRIDES_FILE)
    plan = []
    for s in _last_plan:
        o = ov.get(s["key"], {})
        plan.append(_make_seg(s, s["native"], s["tempo"], int(o.get("start_beats", 0)), int(o.get("end_beats", 0))))
    from drivers import deck_orchestrator
    if _run is not None:
        old = _run
        old["stop"] = True
        deck_orchestrator.dj_engine._channels[_deck_name(old["deck"])].stop()
        new = _new_run(plan, old["deck"], None, 0)
    else:
        if state.show_phase != "live" or state.mode != state.MODE_DJ:
            return {"ok": False, "error": "medley needs a live show in DJ mode"}
        if deck_orchestrator.has_pending_move():
            return {"ok": False, "error": "a transition is already in progress"}
        new = _new_run(plan, 2 if state.active_deck == 1 else 1, state.active_deck, 0)
    _last_plan[:] = plan
    _run = new
    from drivers import medley_game
    medley_game.new_game(plan)
    state.medley_active = True
    state.medley_count, state.medley_pos = len(plan), 0
    state.trivia_confirm_active = False
    threading.Thread(target=_render_worker, args=(new,), daemon=True, name="medley-render").start()
    print(f"[MEDLEY] Replaying the last medley ({len(plan)} hooks) with saved edits.")
    return {"ok": True, "tracks": len(plan)}


def status():
    run = _run
    from drivers import medley_game
    st = {"active": run is not None, "sweeper_mode": state.medley_sweeper_mode,
          "game_on": medley_game.enabled(), "game_switch": state.medley_game_enabled,
          "suppressed": state.questions_suppressed,
          "has_last": bool(_last_plan), "last_total": len(_last_plan), "options": options()}
    if run is None:
        return st
    plan = run["plan"]
    i = run["played"] if run["started"] else -1
    remaining = len(plan) - (i + 1) if run["started"] else len(plan) - run["first"]
    cur = plan[i] if i >= 0 else None
    nxt = plan[i + 1] if i + 1 < len(plan) else None
    st.update({
        "starting": not run["started"],
        "total": len(plan), "requested": run.get("requested", len(plan)), "index": i + 1, "remaining": remaining,
        "current": {"title": cur["title"], "artist": cur["artist"], "tempo": round(cur["tempo"], 1)} if cur else None,
        "next": {"title": nxt["title"], "artist": nxt["artist"], "tempo": round(nxt["tempo"], 1)} if nxt else None,
        "next_in_seconds": max(0.0, run.get("cur_len", 0) - (time.time() - run["chunk_started_at"])) if run["started"] else None,
    })
    ed = run.get("edit")
    if ed:
        a_, b_ = plan[ed["a"]], plan[ed["b"]]
        st["edit"] = {"a": {"title": a_["title"], "artist": a_["artist"], "start_beats": ed["sa"], "end_beats": ed["ea"]},
                      "b": {"title": b_["title"], "artist": b_["artist"], "start_beats": ed["sb"], "end_beats": ed["eb"]},
                      "has_c": ed["b"] + 1 < len(plan), "playing": ed["playing"], "scope": ed.get("scope")}
    return st
