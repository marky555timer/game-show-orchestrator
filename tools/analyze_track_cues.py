"""Batch-analyze audio/music with drivers/track_cue_engine and write
track_cues.json (used by Auto-DJ) plus a summary. Run from the repo root:

    python tools/analyze_track_cues.py            # whole library, 8 procs
    python tools/analyze_track_cues.py --force    # ignore the cache
    python tools/analyze_track_cues.py "cake"     # only names containing 'cake'
"""
import argparse
import os
import sys
from concurrent.futures import ProcessPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from drivers import track_cue_engine as tce  # noqa: E402

MUSIC = os.path.join(ROOT, "audio", "music")
OUT = os.path.join(ROOT, "track_cues.json")


def _work(path):
    try:
        res = tce.analyze(path)
        res["sig"] = tce._sig(path)
        return os.path.basename(path), res
    except Exception as e:
        return os.path.basename(path), {"error": str(e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("filter", nargs="?", default="")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    cache = tce.load_cache(OUT)
    files = sorted(f for f in os.listdir(MUSIC)
                   if f.lower().endswith(".mp3") and a.filter.lower() in f.lower())
    todo = [os.path.join(MUSIC, f) for f in files
            if a.force or cache.get(f, {}).get("sig") != tce._sig(os.path.join(MUSIC, f))]
    print(f"{len(files)} tracks, {len(todo)} to analyze")
    with ProcessPoolExecutor() as ex:
        for i, (name, res) in enumerate(ex.map(_work, todo, chunksize=4), 1):
            cache[name] = res
            if i % 50 == 0:
                print(f"  {i}/{len(todo)}")
    tce.save_cache(OUT, cache)

    rows = [(f, cache[f]) for f in files if "cue" in cache.get(f, {})]
    errs = [f for f in files if "error" in cache.get(f, {})]
    kinds = {}
    saved = 0.0
    for f, r in rows:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        saved += r["duration"] - r["cue"]
    print("kinds:", kinds, "errors:", len(errs))
    if rows:
        print(f"avg time trimmed per track: {saved / len(rows):.1f}s")
    for e in errs[:5]:
        print("ERR", e, cache[e]["error"][:100])


if __name__ == "__main__":
    main()
