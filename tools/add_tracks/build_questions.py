"""build_questions.py <specsN> <questionsN> <out.json> -- turns the hand-written question file into
track_cache.json entries ({track key: [question, ...]}), checking every LED/phone length limit."""
import importlib
import json
import random
import re
import sys
import zlib

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
specs = importlib.import_module(sys.argv[1]).SPECS
qmod = importlib.import_module(sys.argv[2])
SHORT_MAX, CHOICE_MAX, LONG_MAX, LONG_CHOICE_MAX = 48, 24, 280, 80
norm = lambda s: re.sub(r"\s+", " ", s.strip().lower())
out, problems, n_false, n_tf = {}, [], 0, 0


def check(title, q):
    for f, cap in (("question", SHORT_MAX), ("question_long", LONG_MAX), ("headline", 40), ("full", 200), ("correction", 100)):
        if len(q[f]) > cap:
            problems.append(f"{title}: {f} is {len(q[f])} > {cap}: {q[f]}")
    for c in q["choices"]:
        if len(c) > CHOICE_MAX:
            problems.append(f"{title}: choice {c!r} is {len(c)} > {CHOICE_MAX}")
    if len({c.lower() for c in q["choices"]}) != len(q["choices"]):
        problems.append(f"{title}: duplicate choices")


for prefix, artist, title, year, genre, rank, slow, energy, never in specs:
    items = qmod.Q.get(title)
    if not items:
        problems.append(f"{title}: no questions")
        continue
    rng = random.Random(zlib.crc32(title.encode()))
    qs = []
    if title not in qmod.NO_YEAR:
        offs = rng.choice([(-9, -4, 5), (-6, 4, 9), (-11, -5, 4), (-4, 5, 10), (-7, -3, 6)])
        choices = [str(year)] + [str(year + o) for o in offs]
        rng.shuffle(choices)
        qs.append({"category": "date", "question": "Year released?",
                   "question_long": f"What year was '{title}' by {artist} released?",
                   "choices": choices, "correct_index": choices.index(str(year)), "correction": "",
                   "headline": f"Released in {year}", "full": f"'{title}' by {artist} was released in {year}."})
    for it in items:
        if it[0] == "mc":
            _, cat, short, long_, ch, head, full = it
            choices = list(ch)
            rng.shuffle(choices)
            qs.append({"category": cat, "question": short, "question_long": long_, "choices": choices,
                       "correct_index": choices.index(ch[0]), "correction": "", "headline": head, "full": full})
        else:
            _, short, long_, ans, corr, head, full = it
            assert bool(corr) == (not ans), title
            n_tf += 1
            n_false += not ans
            qs.append({"category": "true_false", "question": "T/F: " + short, "question_long": "True or false: " + long_,
                       "choices": ["True", "False"], "correct_index": 0 if ans else 1, "correction": corr,
                       "headline": head, "full": full})
    if len(qs) != 3:
        problems.append(f"{title}: {len(qs)} questions, want 3")
    for q in qs:
        q["choices_long"] = list(q["choices"])
        q["release_year"] = year
        q["energy"] = energy
        check(title, q)
    out[f"{norm(artist)} - {norm(title)}"] = qs
extra = set(qmod.Q) - {s[2] for s in specs}
if extra:
    problems.append(f"questions for unknown titles: {sorted(extra)}")
print("\n".join(problems) if problems else "no problems")
print(len(out), "songs,", sum(len(v) for v in out.values()), "questions;", n_false, "of", n_tf, "true/false are False")
if not problems:
    json.dump(out, open(sys.argv[3], "w", encoding="utf-8"), indent=1, ensure_ascii=False)
