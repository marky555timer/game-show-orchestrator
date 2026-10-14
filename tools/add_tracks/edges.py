"""edges.py <dir> <out.json> -- where the audio really starts and ends in each file (dead-air detection)."""
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import imageio_ffmpeg
import numpy as np

FF = imageio_ffmpeg.get_ffmpeg_exe()
SR = 22050
WIN = 441  # 20 ms
d, out = sys.argv[1], sys.argv[2]


def db(x):
    return 20 * np.log10(np.maximum(x, 1e-9))


def one(name):
    raw = subprocess.run([FF, "-v", "error", "-i", os.path.join(d, name), "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                         capture_output=True).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    n = len(x) // WIN
    rms = np.sqrt((x[:n * WIN].reshape(n, WIN) ** 2).mean(axis=1))
    lev = db(rms)
    body = np.percentile(lev, 90)
    # start: first window within 45 dB of the body level (and above -60 dBFS)
    th_in = max(body - 45, -60)
    on = np.nonzero(lev > th_in)[0]
    first, last = int(on[0]), int(on[-1])
    # end: last window above -60 dBFS absolute (keeps fades whole)
    on2 = np.nonzero(lev > -60)[0]
    last = int(on2[-1])
    # longest internal gap (below body-40) -- flags a video with a silent break or a hidden second part
    quiet = lev[first:last] < max(body - 45, -60)
    best = cur = 0
    best_at = 0
    for i, q in enumerate(quiet):
        cur = cur + 1 if q else 0
        if cur > best:
            best, best_at = cur, i - cur + 1
    t = WIN / SR
    return name, {"len": round(len(x) / SR, 2), "start": round(first * t, 2), "end": round((last + 1) * t, 2),
                  "body_db": round(float(body), 1), "gap": round(best * t, 2), "gap_at": round((first + best_at) * t, 1),
                  "head_db": round(float(lev[first:first + 25].max()), 1),
                  "tail_db": [round(float(v), 0) for v in lev[max(0, last - 250):last + 1:50]]}


names = sorted(n for n in os.listdir(d) if n.lower().endswith(".mp3"))
with ThreadPoolExecutor(6) as ex:
    res = dict(ex.map(one, names))
json.dump(res, open(out, "w", encoding="utf-8"), indent=1)
for n, v in res.items():
    m, s = divmod(v["len"], 60)
    print(f"{int(m)}:{s:04.1f} lead {v['start']:5.2f} tail {v['len'] - v['end']:5.2f} gap {v['gap']:4.1f}@{v['gap_at']:5.1f}  {n[:62]}")
