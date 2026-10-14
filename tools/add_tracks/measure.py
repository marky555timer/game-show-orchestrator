"""measure.py <dir> <out.json> -- integrated loudness (LUFS), true peak, format of every audio file."""
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import imageio_ffmpeg
import mutagen

FF = imageio_ffmpeg.get_ffmpeg_exe()
d, out = sys.argv[1], sys.argv[2]


def one(name):
    p = os.path.join(d, name)
    r = subprocess.run([FF, "-hide_banner", "-nostats", "-i", p, "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = r.stderr[r.stderr.rfind("Summary:"):]
    i = re.search(r"I:\s+(-?[\d.]+) LUFS", tail)
    lra = re.search(r"LRA:\s+(-?[\d.]+) LU", tail)
    pk = re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", tail)
    info = {}
    try:
        f = mutagen.File(p, easy=True)
        info = {"sr": f.info.sample_rate, "br": getattr(f.info, "bitrate", 0), "ch": getattr(f.info, "channels", 0),
                "len": round(f.info.length, 2), "title": (f.get("title") or [""])[0], "artist": (f.get("artist") or [""])[0]}
    except Exception as e:
        info = {"err": str(e)}
    return name, dict(info, I=float(i.group(1)) if i else None, LRA=float(lra.group(1)) if lra else None,
                      TP=(float(pk.group(1)) if pk and pk.group(1) != "-inf" else None))


names = sorted(n for n in os.listdir(d) if n.lower().endswith((".mp3", ".wav", ".flac", ".m4a", ".ogg")))
with ThreadPoolExecutor(6) as ex:
    res = dict(ex.map(one, names))
json.dump(res, open(out, "w", encoding="utf-8"), indent=1)
print(len(res), "files")
