"""drivers/medley_analysis.py
Offline analysis for Dance Medley mode (numpy + ffmpeg only, like
track_cue_engine.py): per track it finds the tempo, a constant beat grid,
the bar phase, a musical key, and the best short "hook" (chorus / drop)
windows -- everything the medley planner/mixer needs, computed once and
cached in medley_analysis.json.

Dance / floor-filler music sits on a steady grid, so a single global BPM +
phase (refined against the whole track's onset curve) is accurate; tracks
whose grid doesn't fit well come back with a low `grid_conf` and the planner
skips them.
"""
import json
import os
import subprocess

import numpy as np

SR = 22050
N_FFT = 1024
HOP = 256
HOP_S = HOP / SR
BPM_MIN, BPM_MAX = 70.0, 190.0
PRIOR_CENTER_BPM = 122.0

# Krumhansl-Kessler key profiles (major / minor), rotated per tonic.
_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
_NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# Camelot wheel numbers: major -> B side, minor -> A side.
_CAMELOT_MAJOR = {"C": 8, "G": 9, "D": 10, "A": 11, "E": 12, "B": 1, "F#": 2, "C#": 3, "G#": 4, "D#": 5, "A#": 6, "F": 7}
_CAMELOT_MINOR = {"A": 8, "E": 9, "B": 10, "F#": 11, "C#": 12, "G#": 1, "D#": 2, "A#": 3, "F": 4, "C": 5, "G": 6, "D": 7}


def _ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def decode(path, sr=SR):
    proc = subprocess.run(
        [_ffmpeg(), "-v", "error", "-i", path, "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def _stft_mag(x):
    n = 1 + (len(x) - N_FFT) // HOP
    if n < 8:
        return np.zeros((0, N_FFT // 2 + 1), dtype=np.float32)
    win = np.hanning(N_FFT).astype(np.float32)
    out = np.empty((n, N_FFT // 2 + 1), dtype=np.float32)
    step = 2048
    for a in range(0, n, step):
        b = min(n, a + step)
        idx = np.arange(a, b)[:, None] * HOP + np.arange(N_FFT)[None, :]
        out[a:b] = np.abs(np.fft.rfft(x[idx] * win, axis=1))
    return out


def _onset_env(mag):
    comp = np.log1p(mag * 20.0)
    diff = np.maximum(0.0, comp[1:] - comp[:-1])
    env = diff.sum(axis=1)
    env = np.concatenate([[0.0], env])
    k = np.array([0.25, 0.5, 0.25])
    env = np.convolve(env, k, mode="same")
    env -= env.mean()
    return env, comp


def _interp(a, idx):
    i0 = np.floor(idx).astype(np.int64)
    fr = idx - i0
    i0 = np.clip(i0, 0, len(a) - 2)
    return a[i0] * (1 - fr) + a[i0 + 1] * fr


def _comb_score(env, period, n_phases=96):
    """Best comb sum over phase for a given period (frames). Returns
    (score, phase_frames)."""
    n = len(env)
    k = int((n - 2) // period)
    if k < 8:
        return -1e9, 0.0
    phases = np.linspace(0, period, n_phases, endpoint=False)
    idx = phases[:, None] + period * np.arange(k)[None, :]
    idx = np.minimum(idx, n - 2)
    sc = _interp(env, idx).sum(axis=1)
    j = int(np.argmax(sc))
    return float(sc[j]) / k, float(phases[j])


def find_grid(env):
    """Global BPM + beat phase (seconds) + confidence from the onset curve."""
    n = len(env)
    lo = int(np.floor(60.0 / (BPM_MAX * HOP_S)))
    hi = int(np.ceil(60.0 / (BPM_MIN * HOP_S)))
    f = np.fft.rfft(env, 2 * n)
    ac = np.fft.irfft(f * np.conj(f))[:n]
    ac = ac / (ac[0] + 1e-9)
    best_l, best = None, -1e9
    for lag in range(lo, hi + 1):
        bpm = 60.0 / (lag * HOP_S)
        s = ac[lag]
        if 2 * lag < n:
            s += 0.5 * ac[2 * lag]
        if 4 * lag < n:
            s += 0.25 * ac[4 * lag]
        prior = np.exp(-0.5 * (np.log2(bpm / PRIOR_CENTER_BPM) / 0.55) ** 2)
        s *= prior
        if s > best:
            best, best_l = s, lag
    # refine the period against the whole track's comb (coarse -> fine)
    p0 = float(best_l)
    cands = p0 * (1 + np.linspace(-0.04, 0.04, 161))
    scores = [(_comb_score(env, p)[0], p) for p in cands]
    _, p1 = max(scores)
    cands = p1 * (1 + np.linspace(-0.004, 0.004, 81))
    scores = [(_comb_score(env, p, 128) + (p,)) for p in cands]
    score, phase, period = max(scores)
    _, phase = _comb_score(env, period, 384)
    bpm = 60.0 / (period * HOP_S)
    # confidence: comb score vs. the average comb at off-grid periods
    base = np.mean([_comb_score(env, period * r)[0] for r in (0.61, 0.73, 0.87, 1.13, 1.31, 1.57)])
    conf = float(score / (abs(base) + 1e-6)) if base > 0 else float(score / (np.std(env) + 1e-6))
    return bpm, phase * HOP_S + (N_FFT / 2) / SR, conf, period * HOP_S


def _fix_offbeat(mag, bpm, phase):
    """The onset comb can lock onto the off-beat hi-hats (half a beat late).
    Compare KICK (low-band) onset strength on the grid vs. half a beat away
    and shift the phase if the off-beat wins."""
    low = np.log1p(mag[:, :12] * 20.0).sum(axis=1)
    low = np.maximum(0.0, np.concatenate([[0.0], np.diff(low)]))
    period = 60.0 / bpm
    dur = len(low) * HOP_S

    def strength(ph):
        t = ph % period + period * np.arange(int((dur - 1 - ph % period) // period))
        idx = np.clip(((t * SR - N_FFT / 2) / HOP).astype(int), 0, len(low) - 1)
        # take the max within +-1 frame so small timing error doesn't matter
        v = np.maximum(np.maximum(low[np.clip(idx - 1, 0, len(low) - 1)], low[idx]),
                       low[np.clip(idx + 1, 0, len(low) - 1)])
        return float(v.mean())

    on, off = strength(phase), strength(phase + period / 2)
    return (phase + period / 2) if off > on * 1.10 else phase


def _refine_hook(low_env, full_env, bpm, start, bars):
    """Re-fit the beat grid LOCALLY around a hook (kick-weighted): a single
    global tempo drifts by the time a mid-song hook is reached, or the track's
    drummer isn't a machine. Returns (start, local_bpm): the hook start moved
    by less than half a beat onto the local kick grid, and the local tempo."""
    period0 = 60.0 / bpm
    a = max(0, int((start - 4.0) / HOP_S))
    b = min(len(low_env) - 2, int((start + bars * 4 * period0 + 1.0) / HOP_S))
    if b - a < 200:
        return start, bpm
    low = low_env[a:b] / (low_env[a:b].std() + 1e-9)
    full = full_env[a:b] / (full_env[a:b].std() + 1e-9)
    seg = low + 0.3 * full
    best = (-1e9, None, None)
    for scale in 1 + np.linspace(-0.006, 0.006, 25):
        p = period0 * scale / HOP_S
        k = int((len(seg) - 2) // p)
        if k < 8:
            continue
        phases = np.linspace(0, p, 160, endpoint=False)
        idx = np.minimum(phases[:, None] + p * np.arange(k)[None, :], len(seg) - 2)
        sc = _interp(seg, idx).sum(axis=1) / k
        j = int(np.argmax(sc))
        if sc[j] > best[0]:
            best = (float(sc[j]), float(phases[j]), float(p))
    if best[1] is None:
        return start, bpm
    _, ph, p = best
    p_s = p * HOP_S
    t0 = (a + ph) * HOP_S + (N_FFT / 2) / SR
    delta = (t0 - start + p_s / 2) % p_s - p_s / 2
    return start + delta, 60.0 / p_s


def _beat_times(bpm, phase, dur):
    period = 60.0 / bpm
    first = phase % period
    return first + period * np.arange(int((dur - first) // period))


def _downbeat(mag, beats):
    """Which of 4 beats per bar is the downbeat, from low-band (kick) energy."""
    low = mag[:, 1:9].sum(axis=1)  # ~ <350 Hz
    idx = np.clip(((beats * SR - N_FFT / 2) / HOP).astype(int), 0, len(low) - 1)
    strength = low[idx]
    if len(strength) < 16:
        return 0
    sums = [strength[d::4].mean() for d in range(4)]
    return int(np.argmax(sums))


def _key(x, t0, t1):
    """Key from a fine-resolution chroma of x[t0:t1] (8192-pt FFT so bass
    notes resolve to the right pitch class)."""
    seg = x[int(max(0, t0) * SR):int(t1 * SR)]
    n_fft = 8192
    if len(seg) < n_fft * 2:
        return None
    win = np.hanning(n_fft).astype(np.float32)
    n = 1 + (len(seg) - n_fft) // 4096
    freqs = np.fft.rfftfreq(n_fft, 1.0 / SR)
    ok = (freqs >= 110) & (freqs <= 1800)
    pcs = np.round(12 * np.log2(freqs[ok] / 440.0) + 69).astype(int) % 12
    acc = np.zeros(ok.sum())
    for k in range(n):
        frame = seg[k * 4096:k * 4096 + n_fft] * win
        acc += np.abs(np.fft.rfft(frame))[ok] ** 2
    chroma = np.array([acc[pcs == pc].sum() for pc in range(12)])
    chroma = np.sqrt(chroma)
    if chroma.sum() <= 0:
        return None
    best = (-2, None)
    for tonic in range(12):
        for prof, mode in ((_MAJOR, "maj"), (_MINOR, "min")):
            r = np.corrcoef(chroma, np.roll(prof, tonic))[0, 1]
            if r > best[0]:
                best = (r, (tonic, mode))
    r, (tonic, mode) = best
    name = _NOTES[tonic]
    cam = (_CAMELOT_MAJOR if mode == "maj" else _CAMELOT_MINOR)[name]
    return {"key": f"{name} {mode}", "camelot": f"{cam}{'B' if mode == 'maj' else 'A'}", "key_conf": round(float(r), 2)}


def _hooks(x, bpm, beats, downbeat_i, target_s=15.0, max_s=24.0, n_best=3):
    """Best hook windows as bar-aligned (start_s, bars, score). Energy per
    beat -> per bar; a window scores its mean loudness plus a bonus for
    jumping up from the phrase before it (a drop / chorus entry)."""
    period = 60.0 / bpm
    bar_s = 4 * period
    beat_e = []
    for t in beats:
        a = int(t * SR)
        b = int((t + period) * SR)
        seg = x[a:b]
        beat_e.append(float(np.sqrt(np.mean(seg ** 2))) if len(seg) > 8 else 0.0)
    beat_e = np.array(beat_e)
    nb = (len(beat_e) - downbeat_i) // 4
    if nb < 12:
        return []
    bars = beat_e[downbeat_i:downbeat_i + nb * 4].reshape(nb, 4).mean(axis=1)
    bars_db = 20 * np.log10(bars + 1e-6)
    W = int(round(target_s / bar_s))
    W = max(4, min(W, int(max_s // bar_s)))
    if W % 2:
        W += 1 if (W + 1) * bar_s <= max_s else -1
    body = np.percentile(bars_db, 75)
    scored = []
    for j in range(int(nb * 0.06), nb - W - int(nb * 0.06)):
        win = bars_db[j:j + W]
        prev = bars_db[max(0, j - W):j]
        loud = win.mean()
        jump = max(0.0, loud - (prev.mean() if len(prev) else loud))
        steady = -0.25 * win.std()
        phrase = 0.4 if (j % 4 == 0) else 0.0
        early = 0.0 if nb < 20 else -0.3 * (j / nb)  # slight preference for the first strong chorus
        score = loud + 0.5 * jump + steady + phrase + early - 0.6 * max(0.0, body - loud)
        scored.append((score, j))
    scored.sort(reverse=True)
    out = []
    for score, j in scored:
        if all(abs(j - o[0]) >= W for o in out):
            start = beats[downbeat_i + j * 4]
            out.append((j, start, W, score))
        if len(out) >= n_best:
            break
    return [{"start": round(float(s), 3), "bars": int(w), "score": round(float(sc), 2)}
            for (_, s, w, sc) in out]


def analyze(path):
    x = decode(path)
    dur = len(x) / SR
    if dur < 45:
        return {"duration": round(dur, 2), "error": "too short"}
    mag = _stft_mag(x)
    env, _ = _onset_env(mag)
    bpm, phase, conf, _ = find_grid(env)
    phase = _fix_offbeat(mag, bpm, phase)
    beats = _beat_times(bpm, phase, dur)
    d_i = _downbeat(mag, beats)
    hooks = _hooks(x, bpm, beats, d_i)
    low = np.log1p(mag[:, :12] * 20.0).sum(axis=1)
    low_env = np.maximum(0.0, np.concatenate([[0.0], np.diff(low)]))
    for h in hooks:
        s, lb = _refine_hook(low_env, env, bpm, h["start"], h["bars"])
        h["start"], h["bpm"] = round(float(s), 3), round(float(lb), 3)
    res = {"duration": round(dur, 2), "bpm": round(float(bpm), 3), "beat_phase": round(float(phase % (60 / bpm)), 4),
           "downbeat": int(d_i), "grid_conf": round(conf, 2), "hooks": hooks}
    if hooks:
        h = hooks[0]
        k = _key(x, h["start"], h["start"] + 24)
        if k:
            res.update(k)
    return res


def _sig(path):
    st = os.stat(path)
    return f"{st.st_size}:{int(st.st_mtime)}"


def load_cache(p):
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(p, cache):
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=1, sort_keys=True)
    os.replace(tmp, p)
