"""drivers/show_engine.py
Trivia Night show flow (2026-08-13): Setup -> Countdown -> scripted open
(ShowStart.mp3 choreography) -> live show (state.mode/win_sequence_engine/
etc keep working completely unchanged) -> scripted close (ShowEnd.mp3
choreography, crowns a cumulative-score champion) -> back to Setup.

Shaped like drivers/win_sequence_engine.py / drivers/westminster_engine.py:
a phase machine driven by state.show_phase + state.show_phase_started_at,
update(now) pumped once per frame from inputs/gamepad.py::process_events().
Rendering (LED matrix + DMX) lives in graphics/matrix_canvas.py and
drivers/lighting_engine.py respectively -- both read purely off state and
elapsed time, same precedent as drivers/westminster_engine.py's split.
"""
import time

import config
from state import state
from drivers import midi_driver, deck_orchestrator
from audio.audio_engine import (
    play_processed_sound, pick_random_applause,
    show_start_sound, show_end_sound,
)

# The Channel ShowStart.mp3 (intro) or ShowEnd.mp3 (outro) is playing on.
# Two jobs: _finish_intro() fades the intro one out at the exact moment the
# round begins instead of letting it just play out regardless of what the
# deck is doing, and update() below continuously re-syncs its volume to
# state.music_volume every frame -- play_processed_sound() only sets a
# channel's volume ONCE, at the instant it starts playing, which is fine
# for every OTHER sound in this app (a one-second ding/buzzer/applause
# clip) but not for these two, which can run for tens of seconds to
# several minutes (the intro has no fixed end time -- see skip_intro())
# with the operator expecting VOL +/- to actually do something the whole
# time, same as it does for the deck.
_show_music_channel = None

# Guards _start_outro_music() (below) so the delayed ShowEnd.mp3/applause
# start only ever fires once per outro, reset by start_outro() each time a
# new one begins.
_outro_music_started = False


def start_intro():
    """'Start Game' (immediate) or a scheduled countdown reaching zero /
    'Start Now'. Resets everything that means "fresh night": round
    numbering back to 1, the cumulative scoreboard cleared, every player's
    per-round score cleared. Deck stays silent -- ShowStart.mp3 plays as a
    one-shot SFX, not through the deck channels -- until _finish_intro()
    hands off into the first real track at the scripted end of the intro."""
    if state.show_phase not in ("setup", "countdown", "outro"):
        return  # already intro/live, or a stray call -- never re-fire mid-show
    global _show_music_channel
    now = time.time()
    state.show_phase = "intro"
    state.show_phase_started_at = now
    state.show_scheduled_start_at = 0.0
    state.game_round_number = 1
    state.show_cumulative_scores = {}
    state.game_winner_player_id = ""
    for player in state.quiz_players.values():
        player["score"] = 0
    _show_music_channel = play_processed_sound(show_start_sound)
    print("[SHOW] Intro started.")


def schedule_start(target_epoch):
    """'Start Game at 7PM' -- arms the countdown page. Valid from the Setup
    page itself, or right after a previous show's outro (which -- like
    start_intro() below -- stays "outro" indefinitely for the LED contact
    card rather than auto-reverting to "setup", so this needs the same
    allowance or scheduling the NEXT show would silently fail forever
    after the first one ends)."""
    if state.show_phase not in ("setup", "outro"):
        return False
    state.show_phase = "countdown"
    state.show_phase_started_at = time.time()
    state.show_scheduled_start_at = target_epoch
    return True


def abort_schedule():
    """Countdown page's ABORT button -- back to Setup, no show started."""
    if state.show_phase != "countdown":
        return False
    state.show_phase = "setup"
    state.show_scheduled_start_at = 0.0
    return True


def accumulate_round_scores():
    """Folds every player's CURRENT per-round score into the night-long
    cumulative tally. Called the instant a round's winner is detected
    (drivers/win_sequence_engine.py::start(), before the per-round score
    reset that follows it) and from stop_show() below (covers a manual
    mid-round stop where nobody reached game_win_score yet, so this is the
    only place those partial points get preserved)."""
    for player_id, player in state.quiz_players.items():
        score = player.get("score", 0)
        if score:
            state.show_cumulative_scores[player_id] = (
                state.show_cumulative_scores.get(player_id, 0) + score
            )


def stop_show():
    """Manual STOP GAME (web remote, 'live' phase only) -- immediate, no
    "finish this round first" grace period, same philosophy as
    inputs/gamepad.py::abort_game_mode_early() (which this reuses for the
    live-question/grading cleanup, then layers the win-sequence/
    intermission cleanup on top since abort_game_mode_early() was only ever
    written to interrupt a live question, not a duck/applause/intermission
    already in flight)."""
    if state.show_phase != "live":
        return False
    accumulate_round_scores()

    from inputs import gamepad  # lazy: gamepad imports this module for update()
    gamepad.abort_game_mode_early()

    state.win_sequence_active = False
    state.win_sequence_phase = ""
    state.intermission_active = False
    state.intermission_paused = False
    state.intermission_paused_remaining = 0.0
    state.game_winner_player_id = ""
    for player in state.quiz_players.values():
        player["score"] = 0

    start_outro()
    return True


def start_outro():
    """Fades the live deck out; ShowEnd.mp3 + a random applause clip don't
    start until config.SHOW_OUTRO_DECK_FADE_SECONDS later (see
    _start_outro_music(), fired from update() once that elapses), by which
    point the deck's been force-stopped outright -- a deliberate SEQUENTIAL
    handoff (2026-08-13), not the round-win duck's "new cue plays over the
    still-ducking old one" shape, so the outgoing track can never mash into
    the new music. Computes the champion banner text and stamps when the
    LED/DMX should drop from the active choreography into their indefinite
    post-show hold (contact-card text, DMX blacked out) -- see
    _render_show_phase (matrix_canvas.py) and _render_show_dmx
    (lighting_engine.py), both of which read state.show_outro_music_ends_at
    directly rather than needing another per-frame check here."""
    if state.show_phase == "outro":
        return
    now = time.time()
    state.show_phase = "outro"
    state.show_phase_started_at = now
    state.show_final_round_number = None

    global _show_music_channel, _outro_music_started
    _show_music_channel = None
    _outro_music_started = False
    midi_driver.tween_channel_faders_to(0, config.SHOW_OUTRO_DECK_FADE_SECONDS)

    state.show_outro_message = _compute_champion_message()
    state.show_outro_music_ends_at = (
        now + config.SHOW_OUTRO_DECK_FADE_SECONDS + show_end_sound.get_length())
    print(f"[SHOW] Outro started -- {state.show_outro_message!r}")


def _start_outro_music():
    """Fired once, config.SHOW_OUTRO_DECK_FADE_SECONDS after start_outro()
    -- the deck's fade-to-0 has finished by now. Force-stops it outright
    (audio.dj_engine.DJEngine.stop_decks(): a 0% fader still leaves the
    Sound technically playing, just inaudible) before starting ShowEnd.mp3
    + applause, so the new music starts into real silence instead of
    layering over whatever's left of the outgoing track."""
    global _show_music_channel
    deck_orchestrator.dj_engine.stop_decks()
    _show_music_channel = play_processed_sound(show_end_sound)
    play_processed_sound(pick_random_applause())


def _compute_champion_message():
    scores = state.show_cumulative_scores
    if not scores:
        return "THANKS FOR PLAYING!"
    top = max(scores.values())
    initials = [
        state.quiz_players[pid]["initials"]
        for pid, s in scores.items()
        if s == top and pid in state.quiz_players
    ]
    if not initials:
        return "THANKS FOR PLAYING!"
    names = " & ".join(initials)
    verb = "WINS" if len(initials) == 1 else "WIN"
    return f"{names} {verb} TRIVIA NIGHT!"


def skip_intro():
    """Joy X- pressed while the intro is active (inputs/gamepad.py's
    JOYAXISMOTION handler) -- ends the scripted-open choreography and hands
    off into live gameplay right then, whatever elapsed time it happens to
    be. There's no fixed end timestamp anymore (2026-08-13): the operator's
    live spoken intro over the ShowStart.mp3 music needs open-ended room,
    not a hard cutoff, so this manual trigger is the only thing that ends
    the intro now -- see SHOW_INTRO_BEATS' open-ended last beat in
    config.py and graphics/matrix_canvas.py's matching window-end logic.
    No-op outside the intro phase."""
    if state.show_phase != "intro":
        return
    _finish_intro(time.time())


def _finish_intro(now):
    """Blank the LEDs (handled automatically -- state.show_phase == "live"
    falls through to the normal DJ-idle rendering, see
    graphics/matrix_canvas.py), fade out ShowStart.mp3 right as the round
    begins, tween the deck's channel fader back up to state.music_volume
    (a previous show's outro leaves it faded to 0 -- nothing else ever
    restores it, so without this the first track of a new show plays back
    silently), and hand off into the first real track. Sweeper-only
    (whoosh transition, no spoken announcement) rather than announced --
    2026-08-14 fix: a spoken announcement here stepped on the live host's
    own intro, but the sweeper's transition moment and a "GET READY" LED
    banner are still wanted for this first song specifically (see
    deck_orchestrator.trigger_sweeper_only_track_move()). Every later
    transition that night still goes through the normal
    trigger_track_move()/Auto-DJ path (announced whenever
    state.auto_announce_enabled is on), unaffected by this. Only ever
    called from skip_intro() above."""
    global _show_music_channel
    state.show_phase = "live"
    state.show_phase_started_at = now
    if _show_music_channel is not None:
        _show_music_channel.fadeout(config.SHOW_INTRO_HANDOFF_FADE_MS)
        _show_music_channel = None
    midi_driver.tween_channel_faders_to(
        state.music_volume, config.SHOW_INTRO_HANDOFF_FADE_MS / 1000.0)
    deck_orchestrator.trigger_sweeper_only_track_move()
    print("[SHOW] Intro finished -- live show begins.")


def update(now):
    """Per-frame pump, called from inputs/gamepad.py::process_events()."""
    if (state.show_phase == "countdown"
            and state.show_scheduled_start_at
            and now >= state.show_scheduled_start_at):
        start_intro()

    global _outro_music_started
    if (state.show_phase == "outro" and not _outro_music_started
            and now - state.show_phase_started_at >= config.SHOW_OUTRO_DECK_FADE_SECONDS):
        _outro_music_started = True
        _start_outro_music()

    global _show_music_channel
    if _show_music_channel is not None:
        if state.show_phase == "intro" or (
                state.show_phase == "outro" and now < state.show_outro_music_ends_at):
            _show_music_channel.set_volume(state.music_volume / 100.0)
        else:
            # Phase moved on (intro handed off via _finish_intro()'s own
            # fadeout, or the outro song finished) -- stop touching it.
            _show_music_channel = None
