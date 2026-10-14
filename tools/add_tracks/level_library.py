"""level_library.py <loudness.json from measure.py> <stage dir> -- bring already-added library tracks to the
same level as new ones (see process.py). No trimming: every saved time (cue-outs, medley clips, hook words)
must stay valid. Tracks already within SKIP_DB of the target at 44.1 kHz are left untouched.
Writes <stage dir>/level_report.json; the swap into audio/music is a separate step."""
import json
import math
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import imageio_ffmpeg
import numpy as np
from mutagen.id3 import ID3, ID3NoHeaderError

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MUSIC = os.path.join(ROOT, "audio", "music")
FF = imageio_ffmpeg.get_ffmpeg_exe()
TARGET = -11.5
CEIL_DB = -1.5
MAX_BODY_DB = 2.5       # the level only 0.1% of samples exceed may go this far over the ceiling
MAX_LIMIT_DB = 8.0      # the single highest peak may go this far over
SKIP_DB = 1.0
loud = json.load(open(sys.argv[1], encoding="utf-8"))
stage = sys.argv[2]


def measure(path):
    r = subprocess.run([FF, "-hide_banner", "-nostats", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = r.stderr[r.stderr.rfind("Summary:"):]
    return float(re.search(r"I:\s+(-?[\d.]+) LUFS", tail).group(1)), float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", tail).group(1))


def encode(inp, outp, gain):
    af = f"aresample=44100,volume={gain:.2f}dB,alimiter=limit={10 ** (CEIL_DB / 20):.4f}:attack=5:release=80:level=0"
    subprocess.run([FF, "-v", "error", "-y", "-i", inp, "-vn", "-af", af, "-ar", "44100", "-ac", "2", "-map_metadata", "-1",
                    "-c:a", "libmp3lame", "-b:a", "256k", outp], check=True)


def one(name):
    L = loud[name]
    src = os.path.join(MUSIC, name)
    if L.get("I") is None or not name.lower().endswith(".mp3") or not os.path.exists(src):
        return name, {"skip": "not measured / not mp3 / missing"}
    if abs(TARGET - L["I"]) <= SKIP_DB and L.get("sr") == 44100:
        return name, {"skip": "already level", "in_I": L["I"]}
    gain = TARGET - L["I"]
    raw = subprocess.run([FF, "-v", "error", "-i", src, "-ar", "22050", "-f", "s16le", "-"], capture_output=True).stdout
    p999 = 20 * math.log10(float(max(np.percentile(np.abs(np.frombuffer(raw, np.int16).astype(np.float32)), 99.9), 1.0)) / 32768.0)
    over = max(gain + p999 - CEIL_DB - MAX_BODY_DB + MAX_LIMIT_DB, gain + L["TP"] - CEIL_DB)
    capped = over > MAX_LIMIT_DB
    if capped:
        gain -= over - MAX_LIMIT_DB
        if abs(gain) < 0.5 and L.get("sr") == 44100:
            return name, {"skip": "too dynamic to raise", "in_I": L["I"]}
    outp = os.path.join(stage, name)
    encode(src, outp, gain)
    i_out, tp_out = measure(outp)
    if not capped and TARGET - i_out > 0.4:         # the limiter took some loudness back: one correction pass
        extra = min(TARGET - i_out, MAX_LIMIT_DB - max(over, 0.0))
        if extra > 0.2:
            gain += extra
            encode(src, outp, gain)
            i_out, tp_out = measure(outp)
    try:
        ID3(src).save(outp, v2_version=3)           # every original tag, cover art included
    except ID3NoHeaderError:
        pass
    return name, {"in_I": L["I"], "gain": round(gain, 2), "out_I": i_out, "out_TP": tp_out, "capped": capped}


os.makedirs(stage, exist_ok=True)
with ThreadPoolExecutor(4) as ex:
    rep = dict(ex.map(one, sorted(loud)))
json.dump(rep, open(os.path.join(stage, "level_report.json"), "w", encoding="utf-8"), indent=1)
done = {n: v for n, v in rep.items() if "out_I" in v}
outs = sorted(v["out_I"] for v in done.values())
print(len(done), "levelled,", sum("skip" in v for v in rep.values()), "left alone")
print("out LUFS min", outs[0], "p5", outs[len(outs) // 20], "median", outs[len(outs) // 2], "max", outs[-1])
for n, v in sorted(done.items(), key=lambda kv: kv[1]["out_I"])[:15]:
    print(f"{v['in_I']:6.1f} -> {v['out_I']:6.1f}  gain {v['gain']:+5.1f}  {n}")
