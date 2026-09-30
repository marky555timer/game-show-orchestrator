"""Render a sample Dance Medley to an MP3 (same planner + mixer the live show
uses) so it can be auditioned before going live.

    python tools/render_medley.py [--metadata path/to/music_metadata.csv]
                                  [--seed N] [--start-bpm 120] [--tracks 8]
                                  [--out medley_preview.mp3]
"""
import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metadata", default="")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--start-bpm", type=float, default=0)
    ap.add_argument("--tracks", type=int, default=8)
    ap.add_argument("--out", default=os.path.join(ROOT, "medley_preview.mp3"))
    a = ap.parse_args()
    import config
    if a.metadata:
        config.MUSIC_METADATA_PATH = a.metadata
    import numpy as np
    from drivers import medley_engine as me
    plan = me.build_plan(a.start_bpm or None, seed=a.seed)[:a.tracks]
    if not plan:
        print("no plan (not enough eligible tracks)"); return
    print(f"{len(plan)} hooks:")
    for i, s in enumerate(plan, 1):
        print(f"  {i:2d}. {s['artist'][:22]:22s} - {s['title'][:30]:30s} tempo {s['tempo']:6.1f} (native {s['native']:6.1f}, "
              f"{(s['rate']-1)*100:+4.1f}%) {s['length']:5.1f}s {s['key_cam']} energy {s['energy']}")
    carry, chunks = None, []
    for i in range(len(plan)):
        c, carry = me.render_chunk(plan, i, carry)
        chunks.append(c)
    pcm = me.to_int16(np.vstack(chunks))
    p = subprocess.run([me._ffmpeg(), "-y", "-v", "error", "-f", "s16le", "-ar", str(me.FS), "-ac", "2", "-i", "-",
                        "-b:a", "192k", a.out], input=pcm.tobytes(), stderr=subprocess.PIPE)
    print("wrote", a.out, f"({len(pcm)/me.FS:.0f}s)" if p.returncode == 0 else p.stderr.decode()[:200])


if __name__ == "__main__":
    main()
