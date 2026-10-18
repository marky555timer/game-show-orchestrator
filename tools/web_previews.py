"""Bulk-make the web Show Designer's preview audio on the PC (2026-10-18).

Every song in audio/music and the show-bed sounds (intro theme, wheel theme,
stabs) become a low-bitrate mono Opus copy (config.WEB_PREVIEW_BITRATE) and
are uploaded to triviacade.com under an opaque id (drivers/web_sync.py::
preview_id) -- the server never sees a file name. Copies already there are
skipped. Also uploads the song list, so the web page works before the Pi has
been on; the Pi replaces it with its own the next time it syncs.

    python tools/web_previews.py            # encode + upload what's missing
    python tools/web_previews.py --catalog  # only re-send the song list
    ... --metadata PATH   # decades from a copy of the Pi's music_metadata.csv
                          # (the PC's own is only a sample)

Encoded copies are kept in web_previews/ (git-ignored) so a re-run is quick.
"""
import concurrent.futures
import os
import subprocess
import sys

os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import requests  # noqa: E402

import config  # noqa: E402
from drivers import web_sync  # noqa: E402

CACHE = os.path.join(ROOT, "web_previews")


def ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


def api(action, **kw):
    r = requests.post(f"{config.WEB_DESIGNER_API_URL}?a={action}",
                      headers={"X-Trivia-Key": config.TUNNEL_REDIRECT_SECRET,
                               **({"Content-Type": "application/octet-stream"} if "data" in kw else {})},
                      timeout=120, **kw)
    r.raise_for_status()
    return r.json()


def encode(src, dst):
    if os.path.exists(dst):
        return
    tmp = dst + ".part"
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-i", src, "-vn", "-ac", "1", "-ar", "48000",
                    "-c:a", "libopus", "-b:a", config.WEB_PREVIEW_BITRATE, "-application", "audio",
                    "-f", "webm", tmp], check=True, timeout=300)
    os.replace(tmp, dst)


def main():
    if "--metadata" in sys.argv:
        config.MUSIC_METADATA_PATH = sys.argv[sys.argv.index("--metadata") + 1]
    cat = web_sync.catalog()
    print(f"{sum(1 for s in cat['songs'] if s['decade'])} of {len(cat['songs'])} songs have a decade.")
    cat["hash"] = "pc-" + web_sync._catalog_hash(cat)
    res = api("pi/catalog", json=cat)
    print(f"Song list uploaded: {res.get('count')} songs.")
    if "--catalog" in sys.argv:
        return
    have = set(api("pi/audio-list").get("have") or [])
    from drivers.music_library import music_library
    paths = {os.path.basename(t["path"]): t["path"] for t in music_library.all_tracks()}
    jobs = [(s["pid"], paths.get(s["file"])) for s in cat["songs"]]
    jobs += [(pid, web_sync.bed_path(name)) for name, pid in cat["bed"].items()]
    jobs = [(pid, p) for pid, p in jobs if p and os.path.isfile(p) and pid not in have]
    print(f"{len(have)} previews already online, {len(jobs)} to make.")
    os.makedirs(CACHE, exist_ok=True)

    def one(job):
        pid, src = job
        dst = os.path.join(CACHE, pid + ".webm")
        encode(src, dst)
        with open(dst, "rb") as f:
            api(f"pi/audio-put&id={pid}", data=f.read())
        return os.path.getsize(dst)

    total = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for i, (job, fut) in enumerate(zip(jobs, [pool.submit(one, j) for j in jobs]), 1):
            try:
                total += fut.result()
            except Exception as e:
                print(f"  FAILED {os.path.basename(job[1])}: {e}")
            if i % 25 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)} done, {total / 1e6:.0f} MB uploaded")


if __name__ == "__main__":
    main()
