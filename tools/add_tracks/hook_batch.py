"""Batch hook-word timing: Demucs vocals -> Whisper words -> phrase matches
-> onsets snapped to the vocal. usage: hook_batch.py [--match-only] [names...]
Results append to hook_batch_results.json (one entry per song)."""
import os, sys, re, json, time
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MUSIC = r"C:\Users\miste\OneDrive\Desktop\Orchestrator\audio\music"
CACHE = os.path.join(HERE, "hookcache"); os.makedirs(CACHE, exist_ok=True)
SPEC_MOD = os.environ.get("HOOK_SPECS", "hook_specs1")  # e.g. HOOK_SPECS=hook_specs2
OUT = os.path.join(HERE, f"hook_batch_results_{SPEC_MOD}.json")
sys.path.insert(0, HERE)
SPECS = __import__(SPEC_MOD).SPECS

SR = 44100
_model = None; _whisper = None


def vocals(name):
    p = os.path.join(CACHE, name + ".voc.npy")
    if os.path.exists(p):
        return np.load(p)
    import soundfile as sf, torch
    from demucs.pretrained import get_model
    from demucs.apply import apply_model
    global _model
    torch.set_num_threads(os.cpu_count())
    if _model is None:
        _model = get_model("htdemucs"); _model.eval()
    x, sr = sf.read(os.path.join(MUSIC, name), dtype="float32", always_2d=True)
    assert sr == SR, sr
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    w = torch.from_numpy(x.T.copy()); r = w.mean(0); mu, sd = r.mean(), r.std()
    with torch.no_grad():
        s = apply_model(_model, ((w - mu) / sd)[None], shifts=1, split=True, overlap=0.25, progress=False)[0]
    v = (s[_model.sources.index("vocals")] * sd + mu).mean(0).numpy().astype(np.float32)
    np.save(p, v)
    return v


def words(name, v):
    p = os.path.join(CACHE, name + ".words.json")
    if os.path.exists(p):
        return json.load(open(p))
    import stable_whisper
    global _whisper
    if _whisper is None:
        _whisper = stable_whisper.load_model("small.en")
    n16 = int(len(v) * 16000 / SR)
    v16 = np.interp(np.linspace(0, len(v) - 1, n16), np.arange(len(v)), v).astype(np.float32)
    res = _whisper.transcribe(v16, word_timestamps=True, language="en")
    ws = [{"w": wd.word.strip(), "start": round(wd.start, 3), "end": round(wd.end, 3)}
          for sg in res.segments for wd in sg.words]
    json.dump(ws, open(p, "w"))
    return ws


WIN, HOP = 24.0, 18.0


def words_windowed(name, v):
    """Whisper on overlapping 24s windows: much less prone than one whole-
    song pass to collapsing repeated choruses or smearing word times. Each
    window keeps the words starting in its middle stretch."""
    p = os.path.join(CACHE, name + ".wwords.json")
    if os.path.exists(p):
        return json.load(open(p))
    import stable_whisper
    global _whisper
    if _whisper is None:
        _whisper = stable_whisper.load_model("small.en")
    dur = len(v) / SR
    out = []
    t0 = 0.0
    while t0 < dur:
        a, b = int(t0 * SR), int(min(dur, t0 + WIN) * SR)
        seg = v[a:b]
        n16 = int(len(seg) * 16000 / SR)
        if n16 < 16000:
            break
        v16 = np.interp(np.linspace(0, len(seg) - 1, n16), np.arange(len(seg)), seg).astype(np.float32)
        res = _whisper.transcribe(v16, word_timestamps=True, language="en", verbose=None)
        lo = 0.0 if t0 == 0 else (WIN - HOP) / 2
        hi = WIN if t0 + WIN >= dur else WIN - (WIN - HOP) / 2
        for sg in res.segments:
            for wd in sg.words:
                if lo <= wd.start < hi:
                    out.append({"w": wd.word.strip(), "start": round(t0 + wd.start, 3), "end": round(t0 + wd.end, 3)})
        t0 += HOP
    out.sort(key=lambda w: w["start"])
    json.dump(out, open(p, "w"))
    return out


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def envelope(v, hop=0.01):
    h = int(SR * hop)
    n = len(v) // h
    e = np.sqrt((v[:n * h].reshape(n, h) ** 2).mean(1))
    k = np.ones(3) / 3
    return np.convolve(e, k, mode="same"), hop


def snap(env, hop, t, prev):
    """Vocal onset for a word Whisper put at t: the strongest rise near t,
    or (word after silence) the first real rise after t."""
    i = int(t / hop)
    peak = env.max()
    quiet = 0.04 * peak
    d = np.diff(env, prepend=env[0])
    lo = max(0, int((prev + 0.12) / hop), i - 25)
    if env[i:i + 8].mean() < quiet:
        # Whisper started it in silence: first rise above quiet within 1.8s
        for j in range(i, min(len(env), i + 180)):
            if env[j] > quiet * 1.5 and d[j] > 0:
                return round(j * hop, 2)
        return round(t, 2)
    hi = min(len(env) - 1, i + 30)
    if hi <= lo:
        return round(t, 2)
    seg = d[lo:hi]
    j = lo + int(np.argmax(seg))
    if seg.max() <= 0:
        return round(t, 2)
    return round(j * hop, 2)


def harmonize(occ, env, hop):
    """Choruses are sung the same way each time: with 3+ matches, take the
    median word spacing, re-fit each match to it (anchored where its words
    agree best), and nudge each word to the strongest vocal rise within
    0.12s. A match counts as good when at least 3 of its 4 words were
    already within 0.25s of the fitted spacing."""
    if len(occ) < 3:
        return occ
    T = np.array([o["times"] for o in occ])
    med = np.median(T - T[:, :1], axis=0)
    d = np.diff(env, prepend=env[0])
    out = []
    for o, t in zip(occ, T):
        anchor = float(np.median(t - med))
        fit = anchor + med
        agree = int((np.abs(t - fit) <= 0.25).sum())
        fixed = []
        for x in fit:
            i = int(x / hop)
            lo, hi = max(0, i - 12), min(len(d) - 1, i + 12)
            j = lo + int(np.argmax(d[lo:hi])) if hi > lo else i
            fixed.append(round(j * hop if d[j] > 0 else x, 2))
        spaced = min(b - a for a, b in zip(fixed, fixed[1:])) >= 0.15
        out.append({"times": fixed, "raw": o["times"], "whisper": o["whisper"], "ok": agree >= 3 and spaced,
                    "agree": agree})
    return out


def match(name):
    panels, parts = SPECS[name]
    v = vocals(name)
    ws = words_windowed(name, v) if WINDOWED else words(name, v)
    # joined letters with char -> word index
    joined, owner, offs = [], [], []
    for k, wd in enumerate(ws):
        s = norm(wd["w"])
        offs.append(len(joined))
        for ch in s:
            joined.append(ch); owner.append(k)
    joined = "".join(joined)
    # inner groups made non-capturing so group k is always panel k
    rx = re.compile("".join("(" + re.sub(r"\((?!\?)", "(?:", p) + ")" for p in parts))
    env, hop = envelope(v)
    occ = []
    for m in rx.finditer(joined):
        times = []
        for g in range(1, len(parts) + 1):
            c = m.start(g)
            k = owner[c]
            wd = ws[k]
            L = max(1, len(norm(wd["w"])))
            frac = (c - offs[k]) / L
            times.append(wd["start"] + frac * (wd["end"] - wd["start"]))
        snapped, prev = [], -1.0
        for t in times:
            s = snap(env, hop, t, prev)
            if s <= prev + 0.1:
                s = round(max(t, prev + 0.15), 2)
            snapped.append(s); prev = s
        if occ and snapped[0] - occ[-1]["times"][0] < 3.0:
            continue
        gaps = [b - a for a, b in zip(snapped, snapped[1:])]
        moved = max(abs(a - b) for a, b in zip(snapped, times))
        ok = moved <= 1.0 and min(gaps) >= 0.2 and max(gaps) <= 3.0
        occ.append({"times": snapped, "whisper": [round(t, 2) for t in times], "ok": ok})
    occ = harmonize(occ, env, hop)
    text = " ".join(w["w"] for w in ws)
    return {"panels": panels, "occurrences": occ, "n_words": len(ws), "transcript_head": text[:300]}


WINDOWED = "--windowed" in sys.argv

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    names = args or list(SPECS)
    results = json.load(open(OUT)) if os.path.exists(OUT) else {}
    for name in names:
        t0 = time.time()
        try:
            results[name] = match(name)
            r = results[name]
            print(f"{name}: {sum(o['ok'] for o in r['occurrences'])} ok / {len(r['occurrences'])} found "
                  f"({time.time()-t0:.0f}s) "
                  + " ".join(f"{o['times'][0]:.1f}{'' if o['ok'] else '?'}" for o in r["occurrences"]), flush=True)
        except Exception as e:
            print(f"{name}: FAILED {e!r}", flush=True)
            results[name] = {"error": repr(e)}
        json.dump(results, open(OUT, "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print("done", flush=True)
