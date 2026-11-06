"""make_tags.py <specsN> <tagsN.json> -- the per-file tag list pi_tags.py writes on the Pi
({"Artist - Title.mp3": {key, decade, genre, energy_rank, slow_dance, never_charted, energy}})."""
import importlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
specs = importlib.import_module(sys.argv[1]).SPECS
out = {}
for prefix, artist, title, year, genre, rank, slow, energy, never in specs:
    name = re.sub(r"\s+", " ", re.sub(r'[\\/:*?"<>|]', "", f"{artist} - {title}")).strip() + ".mp3"
    key = f"{re.sub(r'\s+', ' ', artist.strip().lower())} - {re.sub(r'\s+', ' ', title.strip().lower())}"
    out[name] = {"key": key, "decade": f"{year // 10 * 10}s", "genre": genre, "energy_rank": rank,
                 "slow_dance": slow, "never_charted": never, "energy": energy}
json.dump(out, open(sys.argv[2], "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print(len(out), "files")
