"""drivers/cliffhanger_engine.py
Cliffhanger solo challenge (2026-11-20, docs/rounds_design.md build step 5). Price-Is-Right style.

Three songs. For each one the player hears the song and guesses its YEAR; the host types the guess on
the admin page (the song's year is pre-filled from track_cache.json and can be corrected). The climber
moves up one space per year off, cumulative across the songs; there are config.CLIFF_SPACES (25)
spaces. More than 25 in total = he falls off the cliff (lose); 25 or fewer after the last (5th) song = win.
Songs change with a plain crossfade (no sweeper / announcement), and the next song starts on its own
config.CLIFF_NEXT_SONG_SECONDS after a climb. The host types guesses on /cliff (web/static/cliff.html).
Stakes are drivers/solo_challenge.py's (+/-1500 on the night score).

The climb takes config.CLIFF_SECONDS_PER_SPACE per space, with gameMusic/cliffhanger.wav playing while
he moves; a fall plays sound_effects/wilhelm.wav, a win sound_effects/bigWin.wav (solo_challenge).
The song under the challenge is held (no auto-DJ moves) and faded out when a guess is locked in;
NEXT SONG on the admin page cues the next one.

Cliffhanger ROUNDS (2026-11-22, the designer's "Cliffhanger (solo)" round type) open on a PREGAME
(pregame_*): the LED panels show only "Cliffhanger!" on panels 1+2 with the marquee's movie chase
around them (the TV board draws the same), and the host's /cliff page becomes the pregame control
panel -- FADE MUSIC (to 1/4 of the volume) / RAISE VOLUME (back), the team (lowest night score first,
preselected) and BEGIN GAME. BEGIN GAME raises the volume, starts the challenge (the first quiz song
plays at once), wipes "What year did this song debut?" across all six panels and turns /cliff into the
guess screen. The TV holds the board for config.CLIFF_TV_BOARD_SECONDS, then fades to the mountain.

Displays: the TV board (graphics/rig_view.py, practice rig) shows the animated mountain art from
view(); the LED panels show song / guess / year / spaces (graphics/matrix_canvas.py); the marquee
chases while he climbs and on a win and goes dark on a fall (drivers/wled_engine.py).
"""
import json
import random
import time

import config
from state import state

# phase: "" | "listen" (song playing, waiting for the guess) | "climb" | "between" (waiting for NEXT
# SONG) | "fall" | "result"
_c = {"phase": ""}


def active():
    return bool(_c.get("phase"))


def _years():
    """track_key -> release year, from the trivia cache (the AI's release_year on each question)."""
    try:
        with open(config.TRACK_CACHE_PATH, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except Exception as e:
        print(f"[CLIFFHANGER] Could not read {config.TRACK_CACHE_PATH}: {e}")
        return {}
    out = {}
    for key, questions in cache.items():
        for q in questions if isinstance(questions, list) else []:
            try:
                y = int(q.get("release_year") or 0)
            except (TypeError, ValueError, AttributeError):
                y = 0
            if 1900 <= y <= 2100:
                out[key] = y
                break
    return out


def _pick_song():
    """A random library song with a known year, not used in this challenge, not the one playing."""
    from drivers.music_library import music_library
    from drivers.factoid_engine import _sanitize_track_key
    years = _c.get("years") or {}
    used = _c.get("used", set())
    pool = []
    for t in music_library.all_tracks():
        key = _sanitize_track_key(t.get("title", ""), t.get("artist", ""))
        if key in years and t["path"] not in used and t["path"] != state.now_playing_path:
            pool.append((t, years[key]))
    if not pool:
        return None
    try:
        from drivers import deck_orchestrator
        allowed = {t["path"] for t in deck_orchestrator._apply_show_filters([t for t, _ in pool])}
        pool = [(t, y) for t, y in pool if t["path"] in allowed] or pool
    except Exception:
        pass
    return random.choice(pool)


def _deck():
    return "deck1" if state.active_deck == 1 else "deck2"


def _cue(track):
    from drivers import deck_orchestrator, auto_dj_engine
    deck_orchestrator.set_cued_track(track["path"])
    deck_orchestrator.trigger_plain_move()      # no sweeper, no announcement during Cliffhanger
    auto_dj_engine.notify_manual_track_move()


def _load_song(index):
    got = _pick_song()
    if got is None:
        return False
    track, year = got
    _c["used"].add(track["path"])
    _c.update(song=index, title=track.get("title", ""), artist=track.get("artist", ""), path=track["path"],
              year=year, guess=None, off=None, phase="listen")
    _cue(track)
    print(f"[CLIFFHANGER] Song {index + 1}/{config.CLIFF_SONGS}: {track.get('artist')} - {track.get('title')} ({year}).")
    return True


def start():
    """drivers/solo_challenge.py::start. Returns (ok, message)."""
    q_t0 = time.time() if _pre.get("beginning") else 0.0
    _c.clear()
    _c.update(phase="", years=_years(), used=set(), total=0, shown=0.0, climb_from=0.0, climb_to=0.0,
              t0=0.0, fall_at=0.0, result="", history=[], q_t0=q_t0)
    if not _load_song(0):
        _c["phase"] = ""
        return False, "No songs with a known year in the library -- Cliffhanger needs tagged songs."
    return True, ""


def reroll():
    """Admin "different song": before the guess, swap this song for another."""
    if _c.get("phase") != "listen":
        return False
    return _load_song(_c["song"])


def guess(year, actual=None):
    """Admin SUBMIT GUESS. `actual` corrects the song's year. Returns (ok, message)."""
    if _c.get("phase") != "listen":
        return False, "Not waiting for a guess."
    try:
        year = int(year)
        actual = int(actual) if actual not in (None, "") else int(_c["year"])
    except (TypeError, ValueError):
        return False, "Enter the guess as a year, e.g. 1984."
    if not (1900 <= year <= 2100):
        return False, "That doesn't look like a year."
    _c["year"] = actual
    off = abs(year - actual)
    now = time.time()
    _c.update(guess=year, off=off)
    _c["history"].append({"song": _c["song"] + 1, "title": _c["title"], "artist": _c["artist"],
                          "guess": year, "year": actual, "off": off})
    start_at = float(_c["total"])
    _c["total"] += off
    # Over the limit: he climbs to the very edge, then over it.
    target = float(min(_c["total"], config.CLIFF_SPACES + config.CLIFF_OVERHANG_SPACES))
    print(f"[CLIFFHANGER] Guess {year}, actual {actual}: {off} off -- total {_c['total']}/{config.CLIFF_SPACES}.")
    from drivers import deck_orchestrator
    try:
        deck_orchestrator.dj_engine.ramp_volume(_deck(), 0.0, 1.0)   # the song's job is done
    except Exception as e:
        print(f"[CLIFFHANGER] Deck fade failed: {e}")
    if target > start_at:
        # Wrong: the buzzer first, then the climb music and the slide (update() starts them).
        _sfx("buzzer.wav")
        _c.update(phase="buzz", climb_from=start_at, climb_to=target, buzz_until=now + config.CLIFF_BUZZ_SECONDS)
    else:
        if off == 0 and _c["song"] + 1 < config.CLIFF_SONGS:
            _sfx("bigWin.wav")      # spot on (the last song's win plays it from solo_challenge)
        _after_climb(now)
    return True, f"{off} year{'s' if off != 1 else ''} off."


def _sfx(name):
    try:
        from audio.audio_engine import load_sound, play_processed_sound
        play_processed_sound(load_sound(config.resource_path("audio", "sound_effects", name)), volume=1.0)
    except Exception as e:
        print(f"[CLIFFHANGER] {name} failed: {e}")


def _after_climb(now):
    _c["shown"] = _c["climb_to"] if _c["phase"] == "climb" else _c["shown"]
    if _c["total"] > config.CLIFF_SPACES:
        _c.update(phase="fall", fall_at=now)
        try:
            from audio.audio_engine import load_sound, play_processed_sound
            play_processed_sound(load_sound(config.resource_path("audio", "sound_effects", "wilhelm.wav")), volume=1.0)
        except Exception as e:
            print(f"[CLIFFHANGER] wilhelm.wav failed: {e}")
        print("[CLIFFHANGER] Over the edge -- he falls!")
    elif _c["song"] + 1 >= config.CLIFF_SONGS:
        _finish(True)
    else:
        _c.update(phase="between", next_at=now + config.CLIFF_NEXT_SONG_SECONDS)


def next_song():
    """Admin NEXT SONG after a guess."""
    if _c.get("phase") != "between":
        return False, "Not between songs."
    if not _load_song(_c["song"] + 1):
        return False, "No more songs with a known year."
    return True, ""


def _restore_audio():
    from audio.audio_engine import fade_out_game_music
    from drivers import deck_orchestrator
    fade_out_game_music(800)
    try:
        deck_orchestrator.dj_engine.ramp_volume(_deck(), 1.0, 1.5)
    except Exception:
        pass


def _finish(won):
    _c.update(phase="result", result="win" if won else "lose")
    _restore_audio()
    from drivers import solo_challenge
    solo_challenge.finish_external(won, f"{_c['total']} year{'s' if _c['total'] != 1 else ''} off in total")


def cancel():
    if active():
        _c["phase"] = ""
        _restore_audio()
    _pre["done_round"] = None     # a cancelled Cliffhanger round's pregame comes back up


def clear():
    """solo_challenge: the result screen is over."""
    _c["phase"] = ""


def update(now):
    ph = _c.get("phase")
    if ph == "buzz" and now >= _c.get("buzz_until", 0):
        from audio.audio_engine import play_random_game_music
        play_random_game_music(config.CLIFF_CLIMB_SOUND, loop=True)   # covers a long climb
        _c.update(phase="climb", t0=now)
    elif ph == "climb":
        dur = (_c["climb_to"] - _c["climb_from"]) * config.CLIFF_SECONDS_PER_SPACE
        if now - _c["t0"] >= dur:
            from audio.audio_engine import fade_out_game_music
            fade_out_game_music(600)
            _after_climb(now)
    elif ph == "fall" and now - _c["fall_at"] >= config.CLIFF_FALL_SECONDS:
        _finish(False)
    elif ph == "between" and _c.get("next_at") and now >= _c["next_at"]:
        _c["next_at"] = 0.0
        ok, why = next_song()            # the simple host page has no NEXT SONG button
        if not ok:
            print(f"[CLIFFHANGER] Auto next song failed: {why}")


def holds_song():
    return _c.get("phase") in ("listen", "buzz", "climb", "between", "fall")


def view():
    """The TV board / LED panels (None when not running)."""
    if not active():
        return None
    return {"phase": _c["phase"], "song": _c.get("song", 0) + 1, "songs": config.CLIFF_SONGS,
            "guess": _c.get("guess"), "year": _c.get("year") if _c.get("guess") is not None else None,
            "off": _c.get("off"), "total": _c.get("total", 0), "spaces": config.CLIFF_SPACES,
            "from": _c.get("climb_from", 0.0), "to": _c.get("climb_to", 0.0), "t0": _c.get("t0", 0.0),
            "shown": _c.get("shown", 0.0), "sps": config.CLIFF_SECONDS_PER_SPACE,
            "fall_at": _c.get("fall_at", 0.0), "result": _c.get("result", ""),
            # BEGIN GAME from the pregame: when the "What year did this song debut?" wipe started
            "q_t0": _c.get("q_t0", 0.0), "q_led": config.CLIFF_QUESTION_LED_SECONDS,
            "q_tv": config.CLIFF_TV_BOARD_SECONDS, "q_fade": config.CLIFF_TV_FADE_SECONDS,
            # the song is revealed with its year, once the guess is in
            "title": _c.get("title", "") if _c.get("guess") is not None else "",
            "artist": _c.get("artist", "") if _c.get("guess") is not None else ""}


def host_view():
    """/cliff, the simple host page: the song's title and artist (for the host only), never its year."""
    if not active():
        return None
    return {"phase": _c["phase"], "song": _c.get("song", 0) + 1, "songs": config.CLIFF_SONGS,
            "total": _c.get("total", 0), "spaces": config.CLIFF_SPACES, "result": _c.get("result", ""),
            "title": _c.get("title", ""), "artist": _c.get("artist", "")}


def admin_view():
    if not active():
        return None
    return {**view(), "title": _c.get("title", ""), "artist": _c.get("artist", ""),
            "song_year": _c.get("year"), "history": _c.get("history", [])}


def progress_text():
    if not active():
        return ""
    t = f"Song {_c.get('song', 0) + 1} of {config.CLIFF_SONGS} · {_c.get('total', 0)}/{config.CLIFF_SPACES} spaces"
    if _c.get("guess") is not None:
        t += f" · guessed {_c['guess']}, it was {_c['year']} ({_c['off']} off)"
    return t


def marquee_mode():
    ph = _c.get("phase")
    if ph == "climb" or (ph == "result" and _c.get("result") == "win"):
        return "chase"
    if ph == "fall" or (ph == "result" and _c.get("result") == "lose"):
        return "dark"
    return ""


# ------------------------------------------------------------------ pregame (Cliffhanger rounds)
# on: the title + chase are up and /cliff is the pregame panel. round: state.round_index it belongs to.
# vol_before: the volume FADE MUSIC came down from (None = not faded).
_pre = {"on": False, "round": None, "started": 0.0, "vol_before": None, "beginning": False, "done_round": None}


def pregame_active():
    """The pregame is up -- and still belongs to the running Cliffhanger round (END ROUND, a stopped
    show or an intermission drop it)."""
    if not _pre["on"]:
        return False
    if (_pre["round"] != state.round_index or state.round_type != "solo_challenge"
            or state.round_solo_game != "cliffhanger" or state.show_phase != "live" or state.show_background
            or state.intermission_active or state.win_sequence_active or active()):
        _pre["on"] = False
        _raise_volume()
        return False
    return True


def pregame_tick(now):
    """drivers/round_engine.py::update, every frame of a Cliffhanger round before its challenge has
    started: put the pregame up as soon as the round's music is playing."""
    if _pre["on"] or _pre["done_round"] == (state.show_session_id, state.round_index):
        pregame_active()
        return
    if not state.now_playing_path or state.show_first_song_pending_at or state.mode != state.MODE_DJ:
        return
    if state.medley_active or state.price_game_active or state.mystery_active or state.show_spin_active:
        return
    _pre.update(on=True, round=state.round_index, started=now, vol_before=None, beginning=False)
    if state.trivia_confirm_active:
        try:
            from inputs import gamepad
            gamepad.cancel_question()
        except Exception:
            pass
    print(f"[CLIFFHANGER] {state.round_name}: pregame -- title up, waiting for the host's BEGIN GAME.")


def _set_volume(v, seconds):
    from drivers import midi_driver
    state.music_volume = max(0, min(100, int(round(v))))
    midi_driver.tween_channel_faders_to(state.music_volume, seconds)


def pregame_fade():
    """FADE MUSIC: the music down to 1/4 of its volume (the old level is remembered)."""
    if not pregame_active():
        return False, "No Cliffhanger pregame running."
    if _pre["vol_before"] is None:
        _pre["vol_before"] = state.music_volume
        _set_volume(state.music_volume * config.CLIFF_PREGAME_FADE_FRACTION, config.CLIFF_PREGAME_FADE_SECONDS)
        print(f"[CLIFFHANGER] Pregame: music faded {_pre['vol_before']}% -> {state.music_volume}%.")
    return True, ""


def _raise_volume():
    if _pre["vol_before"] is not None:
        back = _pre["vol_before"]
        _pre["vol_before"] = None
        _set_volume(back, config.CLIFF_PREGAME_FADE_SECONDS)
        print(f"[CLIFFHANGER] Pregame: music back up to {back}%.")


def pregame_raise():
    """RAISE VOLUME: back to the level before FADE MUSIC."""
    if not pregame_active():
        return False, "No Cliffhanger pregame running."
    _raise_volume()
    return True, ""


def pregame_teams():
    """Tonight's teams, lowest night score first (the default pick is the first)."""
    rows = [{"player_id": pid, "name": p.get("name") or p.get("initials") or "???",
             "night": int(state.show_cumulative_scores.get(pid, 0)) + int(p.get("score", 0))}
            for pid, p in state.quiz_players.items()]
    rows.sort(key=lambda r: (r["night"], r["name"].lower()))
    return rows


def pregame_begin(player_id):
    """BEGIN GAME: volume back up, the challenge starts (its first song plays at once) and the
    question wipes across the panels. Returns (ok, message)."""
    if not pregame_active():
        return False, "No Cliffhanger pregame running."
    teams = pregame_teams()
    if not player_id and teams:
        player_id = teams[0]["player_id"]
    _raise_volume()
    from drivers import solo_challenge
    _pre["beginning"] = True
    try:
        ok, msg = solo_challenge.start("cliffhanger", player_id)
    finally:
        _pre["beginning"] = False
    if ok:
        _pre.update(on=False, done_round=(state.show_session_id, state.round_index))
        print("[CLIFFHANGER] Pregame over -- BEGIN GAME.")
    return ok, msg


def pregame_view():
    """/cliff's pregame panel (None when no pregame is up)."""
    if not pregame_active():
        return None
    return {"faded": _pre["vol_before"] is not None, "volume": state.music_volume,
            "volume_before": _pre["vol_before"], "teams": pregame_teams(), "round": state.round_name,
            "id": f"{state.show_session_id}:{_pre['round']}:{int(_pre['started'])}"}
