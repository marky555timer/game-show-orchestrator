"""Phone bots (2026-11-23): N fake teams that join a live show and play it like real phones, over the
real public path (triviacade.com/play -> the Cloudflare tunnel), for realistic network-traffic tests.

Each bot does what web/static/play.html does: joins, polls /api/player/question every 1.2 s (0.5 s
while locked in and waiting on the reveal), and answers each question after a human-ish pause.
"Medium IQ": a bot knows the answer to a question only by looking it up in the show machine's own
question files (track_cache.json + fallback_questions.json, copied over ssh and refreshed every
minute), then gets it right with probability --skill (default 0.6); otherwise, or when the question
isn't found, it guesses. Fastest Finger true/false is a coin flip with a small lean to the truth
when the answer has been revealed before (never -- so plain 50/50).

    python tools/phone_bots.py                      # 10 teams on the public link
    python tools/phone_bots.py --teams 4 --base http://192.168.1.26:8765   # straight to the box
Ctrl+C sends every bot "away" and stops.
"""
import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
import threading
import time

import requests

NAMES = ["Quizzly Bears", "Disco Biscuits", "Vinyl Vikings", "Beat Bandits", "Groove Goblins",
         "Treble Makers", "Air Guitars", "Mixtape Mob", "Jukebox Heroes", "Bass Invaders",
         "Rock Lobsters", "Funk Monks", "Synth Pops", "Lyric Lizards", "Chorus Kings"]
EMOJIS = ["🐻", "🍪", "⚔️", "🎸", "👹", "🎻", "🎤", "📼", "🦸", "👾", "🦞", "🧘", "🎹", "🦎", "👑"]

_print_lock = threading.Lock()
_timeline = {"key": None, "since": 0.0, "lock": threading.Lock()}


def note_phase(d):
    """Round timing for beta tests: logs each change of round / intermission / end of night with how
    long the previous stretch lasted (every bot reports; only real changes are printed)."""
    if d.get("show_final"):
        key = ("night over",)
    elif d.get("intermission_active"):
        key = ("intermission", d.get("round_number"))
    else:
        key = ("round", d.get("round_number"), d.get("round_name") or "")
    with _timeline["lock"]:
        if key == _timeline["key"]:
            return
        now = time.time()
        if _timeline["key"] is not None:
            mins = (now - _timeline["since"]) / 60
            log(f"[TIMING] {' '.join(str(x) for x in _timeline['key'])} lasted {mins:.1f} min")
        log(f"[TIMING] now: {' '.join(str(x) for x in key)}")
        _timeline.update(key=key, since=now)


def log(msg):
    with _print_lock:
        print(time.strftime("%H:%M:%S"), msg, flush=True)


# ------------------------------------------------------------------ answer key
class AnswerKey:
    """choices (normalised, sorted) -> correct choice text, from the show machine's question files."""

    def __init__(self, host, key, remote_dir):
        self.host, self.key, self.dir = host, key, remote_dir
        self.by_choices, self.by_question, self.ambiguous = {}, {}, set()
        self._tmp = tempfile.mkdtemp(prefix="bots_")
        self.refresh()
        threading.Thread(target=self._loop, daemon=True).start()

    @staticmethod
    def _norm(s):
        return " ".join(str(s).lower().split())

    def _add(self, q):
        try:
            choices, ci = q["choices"], int(q["correct_index"])
            right = choices[ci]
        except (KeyError, IndexError, TypeError, ValueError):
            return
        ck = tuple(sorted(self._norm(c) for c in choices))
        if self.by_choices.get(ck, self._norm(right)) != self._norm(right):
            self.ambiguous.add(ck)      # two questions share these choices (e.g. years) -- text only
        self.by_choices[ck] = self._norm(right)
        for k in ("question", "phone_question", "led_question", "short_question"):
            if q.get(k):
                self.by_question[self._norm(q[k])] = self._norm(right)

    def refresh(self):
        if not self.host:
            return
        for name in ("track_cache.json", "fallback_questions.json"):
            dest = os.path.join(self._tmp, name)
            r = subprocess.run(["scp", "-q", "-i", self.key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                                f"{self.host}:{self.dir}/{name}", dest], capture_output=True)
            if r.returncode != 0:
                continue
            try:
                data = json.load(open(dest, encoding="utf-8"))
            except Exception:
                continue
            rows = data.values() if isinstance(data, dict) else [data]
            for row in rows:
                for q in (row if isinstance(row, list) else [row]):
                    if isinstance(q, dict):
                        self._add(q)
        log(f"[KEY] {len(self.by_choices)} questions known.")

    def _loop(self):
        while True:
            time.sleep(60)
            try:
                self.refresh()
            except Exception as e:
                log(f"[KEY] refresh failed: {e}")

    def correct_index(self, question, choices):
        right = self.by_question.get(self._norm(question))
        ck = tuple(sorted(self._norm(c) for c in choices))
        if right is None and ck not in self.ambiguous:
            right = self.by_choices.get(ck)
        if right is None:
            return None
        for i, c in enumerate(choices):
            if self._norm(c) == right:
                return i
        return None


# ------------------------------------------------------------------ one phone
class Bot(threading.Thread):
    def __init__(self, n, name, emoji, base, key, skill, stop):
        super().__init__(daemon=True, name=f"bot-{n}")
        self.name_, self.emoji, self.base, self.key, self.skill, self.stop = name, emoji, base, key, skill, stop
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) phone-bot"
        self.pid = None
        self.answered = set()        # round_ids
        self.medley_done = set()     # qids
        self.rtt = None
        self.stats = {"polls": 0, "fails": 0, "answers": 0, "right": 0, "rtt_sum": 0.0, "rtt_max": 0.0}
        self.last_score = 0

    def url(self, path):
        return self.base() + path

    def join(self):
        r = self.s.post(self.url("/api/player/join"), json={"name": self.name_, "emoji": self.emoji}, timeout=10)
        d = r.json()
        if d.get("ok"):
            self.pid = d["player_id"]
            log(f"[{self.name_}] joined as {d.get('name')!r}.")

    def run(self):
        time.sleep(random.uniform(0, 8))          # people don't all scan the QR in the same second
        while not self.stop.is_set() and self.pid is None:
            try:
                self.join()
            except Exception as e:
                log(f"[{self.name_}] join failed: {e}")
                time.sleep(3)
        wait = 1.2
        while not self.stop.is_set():
            t0 = time.time()
            d = None
            try:
                q = f"/api/player/question?player_id={self.pid}" + (f"&rtt={round(self.rtt)}" if self.rtt else "")
                d = self.s.get(self.url(q), timeout=10).json()
                self.rtt = (time.time() - t0) * 1000
                self.stats["polls"] += 1
                self.stats["rtt_sum"] += self.rtt
                self.stats["rtt_max"] = max(self.stats["rtt_max"], self.rtt)
            except Exception:
                self.stats["fails"] += 1
            if d is not None:
                if not d.get("ok"):
                    log(f"[{self.name_}] the show forgot this team -- joining again.")
                    self.pid = None
                    self.answered.clear()
                    try:
                        self.join()
                    except Exception:
                        pass
                else:
                    wait = self.handle(d)
            self.stop.wait(max(0.2, wait - (time.time() - t0)))

    def handle(self, d):
        note_phase(d)
        if d.get("score", 0) != self.last_score:
            self.last_score = d.get("score", 0)
        rid = d.get("round_id")
        if d.get("active") and not d.get("locked_globally") and d.get("choices") and rid not in self.answered:
            self.answered.add(rid)
            threading.Thread(target=self.answer, args=(d,), daemon=True).start()
        m = d.get("medley")
        if m and m.get("active") and m.get("qid") is not None and m["qid"] not in self.medley_done:
            self.medley_done.add(m["qid"])
            threading.Thread(target=self.medley, args=(m, d.get("server_time", time.time())), daemon=True).start()
        waiting = d.get("active") and not d.get("locked_globally") and rid in self.answered
        return 0.5 if waiting else 1.2

    def answer(self, d):
        choices, rid = d["choices"], d.get("round_id")
        left = float(d.get("remaining_seconds") or d.get("timeout_seconds") or 20)
        time.sleep(max(0.8, min(random.uniform(3, 12), left - 1.5)))   # reading time
        right = self.key.correct_index(d.get("question", ""), choices)
        if right is not None and random.random() < self.skill:
            idx = right
        else:
            wrong = [i for i in range(len(choices)) if i != right] if right is not None else list(range(len(choices)))
            idx = random.choice(wrong)
        try:
            self.s.post(self.url("/api/player/select"), json={"player_id": self.pid, "index": idx}, timeout=10)
            time.sleep(random.uniform(0.3, 1.5))
            r = self.s.post(self.url("/api/player/lock"), json={"player_id": self.pid, "index": idx, "round_id": rid},
                            timeout=10).json()
            self.stats["answers"] += 1
            if right is not None and idx == right:
                self.stats["right"] += 1
            if not r.get("ok"):
                log(f"[{self.name_}] lock refused: {r.get('reason')}")
        except Exception as e:
            log(f"[{self.name_}] answer failed: {e}")

    def medley(self, m, server_time):
        opens = float(m.get("opens_at") or 0)
        self.stop.wait(max(0.0, opens - server_time) + random.uniform(0.8, 4.0))
        try:
            self.s.post(self.url("/api/player/medley-answer"),
                        json={"player_id": self.pid, "qid": m["qid"], "answer": random.random() < 0.5}, timeout=10)
        except Exception:
            pass

    def away(self):
        if self.pid:
            try:
                self.s.post(self.url(f"/api/player/away?player_id={self.pid}"), timeout=5)
            except Exception:
                pass


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--teams", type=int, default=10)
    ap.add_argument("--skill", type=float, default=0.6, help="chance of answering a known question right")
    ap.add_argument("--base", default="", help="show machine URL (default: follow triviacade.com/play)")
    ap.add_argument("--host", default="mark@192.168.1.26", help="ssh target for the answer key ('' = none)")
    ap.add_argument("--key", default=os.path.expanduser("~/.ssh/trivia_pi"))
    ap.add_argument("--dir", default="game-show-orchestrator")
    a = ap.parse_args()

    base = {"url": a.base.rstrip("/")}

    def resolve():
        if a.base:
            return
        r = requests.get("https://triviacade.com/play/", allow_redirects=False, timeout=10)
        loc = r.headers.get("Location", "")
        if loc:
            base["url"] = loc.rstrip("/").removesuffix("/play")
            log(f"[NET] show machine at {base['url']}")

    resolve()
    if not base["url"]:
        sys.exit("Couldn't find the show machine (no redirect from triviacade.com/play).")

    def watch_tunnel():          # the tunnel address changes when the show machine restarts
        while True:
            time.sleep(30)
            try:
                old = base["url"]
                resolve()
                if base["url"] != old:
                    log(f"[NET] tunnel moved: {old} -> {base['url']}")
            except Exception:
                pass
    threading.Thread(target=watch_tunnel, daemon=True).start()

    key = AnswerKey(a.host, a.key, a.dir)
    stop = threading.Event()
    bots = [Bot(i, NAMES[i % len(NAMES)], EMOJIS[i % len(EMOJIS)], lambda: base["url"], key, a.skill, stop)
            for i in range(a.teams)]
    for b in bots:
        b.start()
    try:
        while True:
            time.sleep(60)
            polls = sum(b.stats["polls"] for b in bots)
            fails = sum(b.stats["fails"] for b in bots)
            rtt = sum(b.stats["rtt_sum"] for b in bots) / max(1, polls)
            ans = sum(b.stats["answers"] for b in bots)
            right = sum(b.stats["right"] for b in bots)
            worst = max(b.stats["rtt_max"] for b in bots)
            log(f"[STATS] polls {polls}, failed {fails}, avg {rtt:.0f} ms, worst {worst:.0f} ms, "
                f"answers {ans} ({right} right) | " + ", ".join(f"{b.name_} {b.last_score}" for b in bots))
    except KeyboardInterrupt:
        stop.set()
        for b in bots:
            b.away()
        log("Stopped; every bot marked away.")


if __name__ == "__main__":
    main()
