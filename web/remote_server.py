# web/remote_server.py
"""Local web remote control: a small FastAPI app served by uvicorn on a
background daemon thread, giving a second person (e.g. a co-host with a
phone) a mobile-friendly view of now-playing/quiz/Auto-DJ state plus
remote controls for quiz answers, Auto-DJ timing, mixer volume, and DMX
scene overrides. Self-starts at import time (same "import triggers
background work" convention as drivers/rekordbox_driver.py and
drivers/midi_driver.py) -- main.py just needs `from web import
remote_server` for the side effect.

All endpoints read/write the existing global `state` and call existing
driver/action functions directly -- no game-logic duplication. See the
Threading/Concurrency notes in the feature plan: request handlers run on
uvicorn's own thread(s), separate from the main pygame loop; state writes
here aren't mutex-protected against the main thread, which is an accepted
trade-off at human input speed for a live-show control surface."""
import io
import os
import threading
import time
import uuid

try:
    import requests
except ImportError:
    requests = None

import config
from state import state
from drivers import deck_orchestrator
from drivers import auto_dj_engine
from drivers import space_invaders_engine
from drivers.midi_driver import handle_dj_volume, set_dj_volume
from drivers import token_tracker
from drivers import branding_engine
from drivers import announcement_engine
from drivers.music_library import music_library, SUPPORTED_EXTENSIONS, sanitize_track_key
from drivers import music_metadata_engine
from drivers import youtube_import_engine
from drivers import win_sequence_engine
from drivers import show_engine
from drivers.dmx_driver import dmx
from drivers import led_bridge
from drivers import tunnel_engine
from graphics.animations import deal_panel_animations
from web.net_info import get_lan_ip, get_play_url

# Setup page status row (2026-08-16): cached/rate-limited public-internet
# reachability probe -- no existing generic connectivity check exists
# elsewhere in the codebase, and this must never let a down network stall
# a /api/show/status request the operator's browser is actively polling.
_net_check_cache = {"reachable": False, "checked_at": 0.0}
_NET_CHECK_INTERVAL_S = 15.0


def _internet_reachable():
    now = time.time()
    if now - _net_check_cache["checked_at"] < _NET_CHECK_INTERVAL_S:
        return _net_check_cache["reachable"]
    reachable = False
    if requests is not None:
        try:
            requests.head("https://1.1.1.1", timeout=1.5)
            reachable = True
        except Exception:
            reachable = False
    _net_check_cache["reachable"] = reachable
    _net_check_cache["checked_at"] = now
    return reachable

# How long a single web-remote D-pad "move" request keeps the virtual
# direction alive (inputs/gamepad.py::_held_space_invaders_direction()) if
# the phone's touchend/stop ping never arrives -- a dead-man's-switch so a
# dropped connection can't leave the cannon drifting. The remote page
# re-POSTs well inside this window for as long as the button is actually held.
_WEB_SI_DIRECTION_HOLD_SECONDS = 0.5

try:
    import uvicorn
    from fastapi import FastAPI, File, UploadFile
    from fastapi.responses import FileResponse, JSONResponse, Response
    from pydantic import BaseModel
except ImportError:
    uvicorn = None
    FastAPI = None

try:
    import qrcode
except ImportError:
    qrcode = None

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_INDEX_HTML_PATH = os.path.join(_STATIC_DIR, "index.html")
_PLAY_HTML_PATH = os.path.join(_STATIC_DIR, "play.html")
_LIBRARY_HTML_PATH = os.path.join(_STATIC_DIR, "library.html")
_SETUP_HTML_PATH = os.path.join(_STATIC_DIR, "setup.html")
_COUNTDOWN_HTML_PATH = os.path.join(_STATIC_DIR, "countdown.html")

app = FastAPI(title="Game Show Orchestrator Remote") if FastAPI else None


def _track_status(confident, source):
    if not confident:
        return "UNAVAILABLE"
    return "CACHED" if source in ("db", "ai_cache") else "FRESH"


if app is not None:

    class VolumeDelta(BaseModel):
        delta: int = 5

    class DmxScene(BaseModel):
        theme_index: int | None = None
        color_index: int | None = None

    class IdleThemeSet(BaseModel):
        theme: str

    class GameForce(BaseModel):
        category: str

    class BrandingSet(BaseModel):
        text: str

    class AnnouncementTextSet(BaseModel):
        text: str

    class WinScoreSet(BaseModel):
        score: int

    class IntermissionMinutesSet(BaseModel):
        minutes: int

    class ShowSetupSave(BaseModel):
        win_score: int
        intermission_minutes: int
        exclude_explicit: bool = False
        exclude_slow_dance: bool = False
        exclude_never_charted: bool = False
        allowed_genres: list[str] = []
        auto_final_round: bool = False
        rounds_estimate: int = 0  # computed client-side from the time-range slider

    class ShowSchedule(ShowSetupSave):
        target_epoch: float

    class OverlayHold(BaseModel):
        active: bool

    class SiMove(BaseModel):
        direction: int  # -1 left, 0 stop, 1 right

    class PlayerJoin(BaseModel):
        initials: str
        avatar_color: str | None = None

    class PlayerRename(BaseModel):
        player_id: str
        initials: str

    class PlayerAvatarSet(BaseModel):
        player_id: str
        avatar_color: str

    class PlayerSelect(BaseModel):
        player_id: str
        index: int

    class PlayerLock(BaseModel):
        player_id: str

    @app.get("/")
    def index():
        return FileResponse(_INDEX_HTML_PATH)

    @app.get("/library")
    def library_page():
        # Full-size Music Library / File Manager page (2026-08-12) -- the
        # compact version used to live inline on the main remote page;
        # moved out to its own page (reached via the hamburger menu) once
        # it grew a Cue button and a "currently cued" indicator on top of
        # search/upload/delete.
        return FileResponse(_LIBRARY_HTML_PATH)

    @app.get("/setup")
    def setup_page():
        # Trivia Night show flow (2026-08-13): pre-show configuration --
        # crowd-matching tags, points per round, intermission length, the
        # contracted-time planning slider, Auto Final Round, and the
        # Start Game / Start Game at [time] buttons. See
        # drivers/show_engine.py for the phase machine this drives.
        return FileResponse(_SETUP_HTML_PATH)

    @app.get("/countdown")
    def countdown_page():
        # "Start Game at [time]" lands here: a live "Game Start in H:MM:SS"
        # countdown with ABORT (back to Setup) and Start Now.
        return FileResponse(_COUNTDOWN_HTML_PATH)

    # ------------------------------------------
    # MULTIPLAYER QR QUIZ (2026-08-09)
    # ------------------------------------------
    # /play is the guest-facing page: sign up with 3-letter initials, then
    # answer/lock questions from a phone. It's a completely separate surface
    # from / (the operator's remote) -- no auth beyond "you're on the venue
    # LAN and scanned the QR", matching a party's actual trust model.
    @app.get("/play")
    def play_page():
        return FileResponse(_PLAY_HTML_PATH)

    @app.get("/api/quiz/qr")
    def quiz_qr():
        # Renders fresh per request rather than caching -- this is hit
        # rarely (once per phone that scans it) and get_play_url() is cheap,
        # so there's no reason to risk serving a stale IP after a Wi-Fi
        # reconnect the way overlay_panel.py's cached copy has to guard
        # against. Prefers the static non-WiFi redirector URL if configured
        # (config.TUNNEL_REDIRECT_PLAY_URL, see drivers/tunnel_engine.py),
        # falling back to the plain LAN-IP URL otherwise.
        if qrcode is None:
            return Response(status_code=503, content=b"qrcode package not installed")
        url = get_play_url()
        img = qrcode.make(url).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(content=buf.getvalue(), media_type="image/png")

    @app.post("/api/player/join")
    def player_join(body: PlayerJoin):
        initials = (body.initials or "").strip().upper()[:3] or "???"
        avatar_color = body.avatar_color if body.avatar_color in state.AVATAR_COLORS else state.AVATAR_COLORS[0]
        player_id = uuid.uuid4().hex
        state.quiz_players[player_id] = {
            "initials": initials, "selected_index": -1, "locked": False, "score": 0,
            "avatar_color": avatar_color,
        }
        print(f"[MULTIPLAYER QUIZ] '{initials}' joined ({len(state.quiz_players)} players signed up).")
        return {"ok": True, "player_id": player_id, "initials": initials, "avatar_color": avatar_color}

    @app.get("/api/player/whoami")
    def player_whoami(player_id: str):
        # Player phones call this on page load with whatever player_id they
        # have saved in localStorage -- e.g. after a server restart that
        # player_id won't be in state.quiz_players any more, and the page
        # needs to know to fall back to the sign-up form instead of a
        # "playing" view that has nothing real behind it.
        player = state.quiz_players.get(player_id)
        if player is None:
            return {"ok": False}
        return {
            "ok": True, "initials": player["initials"], "score": player["score"],
            "avatar_color": player.get("avatar_color", state.AVATAR_COLORS[0]),
        }

    @app.post("/api/player/rename")
    def player_rename(body: PlayerRename):
        # Changing initials is just a nicety (state.quiz_players is keyed by
        # player_id, not name) -- it can never affect the score already
        # earned under the old initials.
        player = state.quiz_players.get(body.player_id)
        if player is None:
            return {"ok": False}
        player["initials"] = (body.initials or "").strip().upper()[:3] or "???"
        return {"ok": True, "initials": player["initials"]}

    @app.post("/api/player/set-avatar")
    def player_set_avatar(body: PlayerAvatarSet):
        player = state.quiz_players.get(body.player_id)
        if player is None:
            return {"ok": False}
        if body.avatar_color not in state.AVATAR_COLORS:
            return {"ok": False, "reason": "bad avatar_color"}
        player["avatar_color"] = body.avatar_color
        return {"ok": True, "avatar_color": player["avatar_color"]}

    def _build_round_roster():
        """Per-player LIVE status for the round-roster UI shown on both
        play.html (every phone) and index.html (operator) while a question
        is in progress -- single source of truth for
        /api/player/question and /api/gamepad/status so the two surfaces
        can never disagree (2026-08-16, replaces the earlier reaction-feed
        design, which only ever showed the post-grade reveal).

        Never reveals correctness while a round is still live -- only
        "thinking" (not locked in) vs "locked" (locked in, no result shown
        yet), matching player_question()'s existing rule that correct_index
        stays -1 until state.quiz_locked. Once graded, reuses the exact
        same just_graded reveal-hold window that already gates
        correct_index/correction so the roster clears in lockstep with the
        rest of the reveal, rather than a separately-tuned duration.

        Correctness comes from state.quiz_last_round_results (populated
        once per round by inputs/gamepad.py::_grade_multiplayer_round(),
        also read by graphics/matrix_canvas.py's physical-board scorecard)
        -- this function only reshapes that existing data, never re-grades.

        Iterates state.quiz_players in dict/join order -- deliberately NOT
        score-sorted like `leaderboard` below -- so cards hold a stable
        position turn to turn instead of reshuffling as scores change
        mid-glance.

        Deliberately does NOT gate on state.factoid_choices being non-empty
        (confirmed live, 2026-08-16: choices can already be cleared/rolled
        over to the next queued question the instant grading completes,
        well before the reveal-hold window expires -- factoid_choices'
        lifecycle isn't a reliable "is a round live or just-graded" signal
        the way state.quiz_locked/quiz_graded_at are). `live` and
        `just_graded` are each self-sufficient checks."""
        just_graded = state.quiz_locked and (time.time() - state.quiz_graded_at) < config.QUIZ_TF_CORRECTION_HOLD_SECONDS
        live = bool(state.factoid_choices) and not state.quiz_locked
        if not live and not just_graded:
            return []
        correctness_by_player = {r["player_id"]: r["correct"] for r in state.quiz_last_round_results}
        roster = []
        for player_id, player in state.quiz_players.items():
            if live:
                status = "locked" if player["locked"] else "thinking"
            elif player_id in correctness_by_player:
                status = "correct" if correctness_by_player[player_id] else "incorrect"
            else:
                # Graded, but this player never locked in --
                # _grade_multiplayer_round() skips them entirely (not
                # counted as wrong), so give them a distinct neutral status
                # instead of silently vanishing from the roster.
                status = "no_answer"
            roster.append({
                "player_id": player_id, "initials": player["initials"],
                "avatar_color": player.get("avatar_color", state.AVATAR_COLORS[0]),
                "score": player["score"], "status": status,
            })
        return roster

    @app.get("/api/player/question")
    def player_question(player_id: str):
        player = state.quiz_players.get(player_id)
        if player is None:
            return {"ok": False}
        # Stamps "connected" for drivers/live_round_engine.py's auto-grade
        # check -- a player who's stopped polling (page closed, phone
        # locked) shouldn't block grading forever.
        player["last_seen"] = time.time()
        # Beta-Fix Feature Set item 2: a question is answerable the instant
        # the Mystery Band teaser arms it (drivers/mystery_band_engine.py::
        # _start_mystery(), which populates factoid_choices without
        # touching state.mode), not just once state.mode == MODE_GAME.
        # "Active" = loaded and either not yet graded, or graded within the
        # last reveal-hold window (2026-08-10 fix, two bugs in one spot):
        # (1) previously gated on state.mystery_active/mystery_resolved --
        # an independent ~10-13s reveal-blink animation on panels 1+2 that
        # used to silently end a client's "active" window (and stop
        # drivers/live_round_engine.py from monitoring the round at all)
        # long before the real 30/40s answer deadline, letting a correct-
        # but-still-pending answer go ungraded. (2) the fix for that then
        # dropped "active" the INSTANT quiz_locked flipped True, so the
        # client jumped straight to the waiting/leaderboard view without
        # ever showing the graded reveal (own pick + correct answer) at
        # all -- reusing the same hold the matrix display gives itself
        # (config.py) so a player actually gets to see the result.
        just_graded = state.quiz_locked and (time.time() - state.quiz_graded_at) < config.QUIZ_TF_CORRECTION_HOLD_SECONDS
        active = bool(state.factoid_choices) and (not state.quiz_locked or just_graded)
        # 0 once graded (2026-08-11 fix), not still counting down toward
        # round_deadline_at -- a round graded EARLY (e.g. everyone locked
        # in before time ran out) left the deadline in the future, so the
        # client's countdown bar kept visibly ticking during the reveal
        # instead of stopping the moment the round actually ended.
        remaining_seconds = max(0.0, state.round_deadline_at - time.time()) if (active and not state.quiz_locked) else 0.0
        # The mystery/identify question uses a longer timeout than the
        # standard one (config.MYSTERY_QUESTION_TIMEOUT_SECONDS) -- the
        # client needs the real total to draw an accurate countdown bar
        # percentage, not a hardcoded assumption (2026-08-10 fix).
        timeout_seconds = state.round_timeout_seconds if active else config.QUESTION_TIMEOUT_SECONDS

        win_message = ""
        winner_initials = ""
        winner_avatar_color = ""
        if state.win_sequence_active:
            # Pulled out as their own fields (2026-08-16, QUIZCADE avatar
            # feature) so play.html's win-screen celebration avatar doesn't
            # need to string-parse win_message -- sent to EVERY client
            # during the win sequence, not just the winner's own, since the
            # celebration avatar shows for the whole room.
            winner = state.quiz_players.get(state.game_winner_player_id)
            winner_initials = winner["initials"] if winner else "???"
            winner_avatar_color = winner.get("avatar_color", state.AVATAR_COLORS[0]) if winner else ""
            if player_id == state.game_winner_player_id:
                win_message = "STAND UP! YOU WIN!"
            else:
                win_message = f"Congratulations to {winner_initials}, the winner!"

        # First-correct-answer bonus (mystery question only) -- shown once
        # graded, only to whoever earned it.
        bonus_message = ("FIRST ONE RIGHT! BONUS POINT!"
                          if state.quiz_locked and player_id == state.round_bonus_player_id else "")

        # Post-win intermission (2026-08-11): overrides the normal waiting/
        # question view entirely on the client -- see play.html. Frozen
        # while the operator has it paused (2026-08-12) -- same shared
        # helper the LED countdown reads, so phones and the physical board
        # never disagree about how much time is left.
        intermission_remaining = win_sequence_engine.get_intermission_remaining_seconds(time.time())

        # Only reveal the correct answer once the round is actually graded
        # (state.quiz_locked) -- never send it while a question is still
        # live, so a client can't just read it out of the response.
        correct_index = state.factoid_correct_index if state.quiz_locked else -1
        # T/F explanation text (item: "put the right answer on their
        # screens") -- same correction string the matrix already shows,
        # only revealed once graded. play.html phrases the lead-in
        # ("That's right..." vs "Actually...") based on whether THIS
        # player's own answer was correct.
        correction = state.factoid_correction if (state.quiz_locked and state.factoid_choices == ["True", "False"]) else ""

        # Leaderboard for the "waiting for next question" view (item 18) --
        # top scorers, ranked descending, ties broken by join order.
        # player_id included so the client can bold its own row (item 28).
        # avatar_color (2026-08-16) lets the leaderboard show a swatch per row.
        leaderboard = sorted(
            ({"player_id": pid, "initials": p["initials"], "score": p["score"],
              "avatar_color": p.get("avatar_color", state.AVATAR_COLORS[0])}
             for pid, p in state.quiz_players.items()),
            key=lambda p: p["score"], reverse=True,
        )[:8]

        return {
            "ok": True,
            "round_id": state.quiz_round_id,
            # Lets play.html distinguish "waiting between queued questions
            # for this song" (still MODE_GAME) from "the round's fully over,
            # back spinning music" (MODE_DJ) so the waiting view can read
            # "next question" vs "next song" accordingly.
            "mode": "GAME" if state.mode == state.MODE_GAME else "DJ",
            "active": active,
            "locked_globally": state.quiz_locked,
            "question": state.factoid_question if active else "",
            "choices": state.factoid_choices if active else [],
            "selected_index": player["selected_index"],
            "locked": player["locked"],
            "correct_index": correct_index,
            "correction": correction,
            "win_message": win_message,
            "winner_initials": winner_initials,
            "winner_avatar_color": winner_avatar_color,
            "bonus_message": bonus_message,
            "avatar_color": player.get("avatar_color", state.AVATAR_COLORS[0]),
            "round_roster": _build_round_roster(),
            "intermission_active": state.intermission_active,
            "intermission_remaining_seconds": intermission_remaining,
            "score": player["score"],
            "remaining_seconds": remaining_seconds,
            "timeout_seconds": timeout_seconds,
            "leaderboard": leaderboard,
            "win_score": state.game_win_score,
        }

    @app.post("/api/player/select")
    def player_select(body: PlayerSelect):
        player = state.quiz_players.get(body.player_id)
        if player is None:
            return {"ok": False}
        if player["locked"] or state.quiz_locked:
            return {"ok": False, "reason": "already locked"}
        if not (0 <= body.index < len(state.factoid_choices)):
            return {"ok": False, "reason": "bad index"}
        player["selected_index"] = body.index
        if not state.round_first_answer_at:
            # First answer of this round -- panels 3-6 flip from the
            # teaser/idle content to showing the 4 answer options
            # (graphics/matrix_canvas.py).
            state.round_first_answer_at = time.time()
        return {"ok": True}

    @app.post("/api/player/lock")
    def player_lock(body: PlayerLock):
        player = state.quiz_players.get(body.player_id)
        if player is None:
            return {"ok": False}
        if player["selected_index"] < 0:
            return {"ok": False, "reason": "no selection"}
        player["locked"] = True
        # Timestamp of THIS lock -- inputs/gamepad.py::_grade_multiplayer_round()
        # uses this to find who answered the mystery question first (bonus
        # point), not just who's correct.
        player["locked_at"] = time.time()
        return {"ok": True}

    @app.get("/api/now-playing")
    def now_playing():
        if state.active_deck == 1:
            title, artist = state.deck1_track
            status = _track_status(state.deck1_confident, state.deck1_track_source)
        else:
            title, artist = state.deck2_track
            status = _track_status(state.deck2_confident, state.deck2_track_source)
        # Show-curation tags for whatever's actually playing (2026-08-12,
        # replaces the CACHED/FRESH/UNAVAILABLE badge in the Now Playing
        # card) -- empty title means nothing's confidently identified yet,
        # so there's no sensible track_key to look up.
        metadata = _serialize_metadata(sanitize_track_key(title, artist)) if title else None
        return {
            "title": title, "artist": artist, "status": status, "active_deck": state.active_deck,
            "metadata": metadata,
        }

    @app.post("/api/library/rescan")
    def library_rescan():
        # "Rescan Library" button: re-reads config.MUSIC_DIR from disk (new
        # uploads, deletions) without restarting the app.
        tracks = music_library.scan()
        return {"ok": True, "track_count": len(tracks)}

    def _serialize_metadata(track_key):
        entry = music_metadata_engine.get_metadata_for(track_key)
        if entry is None:
            entry = {"explicit": None, "slow_dance": None, "never_charted": None,
                      "decade": "", "genre": "", "energy_rank": None, "tagged_at": ""}
        entry = dict(entry)
        entry["complete"] = music_metadata_engine.is_complete(entry)
        return entry

    @app.get("/api/library/tracks")
    def library_tracks(q: str = ""):
        """Library tab track list -- optional `q` does a case-insensitive
        substring match against title+artist, so the operator can find a
        track in a 400+ song library without scrolling. `filename` (the
        basename inside config.MUSIC_DIR, never a full path) is what
        /api/library/delete and /api/library/cue take -- the client never
        needs to know or send a filesystem path.
        `cued_filename` (top-level, may be None) is whichever track is
        currently set to play next via the Cue button, so the client can
        highlight that one row without a separate poll.
        `metadata` (per track) is the show-curation tagging state --
        booleans/strings may be null/empty if untagged, `complete` tells
        the client whether this row still needs the Tag Library pass."""
        needle = q.strip().lower()
        cued_track = deck_orchestrator.get_cued_track()
        cued_filename = os.path.basename(cued_track["path"]) if cued_track else None
        out = []
        for t in music_library.all_tracks():
            title, artist = t["title"], t["artist"]
            if needle and needle not in title.lower() and needle not in artist.lower():
                continue
            track_key = sanitize_track_key(title, artist)
            out.append({
                "filename": os.path.basename(t["path"]),
                "title": title,
                "artist": artist,
                "duration": t["duration"],
                "metadata": _serialize_metadata(track_key),
            })
        out.sort(key=lambda t: (t["artist"].lower(), t["title"].lower()))
        return {
            "ok": True, "tracks": out, "total": len(music_library.all_tracks()),
            "cued_filename": cued_filename,
            "genres": config.MUSIC_GENRES,
        }

    class MetadataSet(BaseModel):
        filename: str
        explicit: bool | None = None
        slow_dance: bool | None = None
        never_charted: bool | None = None
        decade: str | None = None
        genre: str | None = None
        energy_rank: int | None = None

    @app.post("/api/library/metadata")
    def library_metadata_set(body: MetadataSet):
        """File Manager's per-track tag editor: manual correction/entry,
        same merge-only-what's-passed semantics as the AI pass -- editing
        just the genre doesn't touch the other 5 fields. Any field left as
        the model default (None/"") is simply not included in the update,
        so the client only needs to send what actually changed."""
        path = _safe_music_path(body.filename)
        if path is None or not os.path.isfile(path):
            return {"ok": False, "reason": "file not found"}
        track = next((t for t in music_library.all_tracks() if t["path"] == path), None)
        if track is None:
            return {"ok": False, "reason": "file not found in library"}
        track_key = sanitize_track_key(track["title"], track["artist"])

        fields = {}
        for name in ("explicit", "slow_dance", "never_charted", "decade", "genre", "energy_rank"):
            value = getattr(body, name)
            if value is None:
                continue
            if name == "genre" and value not in config.MUSIC_GENRES:
                return {"ok": False, "reason": f"invalid genre: {value!r}"}
            if name == "energy_rank" and not (config.MUSIC_ENERGY_RANK_MIN <= value <= config.MUSIC_ENERGY_RANK_MAX):
                return {"ok": False, "reason": "energy_rank must be 1-5"}
            fields[name] = value
        if not fields:
            return {"ok": False, "reason": "nothing to update"}

        music_metadata_engine.save_metadata_for(track_key, **fields)
        return {"ok": True, "metadata": _serialize_metadata(track_key)}

    @app.post("/api/library/tag")
    def library_tag_start():
        """Tag Library button: kicks off the decade-backfill + batched
        Haiku pass in the background (drivers/music_metadata_engine.py) --
        returns immediately, client polls /api/library/tag-status for
        progress. Only ever processes tracks that need it (missing or
        incomplete metadata), never re-processes an already-tagged track."""
        started = music_metadata_engine.start_tagging_job()
        if not started:
            return {"ok": False, "reason": "a tagging pass is already running"}
        return {"ok": True}

    @app.get("/api/library/tag-status")
    def library_tag_status():
        return {"ok": True, **music_metadata_engine.tagging_job_status()}

    class YoutubeImportStart(BaseModel):
        url: str

    @app.post("/api/library/youtube-import")
    def library_youtube_import_start(body: YoutubeImportStart):
        """Library page YouTube Import: paste a URL, starts a background
        download+convert job (drivers/youtube_import_engine.py) -- returns
        immediately, client polls /api/library/youtube-import-status for
        progress. Rejects a non-youtube.com/youtu.be URL or a second import
        while one's already running, same guard shape as library_tag_start()."""
        started, reason = youtube_import_engine.start_import(body.url)
        if not started:
            return {"ok": False, "reason": reason}
        return {"ok": True}

    @app.get("/api/library/youtube-import-status")
    def library_youtube_import_status():
        return {"ok": True, **youtube_import_engine.import_status()}

    class LibraryCue(BaseModel):
        filename: str

    @app.post("/api/library/cue")
    def library_cue(body: LibraryCue):
        """Library page Cue button: makes this track the next one a "next"
        move plays (physical/web transport, Auto-DJ's own timer -- any
        trigger of drivers/deck_orchestrator.py::trigger_track_move("next")),
        overriding the normal random pick. One-shot -- cleared the instant
        it's actually used, not sticky across multiple transitions."""
        path = _safe_music_path(body.filename)
        if path is None or not os.path.isfile(path):
            return {"ok": False, "reason": "file not found"}
        deck_orchestrator.set_cued_track(path)
        return {"ok": True}

    @app.post("/api/library/uncue")
    def library_uncue():
        deck_orchestrator.clear_cued_track()
        return {"ok": True}

    def _safe_music_path(filename):
        """Resolves a client-supplied filename to a path INSIDE
        config.MUSIC_DIR, or None if it isn't one. os.path.basename() alone
        already defeats a "../../something" traversal attempt (it discards
        every directory component), but the prefix check is cheap insurance
        against relying on that alone."""
        filename = os.path.basename(str(filename or "").strip())
        if not filename:
            return None
        path = os.path.join(config.MUSIC_DIR, filename)
        music_dir_abs = os.path.abspath(config.MUSIC_DIR) + os.sep
        if not os.path.abspath(path).startswith(music_dir_abs):
            return None
        return path

    @app.post("/api/library/upload")
    async def library_upload(files: list[UploadFile] = File(...)):
        """Library tab upload zone: accepts one or more audio files,
        rejects anything outside music_library.SUPPORTED_EXTENSIONS and
        anything that would collide with a filename already in the library
        (rather than silently overwriting a same-named file that might be a
        completely different song), saves the rest into config.MUSIC_DIR,
        then rescans once at the end -- not per-file, so a multi-file drop
        only costs one tag-reading pass.

        Auto-cues the upload (2026-08-12): a freshly-dropped track becomes
        the next "next" move's pick by default, same as pressing Cue on it
        by hand -- the common case is "I just added this, play it next."
        With multiple files in one drop, the LAST one saved wins the cue
        (arbitrary but unambiguous; there's only ever one cue slot)."""
        os.makedirs(config.MUSIC_DIR, exist_ok=True)
        existing = {os.path.basename(t["path"]) for t in music_library.all_tracks()}
        saved, rejected = [], []
        for f in files:
            name = os.path.basename(f.filename or "")
            ext = os.path.splitext(name)[1].lower()
            if not name or ext not in SUPPORTED_EXTENSIONS:
                rejected.append({"filename": name or "(unnamed)", "reason": "unsupported file type"})
                continue
            if name in existing:
                rejected.append({"filename": name, "reason": "a file with this name already exists"})
                continue
            dest = os.path.join(config.MUSIC_DIR, name)
            try:
                data = await f.read()
                with open(dest, "wb") as out:
                    out.write(data)
                saved.append(name)
                existing.add(name)  # guards against two files in the same upload sharing a name
            except Exception as e:
                rejected.append({"filename": name, "reason": f"write failed: {e}"})

        tracks = music_library.scan() if saved else music_library.all_tracks()
        if saved:
            deck_orchestrator.set_cued_track(os.path.join(config.MUSIC_DIR, saved[-1]))
        return {
            "ok": True, "saved": saved, "rejected": rejected, "track_count": len(tracks),
            "cued_filename": saved[-1] if saved else None,
        }

    class LibraryDelete(BaseModel):
        filename: str

    @app.post("/api/library/delete")
    def library_delete(body: LibraryDelete):
        """Library tab delete button. Irreversible -- the client is expected
        to confirm with the operator before sending this (nothing server-side
        double-checks, same trust level as every other control on this
        unauthenticated local-network remote). Any per-track rows this
        leaves behind in light_prefs.csv/track_cache.json/music_metadata.csv
        are harmless orphans, same as a hand-edited CSV drifting -- no
        cleanup pass needed."""
        path = _safe_music_path(body.filename)
        if path is None:
            return {"ok": False, "reason": "invalid filename"}
        if not os.path.isfile(path):
            return {"ok": False, "reason": "file not found"}
        try:
            os.remove(path)
        except Exception as e:
            return {"ok": False, "reason": f"delete failed: {e}"}
        tracks = music_library.scan()
        return {"ok": True, "track_count": len(tracks)}

    @app.post("/api/announcement/preview")
    def announcement_preview():
        # On-demand sweeper+VO preview -- doesn't touch either deck. Moved
        # here from firing automatically on every manual joystick track
        # move (2026-08-07); this is now the only way to trigger it.
        played = deck_orchestrator.preview_announcement()
        return {"ok": played}

    @app.get("/api/game/categories")
    def game_categories():
        return {"categories": config.WEB_GAME_CATEGORIES}

    @app.post("/api/game/force")
    def game_force(body: GameForce):
        # Lazy import: inputs.gamepad imports web.qr_popup at module scope,
        # so importing gamepad back at THIS module's top level would risk a
        # circular import depending on package init order.
        from inputs.gamepad import force_game_mode
        ok, message = force_game_mode(body.category)
        return {"ok": ok, "message": message}

    @app.post("/api/game/exit")
    def game_exit():
        # Web equivalent of physical Btn7/Btn8 (2026-08-09) -- same abort,
        # not a grade: no score recorded, no win/loss sound.
        from inputs.gamepad import abort_game_mode_early
        abort_game_mode_early()
        return {"ok": True}

    def _question_type_label(q):
        if q.get("category") == "true_false":
            return "True/False"
        if q.get("category") == "identify_band":
            return "Band Name"
        if "product" in q:
            return "Price Game"
        return "Multiple Choice"

    @app.get("/api/host/preview")
    def host_preview():
        # Party Host "Upcoming Question" Preview Panel: full metadata for
        # the next queued question (not merely a peek) so the MC can
        # prepare announcement banter before it ever hits the LED displays.
        if not state.track_question_queue:
            return {"available": False}
        q = state.track_question_queue[0]
        choices = q.get("choices", [])
        correct_index = q.get("correct_index", -1)
        correct_answer = choices[correct_index] if 0 <= correct_index < len(choices) else ""
        return {
            "available": True,
            "type": _question_type_label(q),
            "category": q.get("category", ""),
            "headline": q.get("headline", ""),
            "prompt": q.get("question", ""),
            "full": q.get("full", ""),
            "correct_answer": correct_answer,
            "queue_length": len(state.track_question_queue),
        }

    @app.get("/api/autodj/status")
    def autodj_status():
        remaining = state.auto_dj_track_duration - (time.time() - state.auto_dj_track_started_at)
        return {
            "enabled": state.auto_dj_enabled,
            "remaining_seconds": max(0.0, remaining),
            "duration_seconds": state.auto_dj_track_duration,
        }

    @app.post("/api/autodj/skip10")
    def autodj_skip10():
        # Pulls the elapsed-track window forward -- auto_dj_engine.update()
        # already reads started_at/duration every frame, no new engine hook
        # needed (see drivers/auto_dj_engine.py).
        state.auto_dj_track_started_at -= config.WEB_AUTODJ_SKIP_SECONDS
        return {"ok": True}

    @app.post("/api/autodj/add10")
    def autodj_add10():
        # Mirror image of skip10: pushes the elapsed-track window back, i.e.
        # buys the host more time before the auto-advance transition arms.
        # Clamped so "elapsed" can never go negative (a track that just
        # started can't be pushed to "not started yet").
        state.auto_dj_track_started_at = min(
            time.time(),
            state.auto_dj_track_started_at + config.WEB_AUTODJ_ADD_SECONDS,
        )
        return {"ok": True}

    @app.post("/api/autodj/skip-now")
    def autodj_skip_now():
        deck_orchestrator.trigger_track_move("next")
        auto_dj_engine.notify_manual_track_move()
        return {"ok": True}

    @app.post("/api/autodj/toggle")
    def autodj_toggle():
        # Virtual Gamepad "Toggle Auto-DJ" -- the only way to toggle it now
        # (2026-08-12: removed from physical Btn4, too easy to bump by
        # accident -- see inputs/gamepad.py's JOYBUTTONDOWN dispatch).
        auto_dj_engine.toggle_auto_dj()
        return {"ok": True, "enabled": state.auto_dj_enabled}

    @app.post("/api/announcement/toggle")
    def announcement_toggle():
        # Virtual Gamepad "Announcements" checkbox -- same engine hook as
        # physical Btn1 tap (drivers/auto_dj_engine.py::toggle_auto_announce()).
        # Governs BOTH Auto-DJ's automatic transitions and manual "next"/
        # "back" (deck_orchestrator.trigger_track_move checks this same flag).
        auto_dj_engine.toggle_auto_announce()
        return {"ok": True, "enabled": state.auto_announce_enabled}

    @app.post("/api/transport/next")
    def transport_next():
        # Mirrors the physical gamepad's X- axis (DJ mode "Next Track").
        deck_orchestrator.trigger_track_move("next")
        auto_dj_engine.notify_manual_track_move()
        return {"ok": True}

    @app.post("/api/transport/prev")
    def transport_prev():
        # Mirrors the physical gamepad's X+ axis (DJ mode "Previous Track").
        deck_orchestrator.trigger_track_move("back")
        auto_dj_engine.notify_manual_track_move()
        return {"ok": True}

    @app.post("/api/mixer/volume")
    def mixer_volume(body: VolumeDelta):
        handle_dj_volume(body.delta)
        return {"ok": True, "music_volume": state.music_volume}

    class VolumeSet(BaseModel):
        volume: int

    @app.post("/api/mixer/set-volume")
    def mixer_set_volume(body: VolumeSet):
        # Volume slider (2026-08-12): sends the live position as the
        # operator drags rather than a relative nudge -- set_dj_volume()
        # shares handle_dj_volume()'s Price Game duck-awareness rather than
        # duplicating it.
        set_dj_volume(body.volume)
        return {"ok": True, "music_volume": state.music_volume}

    @app.post("/api/dmx/scene")
    def dmx_scene(body: DmxScene):
        if body.theme_index is not None:
            state.dj_theme_index = max(0, min(config.DJ_THEME_COUNT, body.theme_index))
        if body.color_index is not None:
            state.dj_color_index = max(0, min(len(config.DJ_COLOR_PALETTE) - 1, body.color_index))
        return {"ok": True, "theme_index": state.dj_theme_index, "color_index": state.dj_color_index}

    @app.post("/api/idle/theme")
    def idle_theme_set(body: IdleThemeSet):
        """Switches the idle-animation theme (panels 3-6's dot/line/critter
        pool stays available under any theme; this picks which themed set
        -- Halloween/Birthday/Question Marks -- layers on top, see
        graphics/animations.py::IDLE_THEMES). Rejects anything not in
        config.IDLE_ANIMATION_THEMES rather than silently falling back, so a
        typo'd request tells the caller it did nothing instead of quietly
        picking the default.

        deal_panel_animations() is called here, synchronously, so the
        change is visible on the very next rendered frame -- not deferred
        to the next track like a normal re-deal."""
        if body.theme not in config.IDLE_ANIMATION_THEMES:
            return JSONResponse(
                {"ok": False, "error": f"unknown theme {body.theme!r}",
                 "valid": config.IDLE_ANIMATION_THEMES},
                status_code=400,
            )
        state.idle_theme = body.theme
        deal_panel_animations()
        return {"ok": True, "theme": state.idle_theme}

    @app.get("/api/tokens")
    def tokens():
        input_tokens, output_tokens, total = token_tracker.get_totals()
        return {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total}

    @app.get("/api/branding/current")
    def branding_current():
        # Real-Time Web Remote Branding Controller: lets the remote page
        # pre-fill its edit box with whatever's currently showing (live
        # edit, cached URL fetch, or the empty default).
        return {"text": branding_engine.get_current_text()}

    @app.post("/api/branding/set")
    def branding_set(body: BrandingSet):
        branding_engine.set_current_text(body.text)
        return {"ok": True, "text": branding_engine.get_current_text()}

    @app.get("/api/announcement-text/current")
    def announcement_text_current():
        # Per-file caption table (2026-08-10 redesign): "Last Announcement
        # Text" always reads/writes whichever file most recently played
        # (state.last_announcement_filename), not one global value --
        # filename included so the operator page can label the field with
        # which specific clip they're editing.
        filename = state.last_announcement_filename
        return {"filename": filename, "text": announcement_engine.get_text_for(filename)}

    @app.post("/api/announcement-text/set")
    def announcement_text_set(body: AnnouncementTextSet):
        filename = state.last_announcement_filename
        announcement_engine.set_text_for(filename, body.text)
        return {"ok": True, "filename": filename, "text": announcement_engine.get_text_for(filename)}

    @app.get("/api/gamepad/status")
    def gamepad_status():
        # Backs the Virtual Gamepad panel's toggle labels/enable-state --
        # no hardware gamepad to read this off of, so the remote polls it.
        mode_name = {
            state.MODE_DJ: "DJ",
            state.MODE_GAME: "GAME",
            state.MODE_SPACE_INVADERS: "SPACE_INVADERS",
        }.get(state.mode, "DJ")
        return {
            "mode": mode_name,
            "ai_suppressed": state.ai_suppressed,
            "auto_dj_enabled": state.auto_dj_enabled,
            "auto_announce_enabled": state.auto_announce_enabled,
            "sfx_enabled": state.sfx_enabled,
            "quiz_player_count": len(state.quiz_players),
            "round_roster": _build_round_roster(),
            "game_win_score": state.game_win_score,
            "intermission_minutes": state.intermission_minutes,
            "music_volume": state.music_volume,
            "intermission_active": state.intermission_active,
            "intermission_paused": state.intermission_paused,
            "intermission_remaining_seconds": win_sequence_engine.get_intermission_remaining_seconds(time.time()),
            "show_phase": state.show_phase,
        }

    @app.post("/api/game/price-game")
    def game_price_game():
        """"PLAY PRICE GAME" button (2026-08-12): the reliable, local-CSV-
        bank path (drivers/price_bank_engine.py) -- calls the exact same
        handler physical Btn6 uses, NOT the old AI-armed
        force_game_mode("price_game") path above, which only works if the
        AI happened to already tag the current track's era. This one never
        fails into a fallback question or silently does nothing."""
        from inputs.gamepad import handle_quiz_gate_button
        if state.mode != state.MODE_DJ:
            return {"ok": False, "reason": "Already in Game Mode."}
        if state.intermission_active:
            return {"ok": False, "reason": "Can't start during intermission."}
        handle_quiz_gate_button()
        return {"ok": True}

    @app.post("/api/game/win-score")
    def game_win_score_set(body: WinScoreSet):
        state.game_win_score = max(1, int(body.score))
        print(f"[WEB REMOTE] Game win score -> {state.game_win_score}")
        return {"ok": True, "game_win_score": state.game_win_score}

    @app.post("/api/game/intermission-minutes")
    def intermission_minutes_set(body: IntermissionMinutesSet):
        state.intermission_minutes = max(1, int(body.minutes))
        print(f"[WEB REMOTE] Intermission length -> {state.intermission_minutes} minute(s)")
        return {"ok": True, "intermission_minutes": state.intermission_minutes}

    # ------------------------------------------
    # TRIVIA NIGHT SHOW FLOW (2026-08-13): Setup / Countdown / Stop / New
    # Game. See drivers/show_engine.py for the phase machine these drive.
    # ------------------------------------------
    def _apply_show_setup(body):
        state.game_win_score = max(1, int(body.win_score))
        state.intermission_minutes = max(1, int(body.intermission_minutes))
        state.show_exclude_explicit = bool(body.exclude_explicit)
        state.show_exclude_slow_dance = bool(body.exclude_slow_dance)
        state.show_exclude_never_charted = bool(body.exclude_never_charted)
        state.show_allowed_genres = [g for g in body.allowed_genres if g in config.MUSIC_GENRES]
        state.show_final_round_number = (
            max(1, int(body.rounds_estimate))
            if body.auto_final_round and body.rounds_estimate else None
        )

    @app.get("/api/show/status")
    def show_status():
        now = time.time()
        countdown_remaining = (
            max(0.0, state.show_scheduled_start_at - now)
            if state.show_phase == "countdown" else 0.0
        )
        return {
            "show_phase": state.show_phase,
            "countdown_remaining_seconds": countdown_remaining,
            "scheduled_start_at": state.show_scheduled_start_at,
            "game_win_score": state.game_win_score,
            "intermission_minutes": state.intermission_minutes,
            "exclude_explicit": state.show_exclude_explicit,
            "exclude_slow_dance": state.show_exclude_slow_dance,
            "exclude_never_charted": state.show_exclude_never_charted,
            "allowed_genres": state.show_allowed_genres,
            "auto_final_round": state.show_final_round_number is not None,
            "final_round_number": state.show_final_round_number,
            "game_round_number": state.game_round_number,
            "music_genres": config.MUSIC_GENRES,
            "avg_round_minutes": config.SHOW_AVG_ROUND_MINUTES,
            "time_slider_step_minutes": config.SHOW_TIME_SLIDER_STEP_MINUTES,
            "time_slider_max_hours_ahead": config.SHOW_TIME_SLIDER_MAX_HOURS_AHEAD,
            "led_transport": led_bridge.current_transport(),
            "dmx_active": dmx.active,
            "tunnel_live": tunnel_engine.get_current_tunnel_url() != "",
            "internet_reachable": _internet_reachable(),
        }

    @app.post("/api/show/save-and-start-now")
    def show_save_and_start_now(body: ShowSetupSave):
        _apply_show_setup(body)
        show_engine.start_intro()
        return {"ok": True}

    @app.post("/api/show/save-and-schedule")
    def show_save_and_schedule(body: ShowSchedule):
        _apply_show_setup(body)
        ok = show_engine.schedule_start(body.target_epoch)
        return {"ok": ok}

    @app.post("/api/show/start-now")
    def show_start_now():
        # Countdown page's "Start Now" -- settings were already saved by
        # save-and-schedule above; this just fires the intro immediately
        # instead of waiting out the rest of the countdown.
        show_engine.start_intro()
        return {"ok": True}

    @app.post("/api/show/abort-countdown")
    def show_abort_countdown():
        ok = show_engine.abort_schedule()
        return {"ok": ok}

    @app.post("/api/show/stop")
    def show_stop():
        ok = show_engine.stop_show()
        return {"ok": ok, "reason": None if ok else "Show isn't live."}

    @app.post("/api/game/intermission-pause")
    def intermission_pause():
        ok = win_sequence_engine.pause_intermission()
        return {"ok": ok, "reason": None if ok else "No active intermission to pause."}

    @app.post("/api/game/intermission-resume")
    def intermission_resume():
        ok = win_sequence_engine.resume_intermission()
        return {"ok": ok, "reason": None if ok else "Intermission isn't paused."}

    class IntermissionRemainingSet(BaseModel):
        minutes: float

    @app.post("/api/game/intermission-set-remaining")
    def intermission_set_remaining(body: IntermissionRemainingSet):
        # "The crowd's taking too long to get ready" control (2026-08-12):
        # overrides however much time is left, whether the countdown is
        # currently paused or still running -- works in either direction.
        ok = win_sequence_engine.set_intermission_remaining_seconds(body.minutes * 60.0)
        return {
            "ok": ok, "reason": None if ok else "No active intermission.",
            "intermission_remaining_seconds": win_sequence_engine.get_intermission_remaining_seconds(time.time()),
        }

    @app.post("/api/overlay/status-hold")
    def overlay_status_hold(body: OverlayHold):
        # Virtual Gamepad "Status Overlay Hold" -- same panel-3 overlay as a
        # physical Btn1 hold (inputs/gamepad.py::_process_btn1_hold()/
        # _handle_btn1_release()). On release, mirrors the hardware's
        # persistence window instead of dropping the icon instantly.
        state.btn1_hold_overlay_active = body.active
        if not body.active:
            state.btn1_hold_overlay_until = time.time() + config.BTN1_HOLD_OVERLAY_PERSIST_SECONDS
        return {"ok": True}

    @app.post("/api/overlay/token-hold")
    def overlay_token_hold(body: OverlayHold):
        # Virtual Gamepad "Token Counter Overlay" -- mirrors a physical Btn3
        # hold (no persistence window, same as the hardware behavior).
        state.btn3_token_overlay_active = body.active
        return {"ok": True}

    @app.post("/api/system/ai-suppress/toggle")
    def ai_suppress_toggle():
        # Virtual Gamepad "Master AI Suppress Toggle" -- same flag the
        # desktop overlay panel's checkbox flips (graphics/overlay_panel.py).
        state.ai_suppressed = not state.ai_suppressed
        print(f"[WEB REMOTE] Suppress AI Functions -> {state.ai_suppressed}")
        return {"ok": True, "ai_suppressed": state.ai_suppressed}

    @app.post("/api/sfx/toggle")
    def sfx_toggle():
        # Operator page "Game SFX" checkbox -- mutes ding/buzzer/bigwin/
        # coin/buzz_short (inputs/gamepad.py) without touching music volume.
        state.sfx_enabled = not state.sfx_enabled
        print(f"[WEB REMOTE] Game SFX -> {state.sfx_enabled}")
        return {"ok": True, "sfx_enabled": state.sfx_enabled}

    @app.post("/api/space-invaders/toggle")
    def space_invaders_toggle():
        # Virtual Gamepad "Space Invaders Mode Toggle" -- same entry/exit
        # hooks as the physical Btn1+Btn3 combo / Btn7/Btn8 exit. Entry is
        # scoped to DJ mode only, mirroring the hardware combo's own guard.
        if state.mode == state.MODE_SPACE_INVADERS:
            space_invaders_engine.exit_space_invaders()
            return {"ok": True, "active": False}
        if state.mode == state.MODE_DJ:
            space_invaders_engine.enter_space_invaders()
            return {"ok": True, "active": True}
        return {"ok": False, "active": state.mode == state.MODE_SPACE_INVADERS,
                "message": "Can only enter Space Invaders from DJ mode."}

    @app.post("/api/space-invaders/move")
    def space_invaders_move(body: SiMove):
        # Virtual D-pad: sets the web-held direction inputs/gamepad.py's
        # _held_space_invaders_direction() polls every frame, same as a
        # held key/hat/axis. See state.web_si_direction's docstring for the
        # dead-man's-switch expiry reasoning.
        direction = 1 if body.direction > 0 else (-1 if body.direction < 0 else 0)
        state.web_si_direction = direction
        state.web_si_direction_expires_at = (
            time.time() + _WEB_SI_DIRECTION_HOLD_SECONDS if direction != 0 else 0.0
        )
        return {"ok": True}

    @app.post("/api/space-invaders/fire")
    def space_invaders_fire():
        if state.mode == state.MODE_SPACE_INVADERS:
            space_invaders_engine.fire()
        return {"ok": True}

    @app.post("/api/westminster/test")
    def westminster_test():
        # Lazy import: same circular-import reasoning as the quiz/game
        # lazy imports above -- avoid pulling drivers.westminster_engine in
        # at this module's top level.
        from drivers import westminster_engine
        westminster_engine.trigger_test()
        return {"ok": True}

    @app.post("/api/system/shutdown")
    def system_shutdown():
        # Just raises the flag -- the actual graceful-shutdown sequence
        # (cache saves, DMX blackout, driver stop, pygame.quit()) runs on
        # the MAIN thread in main.py's game loop, which polls
        # state.shutdown_requested every frame via inputs.gamepad.
        # process_events(). Doing the teardown here (a uvicorn worker
        # thread) would touch pygame/DMX state from off the main thread.
        print("[WEB REMOTE] SHUTDOWN APP requested via web remote.")
        state.shutdown_reason = "WEB REMOTE"
        state.shutdown_requested = True
        return {"ok": True}


def get_remote_url():
    return f"http://{get_lan_ip()}:{config.WEB_REMOTE_PORT}"


def _run_uvicorn():
    uvicorn.run(app, host="0.0.0.0", port=config.WEB_REMOTE_PORT, log_level="warning")


if app is not None and uvicorn is not None:
    threading.Thread(target=_run_uvicorn, daemon=True).start()
    print(f"[WEB REMOTE] Serving at {get_remote_url()}.")
else:
    print("[WEB REMOTE] fastapi/uvicorn not installed -- web remote control disabled.")
