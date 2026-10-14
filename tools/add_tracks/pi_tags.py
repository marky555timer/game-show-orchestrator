"""Run on the Pi from the app folder with the app STOPPED: pi_tags.py tags.json
Writes music_metadata.csv tags and the light-show energy for newly added tracks, through the app's own engines."""
import json
import os
import sys

sys.path.insert(0, os.getcwd())
from drivers.music_library import music_library, sanitize_track_key
from drivers.music_metadata_engine import music_metadata_engine as mme
from drivers import light_prefs_engine as lpe
from drivers import medley_engine

tags = json.load(open(sys.argv[1], encoding="utf-8"))
by_name = {os.path.basename(t["path"]): t for t in music_library.all_tracks()}
done = 0
for name, t in tags.items():
    lib = by_name.get(name)
    if not lib:
        print("MISSING FILE", name)
        continue
    key = sanitize_track_key(lib["title"], lib["artist"])
    if key != t["key"]:
        print("KEY MISMATCH", key, "!=", t["key"])
    mme.save(key, explicit=False, slow_dance=t["slow_dance"], never_charted=t["never_charted"],
             decade=t["decade"], genre=t["genre"], energy_rank=t["energy_rank"])
    lpe.save_energy(key, t["energy"])
    done += 1
print("tagged", done)
rep = medley_engine.pool_report()
auto = [n for n in tags if rep.get(n, {}).get("state") == "auto"]
print("medley pool (auto):", len(auto))
for n in sorted(tags):
    r = rep.get(n, {})
    print(("IN   " if r.get("state") == "auto" else "out  ") + n[:-4] + ("" if not r.get("reason") else "  -- " + r["reason"]))
