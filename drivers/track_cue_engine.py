"""drivers/track_cue_engine.py
Content-based "cue-out" analysis: where should Auto-DJ START the transition
out of a track, so there's no dead air (trailing silence, endless fade-outs)
but nothing worth hearing (a closing hit, a sung line) gets cut?

analyze(path) decodes the file (ffmpeg via imageio-ffmpeg, mono 8 kHz),
builds a loudness curve, and classifies the ending:

  hard    -- ends near full level (band stops / stinger). Cue right after
             the last loud hit has decayed.
  fade    -- a sustained downward slope. Cue when the level has fallen
             FADE_CUE_DB below the body, capped FADE_CAP_S after the fade
             starts (long fades get stale).
  stinger -- a fade (or quiet stretch) followed by a big hit. Cue after the
             hit decays -- never inside the fade before it.
  natural -- a quiet/natural ending with no fade slope. Cue when it has
             decayed well below the body (reverb tail allowed).

Everything is clamped to the last audible frame, so trailing silence from a
poorly mixed track is never played. Results are cached in
config.TRACK_CUES_FILE (keyed by filename + size + mtime) so the heavy work
happens once, offline; the Pi only reads the JSON.
"""
import json
import os
import subprocess

import numpy as np

SR = 8000
HOP_S = 0.05                    # analysis step
SMOOTH_S = 0.5                  # smoothed loudness curve window
NOISE_FLOOR_DB = -55.0          # absolute "is there sound" floor
BODY_MIN_ABOVE_FLOOR_DB = 45.0  # relative floor below the track's body level
FADE_START_DB = 3.0             # decline starts when smooth level < body - this
LOUD_EVENT_DB = 6.0             # "still playing at full level" = within this of body
FADE_CUE_DB = 12.0              # fade cue: level this far below the body
FADE_CAP_S = 8.0                # fade cue no later than this after the fade starts
FADE_SLOPE_DB_PER_S = -0.5      # regression slope that counts as a fade
FADE_MIN_DROP_DB = 6.0
STINGER_JUMP_DB = 8.0           # hit rises this far above the level just before it
STINGER_LOOKBACK_S = 2.0
NATURAL_CUE_DB = 20.0           # natural ending: cue when this far below the body
HIT_DECAY_DB = 12.0             # after a hit: cue when it has decayed this far
HIT_DECAY_MAX_S = 3.0
PAD_S = 0.3                     # small safety pad after the chosen point


def _ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def decode_mono(path):
    proc = subprocess.run(
        [_ffmpeg(), "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR),
         "-f", "s16le", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def _db_curves(x):
    hop = int(SR * HOP_S)
    n = len(x) // hop
    frames = x[: n * hop].reshape(n, hop)
    power = np.mean(frames ** 2, axis=1) + 1e-12
    fast = 10 * np.log10(power)
    k = max(1, int(SMOOTH_S / HOP_S))
    kernel = np.ones(k) / k
    smooth = 10 * np.log10(np.convolve(power, kernel, mode="same") + 1e-12)
    return fast, smooth


def analyze_samples(x):
    dur = len(x) / SR
    fast, smooth = _db_curves(x)
    n = len(smooth)
    t = np.arange(n) * HOP_S
    if n < 40 or dur < 10:
        return {"duration": round(dur, 2), "cue": round(dur, 2), "kind": "short",
                "body_db": None, "last_audible": round(dur, 2)}

    live = smooth > NOISE_FLOOR_DB
    body = float(np.percentile(smooth[live], 75)) if live.any() else -20.0
    floor = max(NOISE_FLOOR_DB, body - BODY_MIN_ABOVE_FLOOR_DB)
    audible = np.where(smooth > floor)[0]
    last_i = int(audible[-1]) if len(audible) else n - 1
    last_audible = float(t[last_i])

    def first_below(start_i, level, end_i=None):
        end_i = last_i if end_i is None else end_i
        idx = np.where(smooth[start_i:end_i + 1] < level)[0]
        return start_i + int(idx[0]) if len(idx) else end_i

    # Last "still at full level" moment (fast curve so a brief hit counts).
    loud = np.where(fast >= body - LOUD_EVENT_DB)[0]
    loud = loud[loud <= last_i]
    p_i = int(loud[-1]) if len(loud) else last_i
    # Extend to the end of that loud run's local decay (so a hit is heard out).
    after_hit = last_audible - t[p_i]

    # Was P a stinger: a real quiet stretch just before it, then a jump up?
    lb = max(0, p_i - int(STINGER_LOOKBACK_S / HOP_S))
    stinger = False
    if p_i > int(4 / HOP_S):
        before = float(np.median(smooth[max(0, lb - int(1.0 / HOP_S)):lb + 1]))
        stinger = (fast[p_i] - before >= STINGER_JUMP_DB
                   and before < body - FADE_CUE_DB + 4
                   and t[p_i] > dur * 0.5)

    # Fade start F: last time the smooth level was within FADE_START_DB of body.
    near = np.where(smooth[:last_i + 1] >= body - FADE_START_DB)[0]
    f_i = int(near[-1]) if len(near) else last_i
    seg = slice(f_i, min(last_i + 1, f_i + int(6.0 / HOP_S)))
    seg_t, seg_y = t[seg], smooth[seg]
    slope = float(np.polyfit(seg_t, seg_y, 1)[0]) if len(seg_t) > 5 else 0.0
    drop = body - float(smooth[last_i])
    is_fade = (slope <= FADE_SLOPE_DB_PER_S and drop >= FADE_MIN_DROP_DB
               and last_audible - t[f_i] > 2.0)

    if stinger:
        kind = "stinger"
        d_i = first_below(p_i, body - HIT_DECAY_DB)
        cue = min(float(t[d_i]), t[p_i] + HIT_DECAY_MAX_S)
    elif after_hit <= 2.5 and not is_fade:
        kind = "hard"
        d_i = first_below(p_i, body - HIT_DECAY_DB)
        cue = min(float(t[d_i]), t[p_i] + HIT_DECAY_MAX_S)
    elif is_fade:
        kind = "fade"
        c_i = first_below(f_i, body - FADE_CUE_DB)
        cue = min(float(t[c_i]), float(t[f_i]) + FADE_CAP_S)
    else:
        kind = "natural"
        n_i = np.where(smooth[:last_i + 1] >= body - NATURAL_CUE_DB)[0]
        cue = float(t[int(n_i[-1])]) if len(n_i) else last_audible

    cue = min(cue + PAD_S, last_audible)
    cue = max(cue, dur * 0.3)   # sanity: never a wildly early cue
    return {
        "duration": round(dur, 2),
        "cue": round(float(cue), 2),
        "kind": kind,
        "body_db": round(body, 1),
        "last_audible": round(last_audible, 2),
        "fade_start": round(float(t[f_i]), 2),
        "slope": round(slope, 2),
    }


def analyze(path):
    return analyze_samples(decode_mono(path))


# ----- cache -------------------------------------------------------------

def _sig(path):
    st = os.stat(path)
    return f"{st.st_size}:{int(st.st_mtime)}"


def load_cache(cache_path):
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(cache_path, cache):
    tmp = cache_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=1, sort_keys=True)
    os.replace(tmp, cache_path)


def analyze_cached(path, cache):
    key = os.path.basename(path)
    sig = _sig(path)
    hit = cache.get(key)
    if hit and hit.get("sig") == sig:
        return hit
    res = analyze(path)
    res["sig"] = sig
    cache[key] = res
    return res
