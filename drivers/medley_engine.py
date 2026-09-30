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
"all" (medley + the regular sweepers folder).
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


def eligible_candidates():
    """Floor-filler tracks with a trustworthy beat grid, as dicts:
    {path, name, title, artist, bpm, key_cam, hook, energy, duration}."""
    from drivers.music_library import music_library, sanitize_track_key
    from drivers import music_metadata_engine as mme
    analysis = _load(_analysis, config.MEDLEY_ANALYSIS_FILE)
    ov = _load(_overrides, config.MEDLEY_CANDIDATES_FILE)
    include = {str(x).lower() for x in ov.get("include", [])}
    exclude = {str(x).lower() for x in ov.get("exclude", [])}
    out = []
    for t in music_library.all_tracks():
        name = os.path.basename(t["path"])
        a = analysis.get(name)
        if not a or "bpm" not in a or not a.get("hooks"):
            continue
        key = sanitize_track_key(t["title"], t["artist"])
        if name.lower() in exclude or key in exclude:
            continue
        forced = name.lower() in include or key in include
        meta = mme.get_metadata_for(key) or {}
        energy = int(meta.get("energy_rank") or 0)
        if not forced:
            if a["grid_conf"] < config.MEDLEY_MIN_GRID_CONF:
                continue
            if energy < config.MEDLEY_MIN_ENERGY:
                continue
            if meta.get("slow_dance") is True:
                continue
            if meta.get("genre") in config.MEDLEY_EXCLUDE_GENRES:
                continue
            if state.show_exclude_explicit and meta.get("explicit") is True:
                continue
        local_bpm = a["hooks"][0].get("bpm", a["bpm"])
        if not _tempo_options(local_bpm):
            continue
        out.append({
            "path": t["path"], "name": name, "title": t["title"], "artist": t["artist"],
            "bpm": local_bpm, "key_cam": a.get("camelot"), "hook": a["hooks"][0],
            "hooks": a["hooks"], "energy": energy, "duration": a["duration"],
            "key": key,
        })
    return out


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


def build_plan(start_bpm=None, seed=None, first_name=None):
    """Greedy tempo/key chain over the eligible pool. Returns a list of
    segment dicts (see render_chunk) -- empty if there aren't enough."""
    rng = random.Random(seed)
    pool = eligible_candidates()
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
    plan, used, total = [], set(), 0.0
    used_keys = set()

    def add(c, native, tempo):
        nonlocal total
        ov = _load(_hook_ov, config.MEDLEY_HOOK_OVERRIDES_FILE).get(c["key"], {})
        seg = _make_seg(c, native, tempo, int(ov.get("start_beats", 0)), int(ov.get("end_beats", 0)))
        plan.append(seg)
        used.add(c["name"])
        used_keys.add(c["key"])
        total += seg["length"]

    n0 = min(_tempo_options(first["bpm"]), key=lambda n: abs(n - (start_bpm or 118.0)))
    add(first, n0, n0)
    while total < config.MEDLEY_MAX_SECONDS and len(plan) < config.MEDLEY_MAX_TRACKS:
        cur = plan[-1]
        scored = []
        for c in pool:
            if c["name"] in used or c["key"] in used_keys or c["artist"].lower() == cur["artist"].lower():
                continue
            f = _feasible(cur["tempo"], c["bpm"])
            if not f:
                continue
            tempo_fit, n, nt = f
            key_pen = 0.08 * min(3, _camelot_distance(cur["key_cam"], c["key_cam"]))
            energy_bonus = -0.02 * c["energy"]
            score = tempo_fit + key_pen + energy_bonus + rng.random() * 0.06
            scored.append((score, c, n, nt))
        if not scored:
            break
        scored.sort(key=lambda s: s[0])
        _, c, n, nt = scored[0]
        add(c, n, nt)
    return plan if len(plan) >= config.MEDLEY_MIN_TRACKS else []


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


def _seg_window(seg, xf_in_s, tail_extra_s=0.0):
    """Decode + resample one segment's playback audio from (hook start -
    xf_in) to (hook end + tail_extra). Returns (audio, pre_samples) where
    pre_samples = number of leading pre-roll samples."""
    r = seg["rate"]
    pre_native = xf_in_s * r
    body_native = seg["length"] * r
    tail_native = tail_extra_s * r
    start = seg["hook_start"] - pre_native
    a = _decode_window(seg["path"], start - 0.05, pre_native + body_native + tail_native + 0.15)
    a = a[int(0.05 * FS):]
    y = _resample(a, r)
    pre = int(round(xf_in_s * FS))
    # level-match: each hook's body is brought toward a common RMS (bounded)
    body = y[pre:pre + int(round(seg["length"] * FS))]
    if len(body):
        rms = float(np.sqrt(np.mean(body ** 2)))
        if rms > 1e-4:
            y = y * float(np.clip(config.MEDLEY_TARGET_RMS / rms, 0.7, 1.5))
    return y, pre


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
    tail_extra = config.MEDLEY_LAST_TAIL_SECONDS if last and not last_limit else 0.0
    y, pre = _seg_window(seg, xf_in, tail_extra)
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
    plan = build_plan(start_bpm)
    if not plan:
        return {"ok": False, "error": "not enough mixable floor-filler tracks"}
    run = {"plan": plan, "first": 0, "chunks": {}, "sounds": {}, "played": 0, "queued": -1, "stop": False,
           "started": False, "began_at": 0.0, "chunk_started_at": 0.0, "sweeps": 0,
           "deck": 2 if state.active_deck == 1 else 1, "from_deck": state.active_deck,
           "handoff_sent": False}
    _run = run
    _last_plan[:] = plan
    from drivers import medley_game
    medley_game.new_game(plan)
    state.medley_active = True
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
    state.now_playing_path = seg["path"]
    state.now_playing_duration = seg["duration"]
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


def _maybe_sweeper(run):
    every = max(1, config.MEDLEY_SWEEPER_EVERY)
    run["sweeps"] += 1
    if run["sweeps"] % every:
        return
    paths = _sweeper_paths()
    if not paths:
        return
    try:
        from drivers import deck_orchestrator
        snd = deck_orchestrator.dj_engine.load(random.choice(paths))
        deck_orchestrator.dj_engine._play_at_full("sweeper", snd, 30)
    except Exception as e:
        print(f"[MEDLEY] Sweeper failed: {e}")


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
    # hand off to normal Auto-DJ shortly before the last chunk finishes
    last = len(plan) - 1
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
          "has_last": bool(_last_plan), "last_total": len(_last_plan)}
    if run is None:
        return st
    plan = run["plan"]
    i = run["played"] if run["started"] else -1
    remaining = len(plan) - (i + 1) if run["started"] else len(plan) - run["first"]
    cur = plan[i] if i >= 0 else None
    nxt = plan[i + 1] if i + 1 < len(plan) else None
    st.update({
        "starting": not run["started"],
        "total": len(plan), "index": i + 1, "remaining": remaining,
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
