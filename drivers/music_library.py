"""Native track library index -- replaces rekordbox.xml as the source of
truth for "what tracks exist and what are their title/artist/duration" now
that playback happens directly from config.MUSIC_DIR instead of through
Rekordbox. Reads ID3/tag metadata via mutagen; falls back to the filename
(with a leading track-number stripped) when a file has no usable tags, since
many DJ-pool MP3s are untagged or only carry a track number.
"""
import os
import re
import threading

import mutagen

import config

_LEADING_TRACK_NUM_RE = re.compile(r'^\d+[\s\-\.]*')
# Strips a trailing "- Remastered", "(Remastered 2009)", "[2009 Remaster]"
# etc. qualifier off a title -- these show up on a lot of catalog reissues
# and just clutter the LED display / quiz question text, no show value.
# Anchored to the end of the string since that's where taggers always put
# it; doesn't touch "Remastered" if it shows up mid-title for some other
# reason.
_REMASTER_SUFFIX_RE = re.compile(
    r'\s*[\(\[-]+\s*(?:\d{4}\s*)?remaster(?:ed)?(?:\s*\d{4})?(?:\s*version)?\s*[\)\]]*\s*$',
    re.IGNORECASE,
)
# Strips the common junk suffixes YouTube video titles carry -- "(Official
# Video)", "(Official Audio)", "(Official Music Video)", "(Lyrics)"/"(Lyric
# Video)", "[HD]"/"[4K]", "(HQ)" -- same anchored-at-the-end approach as
# _REMASTER_SUFFIX_RE above. Wired into _read_tags() so every track through
# that one choke point gets the cleanup, not just YouTube imports (a
# manually-uploaded file with a messy title benefits too).
_YOUTUBE_NOISE_SUFFIX_RE = re.compile(
    r'\s*[\(\[]\s*(?:official\s*(?:music\s*)?(?:video|audio)|lyrics?(?:\s*video)?|hd|4k|hq)\s*[\)\]]\s*$',
    re.IGNORECASE,
)
# Public (not underscore-prefixed): web/remote_server.py's upload endpoint
# validates against this same list, so an accepted extension always matches
# what scan() will actually pick up -- one source of truth instead of a
# second hardcoded tuple drifting out of sync with this one.
SUPPORTED_EXTENSIONS = (".mp3", ".wav", ".flac", ".m4a", ".ogg")
_SUPPORTED_EXTENSIONS = SUPPORTED_EXTENSIONS  # back-compat alias, this module's own use


def sanitize_track_key(title, artist):
    """Same normalization as factoid_engine.py's cache key format
    ("artist - title", lowercased/whitespace-collapsed) so track_cache.json
    lookups keep hitting for tracks already fetched, and so
    state.factoid_track_key stays meaningful to every module that already
    keys off it (auto_dj_engine, mystery_band_engine, price_game_engine)."""
    t = re.sub(r'\s+', ' ', str(title).strip().lower())
    a = re.sub(r'\s+', ' ', str(artist).strip().lower())
    return f"{a} - {t}" if a else t


class MusicLibrary:
    def __init__(self):
        self._lock = threading.Lock()
        self.tracks = []  # list of dicts: {path, title, artist, duration}
        self.scan()

    def scan(self):
        """Rescans config.MUSIC_DIR from disk. Only reads tags, not full
        audio -- cheap enough to call after an upload or library change, not
        just once at startup."""
        found = []
        if os.path.isdir(config.MUSIC_DIR):
            for name in sorted(os.listdir(config.MUSIC_DIR)):
                if not name.lower().endswith(_SUPPORTED_EXTENSIONS):
                    continue
                path = os.path.join(config.MUSIC_DIR, name)
                title, artist, duration, bpm = self._read_tags(path, name)
                found.append({
                    "path": path, "title": title, "artist": artist,
                    "duration": duration, "bpm": bpm,
                })
        with self._lock:
            self.tracks = found
        print(f"[MUSIC LIBRARY] Scanned {config.MUSIC_DIR}: {len(found)} tracks.")
        return found

    @staticmethod
    def _read_tags(path, filename):
        """Returns (title, artist, duration, bpm). bpm is the track's stored
        tempo (ID3 TBPM, exposed by mutagen's EasyID3 as the 'bpm' key) if
        the file actually has one tagged -- None if not, so callers (see
        drivers/deck_orchestrator.py) can tell "no tempo info" apart from a
        real value and fall back to whatever tempo is already set instead of
        overwriting it with something bogus. A quick sample of this library
        found 0/60 tracks tagged with BPM, so treat "missing" as the normal
        case, not an edge case."""
        title, artist, duration, bpm = None, "", 0.0, None
        try:
            f = mutagen.File(path, easy=True)
            if f is not None:
                title_tag = f.get("title")
                artist_tag = f.get("artist")
                bpm_tag = f.get("bpm")
                title = title_tag[0] if title_tag else None
                artist = artist_tag[0] if artist_tag else ""
                if bpm_tag:
                    try:
                        parsed = float(bpm_tag[0])
                        if parsed > 0:
                            bpm = parsed
                    except (TypeError, ValueError):
                        pass
                if f.info is not None and getattr(f.info, "length", None):
                    duration = float(f.info.length)
        except Exception as e:
            print(f"[MUSIC LIBRARY] Tag read failed for {filename}: {e}")
        if not title:
            title = _LEADING_TRACK_NUM_RE.sub('', os.path.splitext(filename)[0]).strip()
        title = _REMASTER_SUFFIX_RE.sub('', title).strip()
        title = _YOUTUBE_NOISE_SUFFIX_RE.sub('', title).strip()
        return title, artist or "", duration, bpm

    def all_tracks(self):
        with self._lock:
            return list(self.tracks)

    def all_artists(self):
        """Distinct non-empty artist names -- replaces rb_driver.db.tracks_db's
        role as the Mystery Band decoy pool (drivers/mystery_band_engine.py)."""
        with self._lock:
            return {t["artist"] for t in self.tracks if t["artist"]}


music_library = MusicLibrary()
