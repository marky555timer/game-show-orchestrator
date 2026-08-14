import math
import random
import time
from config import (
    DJ_THEME_ALL_OFF_INDEX, DJ_THEME_COUNT, DJ_COLOR_PALETTE,
    CHASE_PACE_MID_SECONDS, CHASE_PACE_FAST_SECONDS, CHASE_PACE_SLOW_SECONDS,
    DMX_NUM_FIXTURES,
    DMX_SONG_INTRO_FLASH_SECONDS, DMX_SONG_INTRO_BLACK_SECONDS,
    DMX_SPARKLE_INTRO_SECONDS, DMX_SONG_TRANSITION_FADE_IN_SECONDS,
    DMX_SPARKLE_FADE_IN_SECONDS, DMX_SPARKLE_PERIOD_SECONDS,
    DMX_MYSTERY_FLASH_SECONDS, DMX_MYSTERY_FADE_OUT_SECONDS,
    DMX_MYSTERY_FADE_IN_SECONDS,
    ENERGY_COLOR_INDICES, ENERGY_THEME_INDICES,
    WESTMINSTER_STROBE_INTERVAL_SECONDS, WESTMINSTER_UV_RAMP_SECONDS,
    SHOW_INTRO_DMX_FLASH_AT_SECONDS, SHOW_INTRO_DMX_FLASH_SECONDS,
    SHOW_INTRO_CHASE_PERIOD_SECONDS, SHOW_OUTRO_DMX_FADE_SECONDS,
)
from state import state
from drivers.dmx_driver import dmx
from drivers import light_prefs_engine

_SPARKLE_THEME_INDEX = 3  # matches _dj_theme_frame's "Sparkle" pattern

_UPLIGHT_COUNT = DMX_NUM_FIXTURES - 1  # fixtures 2..11


def _dj_theme_frame(t, period, theme=None):
    """Returns a list of per-fixture dimmer levels (0-255) for fixtures
    2-11 -- one of the animated venue-uplighting themes, oscillating to
    `period` (the tap-tempo interval). Color is applied by the caller: every
    theme drives brightness only and uses one color across all fixtures, so
    threading the color through here just meant repeating it 10 times.

    `theme` defaults to state.dj_theme_index (the normal case); passing it
    explicitly lets a caller force a specific pattern without touching that
    state -- used by _render_attention_sparkle() to force the Sparkle
    pattern during the song-intro window regardless of the track's actual
    chosen theme."""
    if theme is None:
        theme = state.dj_theme_index
    values = []

    if theme == 0:
        # Unison breathing pulse.
        phase = (t % period) / period
        level = (math.sin(phase * 2 * math.pi) + 1.0) / 2.0
        dimmer = int(60 + level * 195)
        values = [dimmer] * _UPLIGHT_COUNT

    elif theme == 1:
        # Chase sweep left-to-right across the 10 uplights.
        pos = ((t % period) / period) * _UPLIGHT_COUNT
        for i in range(_UPLIGHT_COUNT):
            dist = min(abs(i - pos), _UPLIGHT_COUNT - abs(i - pos))
            level = max(0.0, 1.0 - dist / 2.5)
            dimmer = int(30 + level * 225)
            values.append(dimmer)

    elif theme == 2:
        # Alternating even/odd fixtures on the beat.
        phase = int((t % (period * 2)) / period)
        for i in range(_UPLIGHT_COUNT):
            on = (i % 2) == phase
            values.append(220 if on else 40)

    elif theme == 3:
        # Sparkle -- each fixture twinkles on its own phase offset.
        for i in range(_UPLIGHT_COUNT):
            phase = ((t + i * (period / _UPLIGHT_COUNT)) % period) / period
            level = (math.sin(phase * 2 * math.pi) + 1.0) / 2.0
            dimmer = int(20 + level * 235)
            values.append(dimmer)

    elif theme == 4:
        # Bidirectional converging chase -- two pulses start at the outer
        # fixtures and sweep toward the center, colliding, then restart.
        half = (_UPLIGHT_COUNT - 1) / 2.0
        phase = (t % period) / period
        pos = phase * half
        for i in range(_UPLIGHT_COUNT):
            dist_from_edge = min(i, _UPLIGHT_COUNT - 1 - i)
            dist = abs(dist_from_edge - pos)
            level = max(0.0, 1.0 - dist / 1.8)
            dimmer = int(25 + level * 230)
            values.append(dimmer)

    elif theme == 5:
        # Random twinkle strobe -- deterministic pseudo-random per-fixture
        # flicker (hashed from fixture index + a coarse time bucket) rather
        # than true randomness, so the pattern is reproducible frame to
        # frame instead of jittering.
        bucket = int(t / max(0.05, period / 6.0))
        for i in range(_UPLIGHT_COUNT):
            on = ((i * 2654435761 + bucket * 40503) >> 5) % 3 == 0
            dimmer = 235 if on else 15
            values.append(dimmer)

    elif theme == 6:
        # Wave gradient -- a smooth traveling sine brightness wave across
        # the fixtures (continuous, unlike the discrete chase in theme 1).
        for i in range(_UPLIGHT_COUNT):
            phase = ((t / period) + (i / _UPLIGHT_COUNT)) % 1.0
            level = (math.sin(phase * 2 * math.pi) + 1.0) / 2.0
            dimmer = int(30 + level * 225)
            values.append(dimmer)

    elif theme == 8:
        # Bounce -- a single bright pulse ping-pongs back and forth across
        # the uplights, reflecting at both ends, unlike theme 1's chase
        # (which wraps around instead of reversing).
        span = max(1, _UPLIGHT_COUNT - 1)
        cycle_len = span * 2
        raw = ((t % (period * 2)) / (period * 2)) * cycle_len
        pos = raw if raw <= span else cycle_len - raw
        for i in range(_UPLIGHT_COUNT):
            dist = abs(i - pos)
            level = max(0.0, 1.0 - dist / 2.0)
            dimmer = int(25 + level * 230)
            values.append(dimmer)

    elif theme == 9:
        # Comet -- a bright head with an exponentially-decaying trail
        # behind it, continuously sweeping one direction (wraps around),
        # unlike theme 1's symmetric falloff on both sides of the head.
        pos = ((t % period) / period) * _UPLIGHT_COUNT
        for i in range(_UPLIGHT_COUNT):
            trail = (pos - i) % _UPLIGHT_COUNT
            level = math.exp(-trail / 1.6)
            dimmer = int(20 + level * 235)
            values.append(dimmer)

    elif theme == 10:
        # Beat flash -- one sharp flash right on the beat, decaying away
        # for the rest of the period (a single strobe-and-fade), unlike the
        # double-pulse "lub-dub" heartbeat theme below.
        phase = (t % period) / period
        level = max(0.0, 1.0 - phase / 0.35)
        dimmer = int(15 + level * 240)
        values = [dimmer] * _UPLIGHT_COUNT

    elif theme == 11:
        # Paired chase -- fixtures light up two at a time (adjacent pairs)
        # sweeping across, instead of one at a time like theme 1's chase.
        pair_count = max(1, _UPLIGHT_COUNT / 2.0)
        pos = ((t % period) / period) * pair_count
        for i in range(_UPLIGHT_COUNT):
            pair_index = i // 2
            dist = min(abs(pair_index - pos), pair_count - abs(pair_index - pos))
            level = max(0.0, 1.0 - dist / 1.5)
            dimmer = int(25 + level * 230)
            values.append(dimmer)

    elif theme == 12:
        # Solid -- every fixture holds steady at full brightness in the
        # current color, no animation and no dependence on `t`/`period` at
        # all. Every other theme is a beat-synced motion effect; this is
        # the "just wash the room in one color and leave it" option for
        # when the operator wants the pattern to get out of the way.
        values = [255] * _UPLIGHT_COUNT

    else:
        # Heartbeat pulse burst -- all fixtures snap into a quick double
        # pulse ("lub-dub"), then fade together before the next beat.
        phase = (t % period) / period
        if phase < 0.12:
            level = phase / 0.12
        elif phase < 0.24:
            level = 1.0 - (phase - 0.12) / 0.12
        elif phase < 0.34:
            level = (phase - 0.24) / 0.10
        elif phase < 0.5:
            level = 1.0 - (phase - 0.34) / 0.16
        else:
            level = 0.0
        dimmer = int(15 + level * 240)
        values = [dimmer] * _UPLIGHT_COUNT

    return values


def _current_color():
    return DJ_COLOR_PALETTE[state.dj_color_index % len(DJ_COLOR_PALETTE)]


def _render_dj_uplights(t, brightness=1.0):
    if state.dj_theme_index == DJ_THEME_ALL_OFF_INDEX:
        dmx.set_all_uplights(0, 0, 0, 0)
        return
    color = _current_color()
    r, g, b = color
    frame = _dj_theme_frame(t, state.dj_tempo_period)
    for i, dimmer in enumerate(frame):
        # White/amber/UV ride their own emitters (see config.DJColor) -- the
        # dimmer scales all of them together, so a "uv" look dims as a UV
        # wash rather than crossfading into RGB.
        dmx.set_uplight(i + 2, int(dimmer * brightness), r, g, b,
                        white=color.white, amber=color.amber, uv=color.uv)


def trigger_song_transition(announcement_seconds=None):
    """Called by drivers/deck_orchestrator.py the instant a track transition
    fires (2026-08-09): kicks off the attention-sparkle-then-settle sequence
    (see _song_intro_state()) and arms the deferred look pick.

    `announcement_seconds` is how long the station VO for THIS transition
    runs (sweeper overlap + clip length), or None for a plain crossfade
    with no announcement. The blackout stage is stretched to cover it
    (2026-08-12) so the room stays dark for the whole voice-over and the
    twinkles fade up as it ends, instead of the lights coming back over the
    top of it. Never shortens the stage below DMX_SONG_INTRO_BLACK_SECONDS
    -- a very short clip still gets enough darkness to cover the crossfade.

    NOT gated to DJ mode (fixed 2026-08-12 -- it used to no-op outside it,
    reasoning that Game Mode wasn't rendering the DJ uplights so there'd be
    nothing to show). That reasoning missed that background music keeps
    crossfading regardless of which mode the LED matrix is in -- a track
    can transition while a quiz question happens to be live (MODE_GAME),
    and the DMX rig is a separate physical system from the matrix, so
    there's no reason the sparkle cue should silently skip just because
    the matrix was busy with something else at that exact instant. See
    update()'s priority order: the sparkle phase now renders (and the look
    still gets resolved) regardless of mode; only the *settled*, ongoing
    DJ-uplight rendering stays DJ-mode-only, same as before.

    The new look is NOT chosen here (changed 2026-08-11). It used to pick a
    random color immediately, and then -- one frame later --
    drivers/light_prefs_engine.py::apply_prefs_for() would restore this
    track's remembered color/pattern on top of it, so a track with saved
    prefs visibly changed look twice per song change. Now the pick is
    deferred to _resolve_pending_look(): the recall (which lands within a
    frame) wins outright, and a look is only implied from the track's
    energy (or picked fully at random, if that's unclassified too) for
    tracks that turn out to have nothing saved. Either way the decision
    happens during the sparkle intro, well before it's ever rendered, so
    the room only ever sees the sparkle loop and then the one real look."""
    state.lighting_transition_started_at = time.time()
    state.lighting_look_pending = True
    # The VO starts at the same instant as the flash, so the darkness has to
    # cover what's LEFT of it once the flash is done -- otherwise the black
    # stage would overrun the voice-over by the flash's length.
    hold = DMX_SONG_INTRO_BLACK_SECONDS
    if announcement_seconds:
        hold = max(hold, float(announcement_seconds) - DMX_SONG_INTRO_FLASH_SECONDS)
    state.lighting_black_hold_seconds = hold


def note_look_recalled():
    """Called by drivers/light_prefs_engine.py::apply_prefs_for() when a
    track's saved color/pattern has just been restored -- cancels the
    pending implied-look pick so the recalled look stands."""
    state.lighting_look_pending = False


def _pick_implied_look():
    """No saved look for this track -- picks one now. Biased toward the
    track's AI-classified energy (config.ENERGY_COLOR_INDICES/
    ENERGY_THEME_INDICES, drivers/light_prefs_engine.py::get_energy_for())
    if it's been classified yet; otherwise (brand new track, or the async
    classification hasn't landed) falls back to a fully random pick across
    the whole palette/pattern set, same as before this feature existed."""
    energy = light_prefs_engine.get_energy_for(state.factoid_track_key)

    color_pool = ENERGY_COLOR_INDICES.get(energy) or range(len(DJ_COLOR_PALETTE))
    color_choices = [i for i in color_pool if i != state.dj_color_index] or list(color_pool)
    state.dj_color_index = random.choice(color_choices)

    theme_pool = ENERGY_THEME_INDICES.get(energy) or range(DJ_THEME_COUNT)
    theme_choices = [i for i in theme_pool if i != state.dj_theme_index] or list(theme_pool)
    state.dj_theme_index = random.choice(theme_choices)


def _resolve_pending_look(now):
    """Picks an implied look for a track that reached the end of the white
    flash without a saved look being recalled. Deliberately waits that long
    (rather than resolving immediately on transition) so a recall arriving
    a frame later isn't overwritten (see trigger_song_transition) -- there
    's no rush beyond that, since the result isn't actually rendered until
    the sparkle hold ends, seconds later."""
    if not state.lighting_look_pending:
        return
    started = state.lighting_transition_started_at
    if started and now - started < DMX_SONG_INTRO_FLASH_SECONDS:
        return  # still mid-flash -- a recall may yet land
    state.lighting_look_pending = False
    _pick_implied_look()


def trigger_mystery_blackout():
    """Mystery Band sting, part 1 (drivers/mystery_band_engine.py::
    check_new_track, the moment a new-artist teaser is committed to): every
    fixture snaps to full white, fades to black, and then HOLDS in black --
    through the announcement VO, however long it runs -- until
    release_mystery_blackout() fires as the "Who is this?" teaser appears.

    Supersedes any song-transition fade already running: both envelopes
    drive the same brightness, and the sting's own fade-out is the one that
    should be seen. Any still-undecided look is settled here too, since the
    room is about to be dark anyway -- by this point apply_prefs_for() has
    already run for this track (inputs/gamepad.py calls ensure_prefetch()
    immediately before check_new_track()), so a recalled look is in place."""
    if state.mode != state.MODE_DJ:
        return
    now = time.time()
    _resolve_pending_look(now)
    state.lighting_transition_started_at = 0.0
    state.mystery_light_flash_at = now
    state.mystery_light_holding = True
    state.mystery_light_release_at = 0.0


def release_mystery_blackout():
    """Mystery Band sting, part 2: the "Who is this?" teaser is now on
    screen -- fade the running pattern back in under it. Safe to call when
    no sting is running (a teaser that armed without a blackout, e.g. one
    triggered outside DJ mode), in which case it's a no-op."""
    if not state.mystery_light_flash_at:
        return
    state.mystery_light_holding = False
    state.mystery_light_release_at = time.time()


def _mystery_sting_brightness(now):
    """Returns (handled, brightness, force_white) for the Mystery Band
    sting. `handled` False means no sting is running and the caller should
    fall back to the normal song-transition envelope."""
    started = state.mystery_light_flash_at
    if not started:
        return False, 1.0, False

    elapsed = now - started
    flash = DMX_MYSTERY_FLASH_SECONDS
    fade_out = DMX_MYSTERY_FADE_OUT_SECONDS

    if elapsed < flash:
        return True, 1.0, True                      # full white pop
    if elapsed < flash + fade_out:
        level = 1.0 - (elapsed - flash) / fade_out  # white fading to black
        return True, max(0.0, level), True
    if state.mystery_light_holding:
        return True, 0.0, True                      # parked dark, awaiting the teaser

    released = state.mystery_light_release_at
    if not released:
        return True, 0.0, True
    fade_in = DMX_MYSTERY_FADE_IN_SECONDS
    since = now - released
    if since >= fade_in:
        state.mystery_light_flash_at = 0.0          # sting complete
        state.mystery_light_release_at = 0.0
        return False, 1.0, False
    # Fading back into the real pattern/color, not into white.
    return True, min(1.0, since / fade_in), False


def _song_intro_state(now):
    """Returns (phase, brightness) for the post-transition sequence, same
    for every track whether it has a saved look or not (2026-08-12 fix --
    an earlier version treated those two cases differently by accident).
    phase is "flash" | "black" | "sparkle" | "settled". brightness is
    0.0-1.0 -- meaningful for "flash" (always 1.0, included for a uniform
    return shape), "sparkle" (ramps 0->1 over DMX_SPARKLE_FADE_IN_SECONDS,
    then holds at 1.0) and "settled" (ramps 0->1 during the fade-up; 1.0
    once the whole sequence is over or was never running). "black" always
    renders at zero regardless.

    Four stages, all timed off state.lighting_transition_started_at (set
    by trigger_song_transition()):
      1. FLASH  -- instant full white, DMX_SONG_INTRO_FLASH_SECONDS long.
      2. BLACK  -- instant cut to black, held for
         state.lighting_black_hold_seconds: at least
         DMX_SONG_INTRO_BLACK_SECONDS (covering the audio crossfade), and
         on an announced transition long enough to cover the whole station
         VO, so the twinkles come up as the voice-over ends.
      3. SPARKLE -- the animated Sparkle pattern forced to white (see
         _render_attention_sparkle), DMX_SPARKLE_INTRO_SECONDS long -- the
         actual attention-grabbing loop. Fades up rather than cutting in.
      4. SETTLE -- fades UP (DMX_SONG_TRANSITION_FADE_IN_SECONDS) into the
         track's real look, already resolved earlier in the sequence by
         _resolve_pending_look() -- recalled, energy-implied, or random."""
    started = state.lighting_transition_started_at
    if not started:
        return "settled", 1.0

    elapsed = now - started
    flash = DMX_SONG_INTRO_FLASH_SECONDS
    # Falls back to the constant for a transition armed before this field
    # existed (or by anything that set the timestamp directly).
    black = state.lighting_black_hold_seconds or DMX_SONG_INTRO_BLACK_SECONDS
    hold = DMX_SPARKLE_INTRO_SECONDS
    settle = DMX_SONG_TRANSITION_FADE_IN_SECONDS
    t1, t2, t3 = flash, flash + black, flash + black + hold
    t4 = t3 + settle

    if elapsed >= t4:
        state.lighting_transition_started_at = 0.0
        return "settled", 1.0
    if elapsed < t1:
        return "flash", 1.0
    if elapsed < t2:
        return "black", 0.0
    if elapsed < t3:
        fade = DMX_SPARKLE_FADE_IN_SECONDS
        return "sparkle", (min(1.0, (elapsed - t2) / fade) if fade > 0 else 1.0)
    return "settled", min(1.0, (elapsed - t3) / settle)


def _render_attention_sparkle(now, brightness=1.0):
    """The animated Sparkle pattern (_SPARKLE_THEME_INDEX), forced to white
    regardless of whatever theme/color the track actually resolved to --
    the attention-grabbing loop proper, used for the "sparkle" stage of
    _song_intro_state(). `brightness` is the stage's fade-up envelope (0->1
    over DMX_SPARKLE_FADE_IN_SECONDS, then 1.0 for the rest of the hold),
    scaling every fixture's dimmer on top of the pattern's own per-fixture
    oscillation so the loop emerges out of the blackout instead of
    snapping on at full.

    Runs at a FIXED DMX_SPARKLE_BPM (2026-08-12), not the track's tempo:
    this is a house attention cue with its own constant pulse, and pinning
    it means the twinkle reads identically every transition instead of
    lurching with whatever the last/next track's BPM happened to be. The
    stored (or AI-predicted) tempo takes back over at the SETTLE stage,
    which renders through the normal DJ-uplight path on
    state.dj_tempo_period -- nothing here writes to that, so there's
    nothing to restore.

    Fixture 1 twinkles too (2026-08-12 fix -- it was hardcoded to solid
    white while the other 10 fixtures modulated, so it visibly didn't
    match). Its dimmer is computed directly here with the Sparkle theme's
    own formula (theme == 3 in _dj_theme_frame) rather than teaching that
    function about an 11th slot, since it's shared with the *normal*
    (non-intro) Sparkle theme, which has no Fixture 1 concept at all --
    every DJ uplighting theme drives fixtures 2-11 only, Fixture 1 is
    always handled separately (see _render_fixture1). The one-slot-width
    offset keeps it reading as part of the same evenly-spaced twinkle
    chain instead of an arbitrary extra light with its own timing."""
    period = DMX_SPARKLE_PERIOD_SECONDS
    frame = _dj_theme_frame(now, period, theme=_SPARKLE_THEME_INDEX)
    for i, dimmer in enumerate(frame):
        level = int(dimmer * brightness)
        dmx.set_uplight(i + 2, level, 255, 255, 255, white=level)

    # Half a slot-width, not a whole one: the 10 uplights sit at whole-slot
    # offsets 0..9 (mod period), so a *whole*-slot offset (including a
    # negative one, which just wraps to another whole slot mod period --
    # the bug in an earlier version of this line) always lands exactly on
    # one of their existing phases instead of a genuinely new one.
    slot = period / _UPLIGHT_COUNT
    phase = ((now + slot / 2) % period) / period
    level = (math.sin(phase * 2 * math.pi) + 1.0) / 2.0
    fixture1_dimmer = int((20 + level * 235) * brightness)
    dmx.set_fixture1(fixture1_dimmer, 255, 255, 255, white=fixture1_dimmer)


def _render_song_intro_flash():
    """Instant full white -- the "flash" stage of _song_intro_state()."""
    dmx.set_all_uplights(255, 255, 255, 255, white=255)
    dmx.set_fixture1(255, 255, 255, 255, white=255)


def _render_song_intro_black():
    """Instant black -- the "black" stage of _song_intro_state(), covering
    the audio crossfade itself."""
    dmx.set_all_uplights(0, 0, 0, 0)
    dmx.set_fixture1(0, 0, 0, 0)


def _chase_pace_seconds(now):
    if state.chase_pace_until and now < state.chase_pace_until:
        return CHASE_PACE_FAST_SECONDS if state.chase_pace_mode == "fast" else CHASE_PACE_SLOW_SECONDS
    return CHASE_PACE_MID_SECONDS


def _render_game_chase(t, now):
    pace = _chase_pace_seconds(now)
    step = int(t / pace) % _UPLIGHT_COUNT
    for i in range(_UPLIGHT_COUNT):
        on = (i == step)
        dmx.set_uplight(i + 2, 255 if on else 15, 255, 255, 255)


def _render_price_strobe(t):
    """70s/80s Price Game intro, phase 1: rapid on/off strobe across all 10
    uplights (fixtures 2-11), ~10Hz full white/black alternation."""
    on = int(t * 10.0) % 2 == 0
    if on:
        dmx.set_all_uplights(255, 255, 255, 255)
    else:
        dmx.set_all_uplights(0, 0, 0, 0)


def _render_grade_flash(now):
    """Amplified game feedback (Section 5): the instant an answer is graded,
    ALL 10 uplight fixtures (2-11) snap to a brief solid green/red pulse
    (state.fixture_flash_mode/until, set by inputs/gamepad.py::
    trigger_big_win/trigger_loss), overriding the normal chase for that
    short window. Returns True while the pulse is live so the caller skips
    _render_game_chase for this frame; once fixture_flash_until passes, the
    chase resumes on its own -- Fixture 1's own win/loss lamp
    (_render_fixture1) is untouched either way."""
    if now >= state.fixture_flash_until:
        return False
    if state.fixture_flash_mode == "win":
        dmx.set_all_uplights(255, 0, 255, 0)
        return True
    if state.fixture_flash_mode == "loss":
        dmx.set_all_uplights(255, 255, 0, 0)
        return True
    return False


def _render_fixture1(t, brightness=1.0):
    mode = state.fixture1_mode
    if mode == "win":
        elapsed = t - state.fixture1_mode_set_at
        cps = 2.0
        phase = (elapsed * cps) % 1.0
        saw = 1.0 - phase
        dimmer = int(40 + saw * 215)
        dmx.set_fixture1(dimmer, 0, 255, 0)
    elif mode == "loss":
        dmx.set_fixture1(255, 255, 0, 0)
    elif mode == "idle":
        # Rejoins the DJ-mode idle lightshow instead of sitting dark the
        # whole time it isn't showing a win/loss result -- a breathing
        # pulse in the current uplight color, locked to state.dj_tempo_period
        # (the track's own BPM tag when available -- see
        # drivers/deck_orchestrator.py's update() -- else the tap-tempo
        # fallback), same beat period the other 10 uplights already dance
        # to, so it reads as part of the same show instead of an unsynced
        # extra blink.
        color = _current_color()
        period = state.dj_tempo_period
        phase = (t % period) / period
        level = (math.sin(phase * 2 * math.pi) + 1.0) / 2.0
        dimmer = int((40 + level * 195) * brightness)
        # Fixture 1 is the same MINIRF4 V2 as the uplights now, so it
        # renders the look exactly as they do -- dedicated white/amber/UV
        # emitters included, no RGB approximation.
        r, g, b = color
        dmx.set_fixture1(dimmer, r, g, b,
                         white=color.white, amber=color.amber, uv=color.uv)
    else:
        dmx.set_fixture1(0, 0, 0, 0)


def _render_westminster_dmx(now):
    """Westminster "Bat Clock" event (Feature Update): kill all uplights
    instantly, fire state.westminster_strobe_count (1-3) fast lightning-
    strike strobes each returning to dark, then fade fixture-index-2 up to
    full UV/dimmer intensity, holding there until the sequence ends.

    Fixture 2 was picked as the spec's "DMX Light 1" back when the real
    Fixture 1 was a plain RGB win/loss lamp with no UV emitter at all.
    Fixture 1 is a matching MINIRF4 V2 now (2026-08-11) and could take the
    UV hit instead, or as well -- but that's a deliberate change to how the
    show looks rather than a bug, so it stays on fixture 2 until asked.

    Driven off state.westminster_started_at
    (independent of the matrix's own phase clock in
    graphics/matrix_canvas.py -- this timing doesn't need to line up with
    the visual flash/particle/readout phases)."""
    elapsed = now - state.westminster_started_at
    strobe_count = state.westminster_strobe_count or 1
    # off, on, off, on, ..., off -- strobe_count "on" flashes, each
    # preceded and followed by a dark interval.
    total_intervals = 2 * strobe_count + 1
    strobe_duration = total_intervals * WESTMINSTER_STROBE_INTERVAL_SECONDS

    if elapsed < strobe_duration:
        phase_idx = int(elapsed / WESTMINSTER_STROBE_INTERVAL_SECONDS)
        if phase_idx % 2 == 1:
            dmx.set_all_uplights(255, 255, 255, 255)
        else:
            dmx.set_all_uplights(0, 0, 0, 0)
        return

    ramp_elapsed = elapsed - strobe_duration
    if WESTMINSTER_UV_RAMP_SECONDS > 0:
        level = min(255, int(255 * ramp_elapsed / WESTMINSTER_UV_RAMP_SECONDS))
    else:
        level = 255
    dmx.set_all_uplights(0, 0, 0, 0)
    dmx.set_uplight(2, level, 0, 0, 0, uv=level)


def _render_show_dmx(now):
    """Trivia Night show flow (2026-08-13, drivers/show_engine.py): Setup/
    Countdown sit dark (no light show while the operator configures things
    or waits on a scheduled start); Intro/Outro run their own scripted
    choreography, purely off state.show_phase_started_at /
    state.show_outro_music_ends_at -- same "compute everything from
    elapsed time" shape as _render_westminster_dmx() above."""
    phase = state.show_phase
    if phase in ("setup", "countdown"):
        dmx.set_all_uplights(0, 0, 0, 0)
        dmx.set_fixture1(0, 0, 0, 0)
        return
    if phase == "intro":
        _render_show_intro_dmx(now)
        return
    if phase == "outro":
        _render_show_outro_dmx(now)


def _render_show_intro_dmx(now):
    elapsed = now - state.show_phase_started_at
    if elapsed < SHOW_INTRO_DMX_FLASH_AT_SECONDS:
        # White twinkle -- the Sparkle pattern, forced white regardless of
        # the current DJ color (same "force this theme/color" approach as
        # _render_attention_sparkle's song-intro use of Sparkle).
        frame = _dj_theme_frame(now, DMX_SPARKLE_PERIOD_SECONDS, theme=_SPARKLE_THEME_INDEX)
        for i, dimmer in enumerate(frame):
            dmx.set_uplight(i + 2, dimmer, dimmer, dimmer, dimmer, white=dimmer)
        dmx.set_fixture1(0, 0, 0, 0)
        return

    # White flash -> black -> white flash -> green marquee chase (holds for
    # the rest of the intro).
    beat = elapsed - SHOW_INTRO_DMX_FLASH_AT_SECONDS
    f = SHOW_INTRO_DMX_FLASH_SECONDS
    if beat < f:
        dmx.set_all_uplights(255, 255, 255, 255, white=255)
    elif beat < f * 2:
        dmx.set_all_uplights(0, 0, 0, 0)
    elif beat < f * 3:
        dmx.set_all_uplights(255, 255, 255, 255, white=255)
    else:
        green = DJ_COLOR_PALETTE[3]  # matches config's named "green" entry
        r, g, b = green
        frame = _dj_theme_frame(now, SHOW_INTRO_CHASE_PERIOD_SECONDS, theme=1)
        for i, dimmer in enumerate(frame):
            dmx.set_uplight(i + 2, dimmer, r, g, b)
    dmx.set_fixture1(0, 0, 0, 0)


def _render_show_outro_dmx(now):
    if now < state.show_outro_music_ends_at:
        # "Twinkle DMX Display through the song" -- same white Sparkle as
        # the intro's twinkle.
        frame = _dj_theme_frame(now, DMX_SPARKLE_PERIOD_SECONDS, theme=_SPARKLE_THEME_INDEX)
        for i, dimmer in enumerate(frame):
            dmx.set_uplight(i + 2, dimmer, dimmer, dimmer, dimmer, white=dimmer)
        dmx.set_fixture1(0, 0, 0, 0)
        return

    # "When song stops, fade out DMX" -- a smooth fade, unlike the intro's
    # hard-cut flashes.
    fade_elapsed = now - state.show_outro_music_ends_at
    if fade_elapsed < SHOW_OUTRO_DMX_FADE_SECONDS:
        level = int(255 * (1.0 - fade_elapsed / SHOW_OUTRO_DMX_FADE_SECONDS))
        dmx.set_all_uplights(level, level, level, level, white=level)
    else:
        dmx.set_all_uplights(0, 0, 0, 0)
    dmx.set_fixture1(0, 0, 0, 0)


def update(now):
    """Per-frame DMX renderer, called once per frame from main.py. Owns
    the entire 176-channel frame and calls dmx.render() itself, replacing
    the old event-driven closures that used to live in inputs/gamepad.py."""
    if state.show_phase != "live":
        # Highest-priority override, ahead of even Westminster -- Setup/
        # Countdown/Intro/Outro fully own the rig. "live" falls straight
        # through to the normal dispatch below, unchanged.
        _render_show_dmx(now)
        dmx.render()
        return

    if state.westminster_active:
        # Highest-priority override -- takes over DMX regardless of DJ/
        # Game mode, same precedence the Price Game strobe phase already
        # gets below. Fixture 1 forced black rather than rendered from
        # state.fixture1_mode, which may be stale mid-show-event.
        _render_westminster_dmx(now)
        dmx.set_fixture1(0, 0, 0, 0)
        dmx.render()
        return

    # The Mystery Band sting and the song-intro sparkle both drive the DMX
    # rig, a physical system entirely separate from the LED matrix -- so
    # both are checked here, ahead of the DJ/Game mode branch below,
    # instead of only inside the MODE_DJ case (fixed 2026-08-12, see
    # trigger_song_transition()'s docstring). Background music keeps
    # crossfading regardless of which mode the matrix is in, so a
    # transition (or a new-artist teaser arming) can legitimately land
    # while a quiz question happens to be live on screen (MODE_GAME); the
    # rig shouldn't silently skip its cue just because the matrix was busy
    # with something else at that exact instant.
    _resolve_pending_look(now)
    brightness = 1.0
    sting, sting_brightness, force_white = _mystery_sting_brightness(now)
    if sting:
        brightness = sting_brightness
        if force_white:
            # Flash/fade/hold phases: every fixture is white, ignoring the
            # current look entirely. Rendered here rather than through
            # _render_dj_uplights so the pattern animation doesn't show
            # through the sting.
            level = int(255 * brightness)
            dmx.set_all_uplights(level, level, level, level, white=level)
            dmx.set_fixture1(level, level, level, level, white=level)
            dmx.render()
            return
        # Fade-in phase -- ramps up into the real pattern/color below.
    else:
        phase, brightness = _song_intro_state(now)
        # flash/black/sparkle all render+return immediately -- each fully
        # owns both the uplights and Fixture 1 for its stage, same reason
        # the mystery-sting force_white branch above does, so there's no
        # fighting with the shared _render_fixture1() tail below.
        if phase == "flash":
            _render_song_intro_flash()
            dmx.render()
            return
        if phase == "black":
            _render_song_intro_black()
            dmx.render()
            return
        if phase == "sparkle":
            _render_attention_sparkle(now, brightness)
            dmx.render()
            return

    if state.price_game_active and state.price_game_phase == "strobe":
        # Price Game intro, phase 1 -- overrides whatever mode we're in.
        _render_price_strobe(now)
    elif state.mode == state.MODE_DJ and not state.price_game_active:
        # Fixture 1 rejoins the idle DJ-mode lightshow (see _render_fixture1's
        # "idle" branch) instead of sitting dark the whole time -- it used
        # to be a wasted fixture until the first win/loss result of a game.
        state.fixture1_mode = "idle"
        _render_dj_uplights(now, brightness)
    elif _render_grade_flash(now):
        pass  # brief ALL-fixture win/loss pulse overrides the chase this frame
    else:
        # GAME_MODE chase, and also the Price Game intro's banner/
        # question-wait phases -- "return to the standard mid-pace chase"
        # per the bonus-round spec (chase_pace_mode is reset to "mid" by
        # price_game_engine.start_price_game()). brightness may be <1.0
        # here if a mystery-sting/sparkle fade-in is still ramping up while
        # Game Mode is active, but _render_game_chase() doesn't take a
        # brightness parameter (its chase was never part of either
        # envelope) -- only _render_fixture1() below actually uses it in
        # that case, and only if fixture1_mode happens to be "idle", which
        # it normally isn't mid-round.
        _render_game_chase(now, now)

    _render_fixture1(now, brightness)
    dmx.render()
