"""drivers/show_engine.py
Trivia Night show flow (2026-08-13, "dark" phase added 2026-08-18): Setup
-> Countdown -> Dark (silent blank-panel/no-DMX pause, entered by "Start
Game"/countdown-zero, manually advanced by next-track) -> scripted open
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


def enter_dark():
    """'Start Game' (immediate) or a scheduled countdown reaching zero /
    'Start Now'. Resets everything that means "fresh night": round
    numbering back to 1, the cumulative scoreboard cleared, every player's
    per-round score cleared. Enters the "dark" phase -- a deliberate,
    silent, blank-panel/no-DMX pause (2026-08-18) so the audience has a
    beat of anticipation before anything starts, rather than ShowStart.mp3
    firing the instant the operator clicks a button. The operator then
    manually cues the actual intro via next-track (begin_intro() below,
    either the physical joystick's Joy X- or the web remote's equivalent
    while show_phase == "dark") -- see graphics/matrix_canvas.py's
    _render_show_phase and drivers/lighting_engine.py's _render_show_dmx
    for how "dark" renders as literally nothing on both.

    2026-08-20 fix: reachable from "outro" (STOP GAME -> start_outro() ->
    ShowEnd.mp3 + applause, which just plays out on its own timeline --
    nothing was ever stopping it early). If the operator hits Start Game
    again before that outro track finishes on its own, dark's "deliberate
    silence" doesn't actually happen with a previous show's outro still
    audibly running underneath it. Fades that out fast (not an abrupt
    cut) and force-stops the deck too, in case Start Game landed inside
    start_outro()'s own SHOW_OUTRO_DECK_FADE_SECONDS window, before
    update()'s own outro-music/deck-stop timer ever got to fire -- once
    show_phase flips to "dark" here, that timer's own "if state.show_phase
    == 'outro'" guard stops checking entirely, so nothing else would ever
    clean either of those up."""
    if state.show_phase not in ("setup", "countdown", "outro"):
        return  # already dark/intro/live, or a stray call -- never re-fire mid-show
    now = time.time()

    global _show_music_channel
    if _show_music_channel is not None:
        _show_music_channel.fadeout(config.SHOW_DARK_ENTRY_FADE_MS)
        _show_music_channel = None
    deck_orchestrator.dj_engine.stop_decks()

    state.show_phase = "dark"
    state.show_phase_started_at = now
    state.show_scheduled_start_at = 0.0
    state.game_round_number = 1
    state.show_cumulative_scores = {}
    state.game_winner_player_id = ""
    for player in state.quiz_players.values():
        player["score"] = 0
    print("[SHOW] Entered dark/waiting state.")


def begin_intro():
    """Next-track (physical joystick's Joy X- or the web remote) pressed
    while show_phase == "dark" -- the operator's manual cue to actually
    start the scripted open. Split out of enter_dark() above (2026-08-18)
    specifically so there's a deliberate silent pause between "Start Game"
    being pressed and the show actually beginning, rather than the two
    being the same instant. Deck stays silent -- ShowStart.mp3 plays as a
    one-shot SFX, not through the deck channels -- until _finish_intro()
    hands off into the first real track at the scripted end of the intro
    (skip_intro() below, itself unchanged by this split). No-op outside
    the dark phase."""
    if state.show_phase != "dark":
        return
    global _show_music_channel
    now = time.time()
    state.show_phase = "intro"
    state.show_phase_started_at = now
    _show_music_channel = play_processed_sound(show_start_sound)
    print("[SHOW] Intro started.")


def mark_operator_interaction():
    """Resets the unattended-autoplay idle clock -- call from anywhere an
    operator (physical rig input or a web remote action) does something.
    Cheap no-op outside the "setup" phase (the clock isn't running then
    anyway, see _update_unattended_autoplay() below), so callers don't
    need to check show_phase themselves before calling this.

    time.monotonic(), not time.time() -- see state.last_operator_
    interaction_at's own comment for why (a wall-clock NTP step on a
    cold-boot Pi made this idle clock fire instantly, well before the
    on-screen countdown had actually finished)."""
    state.last_operator_interaction_at = time.monotonic()


def _update_unattended_autoplay(now):
    """Polled every frame from update() below. If the Setup/"READY" screen
    sits untouched (no mark_operator_interaction() call -- physical rig
    input or a web remote action) for
    config.SHOW_UNATTENDED_AUTOPLAY_TIMEOUT_SECONDS, starts the show
    itself via _enter_unattended_autoplay() -- so the room doesn't sit on
    dead air if the host hasn't arrived yet.

    `now` (wall-clock, time.time()) is only ever handed to
    _enter_unattended_autoplay() below to seed show_phase_started_at, which
    has to line up with every other phase's wall-clock timestamps -- the
    idle-elapsed comparison itself uses its own time.monotonic() reading
    (see state.last_operator_interaction_at's comment), not `now`."""
    if state.show_phase != "setup":
        # Not idle-timing outside Setup -- 0.0 sentinel so re-entering
        # Setup later (Stop Game, a finished outro, an aborted countdown,
        # or reset_unattended_autoplay() below) starts a fresh clock
        # instead of picking up a stale one.
        state.last_operator_interaction_at = 0.0
        return
    if state.setup_confirm_active:
        # "START SHOW NOW?" confirm open (drivers/simon_engine.py::
        # _poll_setup_hardware(), green button) -- pause the idle clock
        # rather than letting it fire out from under the confirm screen.
        # graphics/matrix_canvas.py::_render_setup_confirm() replaces the
        # AUTO countdown banner entirely while this is active, so an
        # operator who opened the confirm and hesitated had no visible
        # timer left to warn them the show was about to auto-start anyway
        # (2026-09-21 fix -- confirmed "occasional" because it only bites
        # when the confirm is opened with the idle timeout already close to
        # firing). Not resetting last_operator_interaction_at here: however
        # the confirm resolves already handles the clock correctly on its
        # own (red -> trigger_unattended_autoplay_now() fires immediately
        # on purpose; anything else -> mark_operator_interaction() resets
        # it fresh, drivers/simon_engine.py::_poll_setup_hardware()) --
        # simply not evaluating the timeout while paused is enough.
        return
    mono_now = time.monotonic()
    if state.last_operator_interaction_at == 0.0:
        state.last_operator_interaction_at = mono_now
        return
    if mono_now - state.last_operator_interaction_at >= config.SHOW_UNATTENDED_AUTOPLAY_TIMEOUT_SECONDS:
        _enter_unattended_autoplay(now)


def trigger_unattended_autoplay_now():
    """Public wrapper for _enter_unattended_autoplay() (2026-09-16): the
    physical rig's green-button "START SHOW NOW?" confirm (drivers/
    simon_engine.py::_poll_setup_hardware()) needs to skip straight to
    unattended autoplay the instant the operator confirms Yes, rather than
    waiting out the rest of config.SHOW_UNATTENDED_AUTOPLAY_TIMEOUT_SECONDS.
    Same "default options" show start as the idle timeout itself -- no-op
    if show_phase has somehow already left "setup" by the time this fires
    (e.g. a stale double-press)."""
    if state.show_phase != "setup":
        return
    _enter_unattended_autoplay(time.time())


def _enter_unattended_autoplay(now):
    """SHOW_UNATTENDED_AUTOPLAY_TIMEOUT_SECONDS of untouched Setup-page
    idle time: starts the show like "Start Game" would (same fresh-night
    reset enter_dark() does), but skips straight to "live" -- no dark
    pause, no ShowStart.mp3, no LED intro beats/DMX flash-chase. The
    trivia/mystery-band engines aren't touched here at all -- they run off
    state.mode == MODE_DJ regardless of show_phase, so the full show
    (including quiz rounds) fires normally once "live" starts, exactly as
    if a host had pressed Start Game."""
    state.show_phase = "live"
    state.show_phase_started_at = now
    state.show_scheduled_start_at = 0.0
    state.game_round_number = 1
    state.show_cumulative_scores = {}
    state.game_winner_player_id = ""
    for player in state.quiz_players.values():
        player["score"] = 0

    state.show_unattended_autoplay = True
    state.show_unattended_autoplay_started_at = now

    # Same handoff _finish_intro() does at the end of a real intro -- a
    # previous show's outro (or a fresh app launch, since the fader
    # defaults to 100 -- see drivers/midi_driver.py's _current_fader_pct)
    # can leave the channel fader anywhere, so make sure it's actually
    # audible, then hand off into the first real track with the same
    # sweeper-only transition every first track of the night gets (a
    # brief whoosh + "GET READY" LED banner) -- NOT the flashy intro DMX
    # flash/chase, which only begin_intro()/skip_intro() ever trigger.
    midi_driver.tween_channel_faders_to(
        state.music_volume, config.SHOW_INTRO_HANDOFF_FADE_MS / 1000.0)
    deck_orchestrator.trigger_sweeper_only_track_move()
    print(f"[SHOW] {config.SHOW_UNATTENDED_AUTOPLAY_TIMEOUT_SECONDS:.0f}s of Setup-page "
          "inactivity -- unattended autoplay started.")


# Set by reset_unattended_autoplay() to the epoch the fade-out it starts
# will finish; polled by update() to actually stop the decks once silent
# (a 0% fader still leaves the Sound technically playing -- same two-step
# _outro_music_started/_start_outro_music() already does for the normal
# outro's fade). 0.0 = nothing pending.
_unattended_reset_stop_at = 0.0


def reset_unattended_autoplay():
    """Host's "Reset to Setup" button (web remote live-panel banner, shown
    while state.show_unattended_autoplay is True) -- fades the deck out
    fast and drops straight back to the blank Setup/"READY" screen,
    skipping the outro's champion banner/ShowEnd.mp3/applause entirely:
    unattended autoplay never had a host running it, so it doesn't deserve
    that treatment. No-op if the show is live for any OTHER reason -- a
    real host-started show must go through stop_show() -> the normal
    outro instead. Returns True if it actually reset anything."""
    if not state.show_unattended_autoplay or state.show_phase != "live":
        return False

    # No accumulate_round_scores() here (unlike stop_show()) -- that tally
    # only ever feeds the outro's champion banner, and this path skips the
    # outro entirely. _enter_unattended_autoplay() already zeroed
    # show_cumulative_scores when this session started, and the next real
    # "Start Game" zeroes it again -- nothing reads it in between.
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

    state.show_unattended_autoplay = False
    state.show_unattended_autoplay_started_at = 0.0
    state.show_phase = "setup"
    state.show_phase_started_at = time.time()
    # Explicit 0.0 reset (not left to _update_unattended_autoplay()'s own
    # "not setup" branch): this function jumps live -> setup in one shot
    # from an HTTP handler, not through a phase update() itself pumps every
    # frame, so nothing else is guaranteed to have zeroed a stale timestamp
    # first -- without this, a last_operator_interaction_at left over from
    # arbitrarily long ago could make the very next frame think the new
    # Setup screen has already been idle past the timeout and re-trigger
    # autoplay instantly.
    state.last_operator_interaction_at = 0.0

    global _unattended_reset_stop_at
    midi_driver.tween_channel_faders_to(0, config.SHOW_UNATTENDED_RESET_FADE_SECONDS)
    _unattended_reset_stop_at = time.time() + config.SHOW_UNATTENDED_RESET_FADE_SECONDS
    print("[SHOW] Unattended autoplay reset -- back to Setup.")
    return True


def schedule_start(target_epoch):
    """'Start Game at 7PM' -- arms the countdown page. Valid from the Setup
    page itself, or right after a previous show's outro (which -- like
    enter_dark() below -- stays "outro" indefinitely for the LED contact
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
    state.auto_announce_enabled is on), unaffected by this. Called from
    skip_intro() above (manual early end) or update() below (automatic
    fallback once ShowStart.mp3 finishes playing on its own)."""
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
        enter_dark()

    _update_unattended_autoplay(now)

    global _unattended_reset_stop_at
    if _unattended_reset_stop_at and now >= _unattended_reset_stop_at:
        _unattended_reset_stop_at = 0.0
        deck_orchestrator.dj_engine.stop_decks()

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

    # If nobody ever pressed skip and ShowStart.mp3 simply finished playing
    # on its own, the show would otherwise sit stuck in "intro" forever --
    # nothing else transitions it. Auto-advance exactly as if skip had been
    # pressed the instant the recorded intro track runs out.
    if (state.show_phase == "intro" and _show_music_channel is not None
            and not _show_music_channel.get_busy()):
        _finish_intro(now)
