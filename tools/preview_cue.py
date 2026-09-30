"""Render an audition clip of a track's ending with a beep at the chosen
cue-out (where the crossfade will begin). Usage from the repo root:

    python tools/preview_cue.py "doo wop" [--pre 18] [--post 8] [--out DIR]
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import imageio_ffmpeg  # noqa: E402

MUSIC = os.path.join(ROOT, "audio", "music")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--pre", type=float, default=18.0)
    ap.add_argument("--post", type=float, default=8.0)
    ap.add_argument("--out", default=os.path.join(ROOT, "cue_previews"))
    a = ap.parse_args()
    cues = json.load(open(os.path.join(ROOT, "track_cues.json"), encoding="utf-8"))
    os.makedirs(a.out, exist_ok=True)
    for f in sorted(cues):
        if a.name.lower() not in f.lower() or "cue" not in cues[f]:
            continue
        c = cues[f]
        start = max(0.0, c["cue"] - a.pre)
        beep_at = c["cue"] - start
        out = os.path.join(a.out, f"{os.path.splitext(f)[0]}__{c['kind']}_cue{c['cue']:.0f}.mp3")
        flt = (f"[1:a]adelay={int(beep_at * 1000)}|{int(beep_at * 1000)},volume=0.6[b];"
               f"[0:a][b]amix=inputs=2:duration=first:normalize=0")
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-v", "error",
                        "-ss", f"{start:.2f}", "-t", f"{a.pre + a.post:.2f}",
                        "-i", os.path.join(MUSIC, f),
                        "-f", "lavfi", "-i", "sine=frequency=1000:duration=0.2",
                        "-filter_complex", flt, out], check=True)
        print(f"{c['kind']:8} dur {c['duration']:.1f}s -> cue {c['cue']:.1f}s  {out}")


if __name__ == "__main__":
    main()
