"""drivers/youtube_import_engine.py
Audience song-request feature (2026-08-14 planning): paste a YouTube URL on
the operator-only Library page (web/static/library.html), a background job
downloads + converts it with yt-dlp, and it lands in config.MUSIC_DIR and
gets auto-cued exactly like a manual upload -- see
web/remote_server.py::library_upload() for the pattern this mirrors.

Deliberately operator-only: this does NOT go on the unauthenticated
audience-facing play.html, since anyone with the QR link could otherwise
queue arbitrary content straight to the show speakers with zero review.

Same single-job-at-a-time background-job convention as
drivers/music_metadata_engine.py's Tag Library job (_job_lock/_job_state,
threading.Thread(daemon=True), a status()/start_*() pair).

Known, accepted tradeoff: pulling audio off YouTube is against their Terms
of Service -- an already-made decision, not something this module
re-litigates. yt-dlp needs occasional updates as YouTube changes its site;
not a one-and-done dependency.
"""
import os
import re
import shutil
import tempfile
import threading
from urllib.parse import urlparse

import config
from drivers.music_library import music_library
from drivers import deck_orchestrator
from drivers import hot_track_engine

_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}

# Windows/macOS/Linux-illegal filename characters -- YouTube titles routinely
# carry these (colons, slashes, quotes) where an uploaded file's own
# filename never would.
_ILLEGAL_FILENAME_CHARS_RE = re.compile(r'[\\/:*?"<>|]')

_job_lock = threading.Lock()
# phase: "" | "fetching_info" | "downloading" | "converting" | "done" | "error"
_job_state = {"running": False, "phase": "", "pct": 0, "title": "", "last_error": ""}


def _is_youtube_url(url):
    try:
        host = urlparse(str(url or "").strip()).netloc.lower()
    except Exception:
        return False
    return host in _YOUTUBE_HOSTS


def import_status():
    with _job_lock:
        return dict(_job_state)


def start_import(url):
    """Returns (True, None) once the background job is started, or
    (False, reason) if the URL isn't youtube.com/youtu.be or a job's
    already running -- same server-side guard shape as
    music_metadata_engine.start_tagging_job(), just with a reason string
    since there are two different rejection cases here."""
    if not _is_youtube_url(url):
        return False, "not a youtube.com/youtu.be URL"
    with _job_lock:
        if _job_state["running"]:
            return False, "an import is already running"
        _job_state.update({"running": True, "phase": "fetching_info", "pct": 0,
                            "title": "", "last_error": ""})
    threading.Thread(target=_run_import, args=(url,), daemon=True).start()
    return True, None


def _sanitize_filename_base(title):
    base = _ILLEGAL_FILENAME_CHARS_RE.sub("", str(title or "")).strip()
    base = re.sub(r"\s+", " ", base)
    return base or "youtube_import"


def _unique_dest_path(base_name):
    """Collision-safe destination inside config.MUSIC_DIR -- append a
    numeric suffix on collision rather than reject, since there's no "try a
    different file" option mid-download the way there is for a manual
    upload. Same check web/remote_server.py::library_upload() does against
    the current library's basenames."""
    existing = {os.path.basename(t["path"]) for t in music_library.all_tracks()}
    candidate = f"{base_name}.mp3"
    if candidate not in existing:
        return os.path.join(config.MUSIC_DIR, candidate)
    n = 2
    while True:
        candidate = f"{base_name} ({n}).mp3"
        if candidate not in existing:
            return os.path.join(config.MUSIC_DIR, candidate)
        n += 1


def _tag_metadata(path, title, artist):
    """Writes the MP3's ID3 title (and, as of the Hot Track feature,
    artist) from yt-dlp's own probe metadata so drivers/music_library.py::
    _read_tags() picks up real tags instead of falling back to the raw
    filename -- that fallback path is also where _YOUTUBE_NOISE_SUFFIX_RE
    strips "(Official Video)"-style junk, so this matters for getting a
    clean display title either way. Writing artist matters beyond display:
    it's what makes the playback-time track key match the key
    hot_track_engine.arm() used, and it's required for
    mystery_band_engine.build_identify_fallback_question() to have anything
    to build a fallback quiz from -- a YouTube import with no artist tag
    could never reach that fallback path at all."""
    from mutagen.easyid3 import EasyID3
    from mutagen.id3 import ID3NoHeaderError
    try:
        tags = EasyID3(path)
    except ID3NoHeaderError:
        tags = EasyID3()
        tags.save(path)
        tags = EasyID3(path)
    tags["title"] = title
    if artist:
        tags["artist"] = artist
    tags.save()


def _run_import(url):
    # Lazy imports (like music_metadata_engine.py's call_haiku import) --
    # avoids coupling this module's import to yt-dlp/imageio-ffmpeg being
    # installed just to load the rest of the app.
    import yt_dlp
    import imageio_ffmpeg

    temp_dir = tempfile.mkdtemp(prefix="yt_import_")
    try:
        ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

        # Phase 1: info only, no download -- validates before committing to
        # any bandwidth.
        probe_opts = {"quiet": True, "no_warnings": True, "skip_download": True}
        with yt_dlp.YoutubeDL(probe_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        title = info.get("title") or "YouTube Import"
        artist = str(info.get("artist") or info.get("uploader") or "").strip()
        duration = info.get("duration") or 0
        with _job_lock:
            _job_state["title"] = title

        if duration > config.YOUTUBE_IMPORT_MAX_DURATION_SECONDS:
            limit_min = config.YOUTUBE_IMPORT_MAX_DURATION_SECONDS / 60.0
            with _job_lock:
                _job_state["phase"] = "error"
                _job_state["last_error"] = (
                    f"Video is {duration / 60.0:.0f} min, longer than the "
                    f"{limit_min:.0f} min import limit -- rejected before downloading."
                )
            return

        # Hot Track feature (2026-08-15): as early as possible -- title/
        # artist are known now, well before the download/convert below even
        # starts -- kick off priority AI trivia prefetch, gated on there
        # being enough runway left on the currently-playing track. A False
        # return means the gate failed; hot_track_armed governs both the
        # auto-cue-on-import behavior below and place_cue()'s slot pick.
        hot_track_armed = hot_track_engine.arm(title, artist)

        # Phase 2: download + convert into a TEMP location first -- only
        # moved into config.MUSIC_DIR after a verified-successful
        # conversion, so a failed/partial download can never corrupt the
        # live library or get picked up by a mid-download scan.
        def _progress_hook(d):
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                with _job_lock:
                    _job_state["phase"] = "downloading"
                    if total:
                        _job_state["pct"] = int(d.get("downloaded_bytes", 0) * 100 / total)
            elif d.get("status") == "finished":
                with _job_lock:
                    _job_state["phase"] = "converting"
                    _job_state["pct"] = 100

        def _pp_hook(d):
            if d.get("postprocessor") == "FFmpegExtractAudio" and d.get("status") == "started":
                with _job_lock:
                    _job_state["phase"] = "converting"

        download_opts = {
            "quiet": True, "no_warnings": True,
            "format": "bestaudio/best",
            "outtmpl": os.path.join(temp_dir, "%(id)s.%(ext)s"),
            "ffmpeg_location": ffmpeg_path,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": str(config.YOUTUBE_IMPORT_MP3_QUALITY_KBPS),
            }],
            "progress_hooks": [_progress_hook],
            "postprocessor_hooks": [_pp_hook],
        }
        with yt_dlp.YoutubeDL(download_opts) as ydl:
            ydl.extract_info(url, download=True)

        mp3_files = [f for f in os.listdir(temp_dir) if f.lower().endswith(".mp3")]
        if not mp3_files:
            raise RuntimeError("Download finished but no MP3 was produced.")
        temp_mp3_path = os.path.join(temp_dir, mp3_files[0])

        _tag_metadata(temp_mp3_path, title, artist)

        os.makedirs(config.MUSIC_DIR, exist_ok=True)
        dest_path = _unique_dest_path(_sanitize_filename_base(title))
        shutil.move(temp_mp3_path, dest_path)

        music_library.scan()
        if hot_track_armed:
            # Decides slot 0 ("next") vs slot 1 ("after next") using LIVE
            # remaining-time data at this exact moment (drivers/
            # hot_track_engine.py::place_cue()), and no-ops entirely if the
            # gate track has already transitioned away since arm().
            hot_track_engine.place_cue(dest_path)
        # else: the 90s runway gate failed at arm() time -- explicitly skip
        # auto-cue-on-import for this case (2026-08-15 Hot Track spec
        # change from the old unconditional auto-cue): plain library
        # addition, operator cues it manually if they want it sooner.

        with _job_lock:
            _job_state["phase"] = "done"
            _job_state["pct"] = 100
        print(f"[YOUTUBE IMPORT] Imported {title!r} -> {os.path.basename(dest_path)}")
    except Exception as e:
        hot_track_engine.cancel()  # a failed import can't leave a phantom armed job
        with _job_lock:
            _job_state["phase"] = "error"
            _job_state["last_error"] = str(e)
        print(f"[YOUTUBE IMPORT] Failed: {e}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        with _job_lock:
            _job_state["running"] = False
