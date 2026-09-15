import random
import time

import config
from state import state
from drivers import simon_hardware
from drivers import midi_driver
from drivers import relay_engine
from audio.audio_engine import simon_sounds, simon_intro_sound, play_processed_sound

# ==========================================
# MILTON BRADLEY "SIMON" MINI-GAME
# ==========================================
# Two entry points share this one simulation:
#   - enter_simon(): DJ-mode secret combo (config.SIMON_ENTRY_BUTTONS,
#     inputs/gamepad.py) -- unchanged "Easter egg" shape, starts playback
#     immediately, and still auto-restarts a fresh round after a miss.
#   - enter_simon_hardware(): any of the 4 physical arcade buttons
#     (config.SIMON_HW_BUTTON_PINS, polled by poll_hardware() below) --
#     runs the "It's Simon!" intro first (see _update_intro()), and a miss
#     ends the game/returns to DJ mode instead of auto-restarting, matching
#     a real cabinet's "game over, press a button to play again" shape.
# state.simon_source records which path is live so update()'s "fail" phase
# knows which of those two endings to take.
#
# Rendering lives in graphics/matrix_canvas.py::_render_simon (matrix
# panels 3-6 + top-strip banner) and drivers/wled_engine.py::_apply_simon
# (marquee outline colors) -- both driven straight off state.simon_active_pad
# and state.simon_phase, same "engine owns state, renderers paint the
# current snapshot" split every other mode in this project already uses.
# Physical LEDs (drivers/simon_hardware.py) are driven directly from here
# instead, since they're this engine's own hardware, not a renderer's.

_PADS = 4
_last_update_at = None
_intro_channel = None  # Channel simon_intro_sound is playing on, or None
_prev_hw_buttons = {}
_held_channel = None  # Channel a physically-held button's sustained tone is playing on, or None
_held_color = None    # which color is currently held/sustaining, or None


def _pad_color(pad):
    return config.SIMON_HW_COLOR_ORDER[pad]


def _start_held_sound(color, sound):
    """Starts (or restarts) a physically-held button's sustained tone/LED
    -- loops the sample indefinitely (audio/audio_engine.py::
    play_processed_sound's loops=-1) so a hold longer than the sample's
    natural length just keeps sounding, as one continuous tone, same as
    the real 1978 device. release() below cuts both the instant that
    physical button comes back up -- see poll_hardware()."""
    global _held_channel, _held_color
    _stop_held_sound()
    _held_channel = play_processed_sound(sound, volume=config.SIMON_SOUND_VOLUME, loops=-1)
    _held_color = color
    simon_hardware.set_led(color, True)


def _stop_held_sound():
    """Immediately cuts whatever's currently sustaining (if anything) --
    called on release, and defensively on every game-start/game-end
    transition so a held sound can never survive past the hold that
    started it."""
    global _held_channel, _held_color
    if _held_channel is not None:
        _held_channel.stop()
    if _held_color is not None:
        simon_hardware.set_led(_held_color, False)
    _held_channel = None
    _held_color = None


def _duck_music():
    """Fades the DJ deck to silent for the duration of a Simon game (entry
    through end) -- same shape as Price Game's background-music duck.
    update_fader_tween() is already pumped every frame by drivers/
    price_game_engine.py::update() (called unconditionally from inputs/
    gamepad.py::process_events(), not gated on Price Game being active),
    so no separate per-frame hook is needed here."""
    midi_driver.tween_channel_faders_to(0, config.SIMON_MUSIC_DUCK_TWEEN_SECONDS)


def _restore_music():
    """Fades the DJ deck back to the admin's live master volume. Called
    whenever a Simon game actually ENDS (explicit exit, hardware loss, or
    an input timeout) -- NOT on the joystick combo's auto-restart-after-miss
    path, which keeps playing (and so stays ducked) into a fresh round."""
    midi_driver.tween_channel_faders_to(state.music_volume, config.SIMON_MUSIC_RESTORE_TWEEN_SECONDS)


def _start_playback_step(now):
    idx = state.simon_playback_index
    pad = state.simon_sequence[idx]
    state.simon_active_pad = pad
    state.simon_active_pad_until = now + config.SIMON_FLASH_ON_SECONDS
    play_processed_sound(simon_sounds[pad], volume=config.SIMON_SOUND_VOLUME)
    simon_hardware.pulse_led(_pad_color(pad), duration=config.SIMON_FLASH_ON_SECONDS)


def _start_intro_step(now):
    """One step of the intro's continuous green-red-yellow-blue rotation
    (state.simon_playback_index free-runs 0-3-0-3... here, not tied to any
    real sequence yet) -- drives the physical LED the same way
    _start_playback_step() does; the matrix panel accent and marquee panel
    color follow state.simon_active_pad automatically via their own
    renderers."""
    pad = state.simon_playback_index % _PADS
    state.simon_active_pad = pad
    state.simon_active_pad_until = now + config.SIMON_FLASH_ON_SECONDS
    simon_hardware.pulse_led(_pad_color(pad), duration=config.SIMON_FLASH_ON_SECONDS)


def _begin_game(now):
    """Shared "start round 1" setup for both entry paths -- called directly
    by enter_simon() (joystick, no intro) and by _update_intro() once the
    hardware intro finishes."""
    state.simon_sequence = [random.randrange(_PADS)]
    state.simon_round = 1
    state.simon_phase = "playback"
    state.simon_playback_index = 0
    state.simon_playback_step_started_at = now
    state.simon_input_index = 0
    state.simon_active_pad = None
    state.simon_active_pad_held = False
    _stop_held_sound()
    _start_playback_step(now)


def enter_simon():
    """Combo entry hook (inputs/gamepad.py): config.SIMON_ENTRY_BUTTONS
    held simultaneously while in DJ_MODE. No intro -- starts playback
    immediately, same as before this module grew a hardware entry path."""
    global _last_update_at
    now = time.time()
    state.mode = state.MODE_SIMON
    state.simon_source = "joystick"
    _last_update_at = None
    _duck_music()
    _begin_game(now)
    print("GAME MODE TRANSITION: Simon initialized via secret combo")


def enter_simon_hardware():
    """Physical-button entry hook (poll_hardware() below, any of the 4
    arcade buttons): arms the "It's Simon!" intro -- top-strip banner/white
    marquee chase plus a continuous green-red-yellow-blue rotation across
    panels 3-6 (marquee color, matrix accent, and the physical LEDs
    together) while audio/gameMusic/simon.wav plays -- then falls into
    _begin_game() the instant that finishes (see _update_intro())."""
    global _last_update_at, _intro_channel
    now = time.time()
    state.mode = state.MODE_SIMON
    state.simon_source = "hardware"
    state.simon_phase = "intro"
    state.simon_intro_started_at = now
    state.simon_playback_index = 0
    state.simon_playback_step_started_at = now
    state.simon_active_pad = None
    state.simon_active_pad_held = False
    _stop_held_sound()
    _last_update_at = None
    _duck_music()
    _intro_channel = play_processed_sound(simon_intro_sound, volume=config.SIMON_SOUND_VOLUME)
    _start_intro_step(now)
    print("GAME MODE TRANSITION: Simon initialized via hardware button press")


def exit_simon():
    """Exit hook (inputs/gamepad.py, simon_exit_1/simon_exit_2): immediately
    halts the game loop and returns to DJ_MODE -- normal DJ visuals/
    lighting resume on the very next frame, same as Space Invaders exit."""
    state.simon_sequence = []
    state.simon_active_pad = None
    state.simon_active_pad_held = False
    _stop_held_sound()
    state.mode = state.MODE_DJ
    _restore_music()
    print("SIMON EXIT: Returned to DJ Mode")


def press(pad, hold=False):
    """Registers a player guess for pad 0-3 -- fed from either the
    joystick's simon_select_1..4 bindings (inputs/gamepad.py, hold=False,
    unchanged fixed-duration flash-and-play since a joystick release isn't
    tracked anywhere in this codebase) or the physical arcade buttons
    (poll_hardware() below, hold=True: the pad's LED/tone sustain for as
    long as the button stays down -- release() cuts them the instant it
    comes back up, closer to the real 1978 device than a fixed flash).
    Both are live whenever state.mode == MODE_SIMON, regardless of which
    one started the game. No-op outside the "input" phase (playback still
    running, or a miss is being displayed)."""
    if state.simon_phase != "input":
        return
    now = time.time()
    state.simon_last_input_at = now
    state.simon_active_pad = pad
    state.simon_active_pad_held = hold
    color = _pad_color(pad)
    if not hold:
        state.simon_active_pad_until = now + config.SIMON_FLASH_ON_SECONDS
        simon_hardware.pulse_led(color, duration=config.SIMON_FLASH_ON_SECONDS)

    expected = state.simon_sequence[state.simon_input_index]
    if pad != expected:
        state.simon_phase = "fail"
        state.simon_fail_started_at = now
        state.simon_active_pad_held = False  # _update_fail owns simon_active_pad exclusively from here
        # Plays the CORRECT pad's sound (not the pressed one) -- the
        # repeating flash in _update_fail is visual-only after this.
        if hold:
            _start_held_sound(color, simon_sounds[expected])
        else:
            play_processed_sound(simon_sounds[expected], volume=config.SIMON_SOUND_VOLUME)
        print(f"[SIMON] Miss on step {state.simon_input_index} -- round {state.simon_round} over")
        return

    if hold:
        _start_held_sound(color, simon_sounds[pad])
    else:
        play_processed_sound(simon_sounds[pad], volume=config.SIMON_SOUND_VOLUME)
    state.simon_input_index += 1
    if state.simon_input_index >= len(state.simon_sequence):
        state.simon_round += 1
        state.simon_sequence.append(random.randrange(_PADS))
        state.simon_phase = "playback"
        relay_engine.pulse_point_relay()
        state.simon_playback_index = -1  # sentinel: round-break pending, see update()'s "playback" branch
        # Deliberately NOT clearing state.simon_active_pad here -- it's
        # still lit/sounding from this exact press, and clearing it now
        # would stomp that out before a single frame ever rendered it.
        # update()'s "playback" branch lets it finish naturally (a fixed
        # flash for hold=False, or the player's own release for hold=True
        # -- see release() below) THEN starts the break.
        state.simon_playback_step_started_at = now
        print(f"[SIMON] Round cleared -- {config.SIMON_ROUND_BREAK_SECONDS}s break, then round {state.simon_round}")


def release(color):
    """Physical-button release hook (poll_hardware() below): if `color` is
    the button currently sustaining a held tone/LED (_start_held_sound()),
    cuts both immediately -- the "stop the instant you let go" half of the
    1978-device emulation. Only clears state.simon_active_pad/advances the
    round-break timer if state.simon_active_pad_held is still True, i.e.
    this hold hasn't already been superseded by the fail flash (a miss
    flips simon_active_pad_held False right away in press(), since
    _update_fail owns simon_active_pad exclusively from that point --
    release() here should still cut the held MISS sound/LED, just not
    touch simon_active_pad anymore)."""
    if color != _held_color:
        return
    _stop_held_sound()
    if state.simon_active_pad_held:
        state.simon_active_pad = None
        state.simon_active_pad_held = False
        if state.simon_phase == "playback" and state.simon_playback_index < 0:
            # This was the round's clearing press -- the break timer starts
            # now that the button has actually come back up, not whenever
            # the press itself happened.
            state.simon_playback_step_started_at = time.time()


def _update_intro(now):
    """Advances the hardware intro's continuous pad rotation (reuses the
    same on/gap timing as a real playback step) and, once audio/gameMusic/
    simon.wav finishes playing (or, failing that, a fallback/hard-cap timer
    so a missing/stuck audio asset can never hang the intro forever), hands
    off to the "get_ready" pause rather than starting the game directly."""
    if state.simon_active_pad is not None:
        if now >= state.simon_active_pad_until:
            state.simon_active_pad = None
            state.simon_playback_step_started_at = now
    else:
        if now - state.simon_playback_step_started_at >= config.SIMON_FLASH_GAP_SECONDS:
            state.simon_playback_index += 1
            _start_intro_step(now)

    elapsed = now - state.simon_intro_started_at
    if _intro_channel is not None:
        intro_done = not _intro_channel.get_busy()
    else:
        intro_done = elapsed >= config.SIMON_INTRO_FALLBACK_SECONDS
    intro_done = intro_done or elapsed >= config.SIMON_INTRO_MAX_SECONDS

    if intro_done:
        state.simon_phase = "get_ready"
        state.simon_get_ready_started_at = now
        state.simon_active_pad = None


def _update_get_ready(now):
    """Pause after the intro jingle ends and before round 1's first tone
    plays (config.SIMON_GET_READY_SECONDS) -- "GET READY" banner (graphics/
    matrix_canvas.py::_render_simon) and the marquee's theater chase
    (drivers/wled_engine.py::_apply_simon) keep running, no pad lit, until
    this elapses, then falls into _begin_game() same as the old direct
    intro->game transition did."""
    if now - state.simon_get_ready_started_at >= config.SIMON_GET_READY_SECONDS:
        _begin_game(now)


def _update_fail(now):
    """Loss sequence: the correct answer's LED (physical + matrix accent +
    marquee panel, all keyed off state.simon_active_pad/simon_hardware.
    set_led()) flashes at config.SIMON_LOSS_FLASH_PERIOD_SECONDS/_DUTY for
    SIMON_FAIL_HOLD_SECONDS, then either hands off to "score_review"
    (hardware entry -- see _update_score_review()) or starts a fresh round
    immediately (joystick combo, unchanged Easter-egg behavior)."""
    correct_pad = state.simon_sequence[state.simon_input_index]
    color = _pad_color(correct_pad)
    on_seconds = config.SIMON_LOSS_FLASH_PERIOD_SECONDS * config.SIMON_LOSS_FLASH_DUTY
    cycle = (now - state.simon_fail_started_at) % config.SIMON_LOSS_FLASH_PERIOD_SECONDS
    lit = cycle < on_seconds
    state.simon_active_pad = correct_pad if lit else None
    simon_hardware.set_led(color, lit)

    if now - state.simon_fail_started_at < config.SIMON_FAIL_HOLD_SECONDS:
        return

    simon_hardware.set_led(color, False)
    if state.simon_source == "hardware":
        state.simon_active_pad = None
        state.simon_phase = "score_review"
        state.simon_score_review_started_at = now
    else:
        state.simon_sequence = [random.randrange(_PADS)]
        state.simon_round = 1
        state.simon_phase = "playback"
        state.simon_playback_index = 0
        state.simon_playback_step_started_at = now
        _start_playback_step(now)


def _update_score_review(now):
    """Extended hold on the final score display after a hardware loss
    (config.SIMON_SCORE_REVIEW_SECONDS) -- the round count stays on screen
    (graphics/matrix_canvas.py::_render_simon) and the marquee's theater
    chase resumes around it (drivers/wled_engine.py::_apply_simon, which
    was off for the whole "playback"/"input"/"fail" span) before the game
    actually ends and returns to DJ mode."""
    if now - state.simon_score_review_started_at < config.SIMON_SCORE_REVIEW_SECONDS:
        return
    rounds_cleared = state.simon_round - 1
    state.simon_sequence = []
    state.mode = state.MODE_DJ
    _restore_music()
    print(f"[SIMON] Hardware game over -- {rounds_cleared} round(s) cleared. Returning to DJ mode.")


def _handle_input_timeout(now):
    """No button pressed for config.SIMON_INPUT_TIMEOUT_SECONDS while
    waiting on the player -- an immediate, unceremonious return to DJ mode
    (no flash/announcement, unlike a miss) regardless of which entry path
    started the game (still experimental/tunable per operator feedback,
    2026-09-13)."""
    rounds_cleared = state.simon_round - 1
    state.simon_sequence = []
    state.simon_active_pad = None
    state.mode = state.MODE_DJ
    _restore_music()
    print(f"[SIMON] No input for {config.SIMON_INPUT_TIMEOUT_SECONDS}s -- "
          f"timing out ({rounds_cleared} round(s) cleared). Returning to DJ mode.")


def update(now):
    """Per-frame poll, called from inputs/gamepad.py::process_events() only
    while state.mode == MODE_SIMON."""
    global _last_update_at
    if _last_update_at is None:
        _last_update_at = now
    _last_update_at = now

    if state.simon_phase == "intro":
        _update_intro(now)

    elif state.simon_phase == "get_ready":
        _update_get_ready(now)

    elif state.simon_phase == "playback":
        if state.simon_playback_index < 0:
            # Round-break pending (see press()'s round-clear block): first
            # let the player's own final press finish -- a fixed on-screen
            # flash for a joystick press, or the player's own release for a
            # held hardware press (release() advances things from there,
            # this branch just waits) -- THEN count the
            # SIMON_ROUND_BREAK_SECONDS pause before replaying the sequence
            # from its very first note.
            if state.simon_active_pad_held:
                pass  # still physically held -- release() starts the break timer
            elif state.simon_active_pad is not None:
                if now >= state.simon_active_pad_until:
                    state.simon_active_pad = None
                    state.simon_playback_step_started_at = now  # break timer starts now
            elif now - state.simon_playback_step_started_at >= config.SIMON_ROUND_BREAK_SECONDS:
                state.simon_playback_index = 0
                _start_playback_step(now)
        elif state.simon_active_pad is not None:
            if now >= state.simon_active_pad_until:
                state.simon_active_pad = None
                state.simon_playback_step_started_at = now  # gap starts now
        else:
            if now - state.simon_playback_step_started_at >= config.SIMON_FLASH_GAP_SECONDS:
                state.simon_playback_index += 1
                if state.simon_playback_index >= len(state.simon_sequence):
                    state.simon_phase = "input"
                    state.simon_input_index = 0
                    state.simon_last_input_at = now
                else:
                    _start_playback_step(now)

    elif state.simon_phase == "input":
        # A held hardware press ignores simon_active_pad_until entirely --
        # it's cleared by release() instead, whenever the button actually
        # comes back up.
        if (state.simon_active_pad is not None and not state.simon_active_pad_held
                and now >= state.simon_active_pad_until):
            state.simon_active_pad = None
        if now - state.simon_last_input_at >= config.SIMON_INPUT_TIMEOUT_SECONDS:
            _handle_input_timeout(now)

    elif state.simon_phase == "fail":
        _update_fail(now)

    elif state.simon_phase == "score_review":
        _update_score_review(now)


def poll_hardware(now):
    """Per-frame poll, called unconditionally from inputs/gamepad.py::
    process_events() regardless of mode -- the physical arcade buttons need
    to work both as the "enter Simon" trigger from DJ mode and as live
    player input once any Simon game (joystick- or hardware-sourced) is in
    its "input" phase. Edge-triggered off simon_hardware.read_buttons()'s
    level snapshot (a color must go False->True for a press, True->False
    for a release, between polls) so holding a button down can't fire a
    repeated press or a double entry."""
    global _prev_hw_buttons
    buttons = simon_hardware.read_buttons()
    if not buttons:
        return
    pressed_colors = [c for c, is_down in buttons.items() if is_down and not _prev_hw_buttons.get(c)]
    released_colors = [c for c, is_down in buttons.items() if not is_down and _prev_hw_buttons.get(c)]
    _prev_hw_buttons = buttons

    for color in released_colors:
        release(color)

    if not pressed_colors:
        return

    if state.mode == state.MODE_SIMON:
        if state.simon_phase == "input":
            press(config.SIMON_HW_COLOR_ORDER.index(pressed_colors[0]), hold=True)
        return

    # Entry: any button, from DJ mode, only during a live show and only
    # when nothing else is already taking over DJ-mode rendering/lighting
    # (Price Game intro, Westminster, or the post-win applause sequence can
    # all be active while state.mode is still MODE_DJ).
    if (state.mode == state.MODE_DJ and state.show_phase == "live"
            and not state.intermission_active and not state.price_game_active
            and not state.westminster_active and not state.win_sequence_active):
        enter_simon_hardware()
