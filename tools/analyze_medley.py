"""Batch-analyze audio/music for Dance Medley mode -> medley_analysis.json.
    python tools/analyze_medley.py            # whole library, all cores
    python tools/analyze_medley.py --force
    python tools/analyze_medley.py "dynamite"
"""
import argparse
import os
import sys
from concurrent.futures import ProcessPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from drivers import medley_analysis as ma  # noqa: E402

MUSIC = os.path.join(ROOT, "audio", "music")
OUT = os.path.join(ROOT, "medley_analysis.json")


def _work(path):
    try:
        r = ma.analyze(path)
        r["sig"] = ma._sig(path)
        return os.path.basename(path), r
    except Exception as e:
        return os.path.basename(path), {"error": str(e)[:200]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("filter", nargs="?", default="")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    cache = ma.load_cache(OUT)
    files = sorted(f for f in os.listdir(MUSIC) if f.lower().endswith(".mp3") and a.filter.lower() in f.lower())
    todo = [os.path.join(MUSIC, f) for f in files
            if a.force or cache.get(f, {}).get("sig") != ma._sig(os.path.join(MUSIC, f))]
    print(f"{len(files)} tracks, {len(todo)} to analyze")
    with ProcessPoolExecutor() as ex:
        for i, (name, res) in enumerate(ex.map(_work, todo, chunksize=4), 1):
            cache[name] = res
            if i % 50 == 0:
                print(f"  {i}/{len(todo)}")
    ma.save_cache(OUT, cache)
    ok = [v for v in cache.values() if "bpm" in v]
    print("analyzed ok:", len(ok), "errors:", sum(1 for v in cache.values() if "error" in v))


if __name__ == "__main__":
    main()
