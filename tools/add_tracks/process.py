"""process.py <src dir> <staging dir>  (ADD_TRACKS_SPECS=specs2 picks the batch's spec module; default specs1) -- trim dead air, level to TARGET LUFS, 44.1 kHz 256k MP3, write tags.
Reads new_loud.json / new_edges.json beside this script; writes process_report.json."""
import json
import math
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import imageio_ffmpeg
import numpy as np
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3NoHeaderError

import importlib
SPECS = importlib.import_module(os.environ.get("ADD_TRACKS_SPECS", "specs1")).SPECS

HERE = os.path.dirname(os.path.abspath(__file__))
FF = imageio_ffmpeg.get_ffmpeg_exe()
TARGET = -11.5          # LUFS
CEIL_DB = -1.5          # limiter ceiling
MAX_BODY_DB = 2.5
MAX_LIMIT_DB = 8.0      # never shave more than this off a track's peaks; it stays a little quieter instead
LEAD, TAIL = 0.05, 0.25
src, stage = sys.argv[1], sys.argv[2]
loud = json.load(open(os.path.join(HERE, "new_loud.json"), encoding="utf-8"))
edges = json.load(open(os.path.join(HERE, "new_edges.json"), encoding="utf-8"))
names = sorted(loud)


def find(prefix):
    hits = [n for n in names if n.startswith(prefix)]
    assert len(hits) == 1, (prefix, hits)
    return hits[0]


def measure(path):
    r = subprocess.run([FF, "-hide_banner", "-nostats", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = r.stderr[r.stderr.rfind("Summary:"):]
    return float(re.search(r"I:\s+(-?[\d.]+) LUFS", tail).group(1)), float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", tail).group(1))


def encode(inp, outp, start, end, gain):
    af = (f"atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS,aresample=44100,"
          f"afade=t=in:d=0.004,volume={gain:.2f}dB,"
          f"alimiter=limit={10 ** (CEIL_DB / 20):.4f}:attack=5:release=80:level=0")
    subprocess.run([FF, "-v", "error", "-y", "-i", inp, "-af", af, "-ar", "44100", "-ac", "2", "-map_metadata", "-1",
                    "-c:a", "libmp3lame", "-b:a", "256k", outp], check=True)


def one(spec):
    prefix, artist, title, year = spec[:4]
    name = find(prefix)
    L, E = loud[name], edges[name]
    start = max(0.0, E["start"] - LEAD)
    end = min(E["len"], E["end"] + TAIL)
    gain = TARGET - L["I"]
    # Limit by how much of the music gets touched, not by the odd stray peak: the level only 0.1% of
    # samples exceed may go MAX_BODY_DB over the ceiling, the single highest peak MAX_LIMIT_DB over.
    raw = subprocess.run([FF, "-v", "error", "-i", os.path.join(src, name), "-ar", "22050", "-f", "s16le", "-"],
                         capture_output=True).stdout
    p999 = 20 * math.log10(float(max(np.percentile(np.abs(np.frombuffer(raw, np.int16).astype(np.float32)), 99.9), 1.0)) / 32768.0)
    over = max(gain + p999 - CEIL_DB - MAX_BODY_DB + MAX_LIMIT_DB, gain + L["TP"] - CEIL_DB)
    capped = over > MAX_LIMIT_DB
    if capped:
        gain -= over - MAX_LIMIT_DB
    base = re.sub(r"\s+", " ", re.sub(r'[\\/:*?"<>|]', "", f"{artist} - {title}")).strip()
    outp = os.path.join(stage, base + ".mp3")
    encode(os.path.join(src, name), outp, start, end, gain)
    i_out, tp_out = measure(outp)
    want = TARGET if not capped else L["I"] + gain
    if not capped and want - i_out > 0.4:           # the limiter took some loudness back: one correction pass
        extra = min(want - i_out, MAX_LIMIT_DB - max(over, 0.0))
        if extra > 0.2:
            gain += extra
            encode(os.path.join(src, name), outp, start, end, gain)
            i_out, tp_out = measure(outp)
    try:
        tags = EasyID3(outp)
    except ID3NoHeaderError:
        tags = EasyID3()
        tags.save(outp)
        tags = EasyID3(outp)
    tags["title"], tags["artist"], tags["date"] = title, artist, str(year)
    tags.save()
    return base + ".mp3", {"src": name, "artist": artist, "title": title, "year": year, "in_I": L["I"], "in_TP": L["TP"],
                           "gain": round(gain, 2), "limit": round(max(0.0, gain + L["TP"] - CEIL_DB), 1),
                           "out_I": i_out, "out_TP": tp_out, "cut_head": round(start, 2),
                           "cut_tail": round(E["len"] - end, 2), "len": round(end - start, 2)}


os.makedirs(stage, exist_ok=True)
with ThreadPoolExecutor(5) as ex:
    rep = dict(ex.map(one, SPECS))
json.dump(rep, open(os.path.join(HERE, "process_report.json"), "w", encoding="utf-8"), indent=1)
outs = sorted(v["out_I"] for v in rep.values())
print(len(rep), "done; out LUFS min", outs[0], "median", outs[len(outs) // 2], "max", outs[-1])
for n, v in sorted(rep.items(), key=lambda kv: kv[1]["out_I"]):
    print(f"{v['in_I']:6.1f} -> {v['out_I']:6.1f}  gain {v['gain']:+5.1f} limit {v['limit']:3.1f}  head -{v['cut_head']:.2f} tail -{v['cut_tail']:.2f}  {n}")
