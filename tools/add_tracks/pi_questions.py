"""Run on the Pi from the app folder with the app STOPPED: pi_questions.py questionsN.json
Adds pre-written questions to track_cache.json through the app's own engine, as unverified (red)
questions. A song that already has questions is left alone."""
import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.getcwd())
from drivers import question_audit

WHO = "Claude Code (Opus 5.5)"
data = json.load(open(sys.argv[1], encoding="utf-8"))
eng = question_audit._engine()
question_audit._backup_once()
added = skipped = 0
for key, qs in data.items():
    def apply(lst, qs=qs):
        if lst:
            return 0
        now = time.time()
        for q in qs:
            q = dict(q, qid=uuid.uuid4().hex[:12], ts=now, source="ai", led_short=True)
            q["audit"] = {"stage": "generated", "by": WHO, "ts": now, "note": "Written offline, not yet checked",
                          "deep": False, "original": None,
                          "history": [{"ts": now, "who": WHO, "action": "generated", "note": ""}]}
            lst.append(q)
        return len(qs)
    n = eng.edit(key, apply)
    added += n
    skipped += not n
print("questions added:", added, "songs skipped (already had questions):", skipped)
