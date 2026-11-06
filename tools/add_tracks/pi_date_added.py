"""Run on the Pi from the app folder with the app STOPPED: pi_date_added.py [backup_dir ...]
One-off date_added backfill (2026-10-23) for music_metadata.csv. Every library
track gets the EARLIEST of its file's modification date and the date of the
same-named file in any backup folder given (the 2026-10-13 levelling rewrote
most files, but ~/music_backup_before_leveling_20261013 kept the originals'
dates). Overwrites an existing date_added only with an earlier one."""
import os
import sys
import time

sys.path.insert(0, os.getcwd())
from drivers.music_library import music_library, sanitize_track_key
from drivers.music_metadata_engine import music_metadata_engine as mme

backups = [os.path.expanduser(d) for d in sys.argv[1:]]
changed = 0
for t in music_library.all_tracks():
    name = os.path.basename(t["path"])
    times = [os.path.getmtime(t["path"])]
    for d in backups:
        p = os.path.join(d, name)
        if os.path.exists(p):
            times.append(os.path.getmtime(p))
    day = time.strftime("%Y-%m-%d", time.localtime(min(times)))
    key = sanitize_track_key(t["title"], t["artist"])
    entry = mme.get(key)
    if entry and entry.get("date_added") and entry["date_added"] <= day:
        continue
    mme.save(key, date_added=day)
    changed += 1
print("date_added set for", changed, "tracks")
counts = {}
for e in mme.all_entries().values():
    counts[e.get("date_added") or "-"] = counts.get(e.get("date_added") or "-", 0) + 1
for d, n in sorted(counts.items()):
    print(f"  {d}: {n}")
