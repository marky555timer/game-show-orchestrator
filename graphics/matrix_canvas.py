import os
import time
import random
import pygame

# Dev canvas simulator: initialize the window pinned to the screen's
# top-left corner (0, 0) BEFORE pygame.display.set_mode() creates it --
# SDL reads this env var at window-creation time, so it has to be set
# first. Fixed placement keeps the OCR pixel/coordinate math in
# drivers/rekordbox_driver.py (crop ratios measured against Rekordbox's own
# window position) from drifting if the simulator window ever gets dragged.
os.environ["SDL_VIDEO_WINDOW_POS"] = "0,0"

from config import (
    MATRIX_WIDTH, MATRIX_HEIGHT, PIXEL_SCALE, GAP, WINDOW_W, WINDOW_H,
    RED_FULL, RED_DIM, RED_OFF, BLACK, PANELS, TOP_COMBINED,
    TOP_CYCLE_TRACK_SECONDS, TOP_CYCLE_FACTOID_SECONDS,
    STATUS_PANEL_HOLD_SECONDS, TOP_SHOW_FACTOID_PAGE,
    TEMPO_FLASH_DECAY_SECONDS, QUIZ_CELEBRATION_HOLD_SECONDS, QUIZ_STATS_HOLD_SECONDS,
    BRANDING_OVERLAY_INTERVAL_SECONDS, BRANDING_OVERLAY_DURATION_SECONDS,
    BRANDING_ASSEMBLY_DURATION_SECONDS, DJ_COLOR_PALETTE,
    MYSTERY_QMARK_PHASE_SECONDS, MYSTERY_REVEAL_BLINK_PERIOD_SECONDS,
    TEMPO_PERIOD_DEFAULT_SECONDS,
    SI_PLAYER_WIDTH, SI_PLAYER_HEIGHT, SI_PLAYER_Y_FROM_BOTTOM, SI_INVADER_SIZE,
)
from state import state
from drivers.rekordbox_driver import get_rekordbox_track
from drivers.factoid_engine import build_mock_question, advance_to_next_queued_question
from drivers.branding_engine import get_current_text
from graphics.text_render import (
    draw_marquee, wrap_two_lines, draw_bitmap_text, text_width, char_width,
    GLYPH_GAP, GLYPH_HEIGHT,
)
from graphics.animations import (
    render_panel_animation, deal_panel_animations,
    anim_dancing_cat, anim_star_burst, anim_coin_pop, anim_question_mark,
)

# Initialize Pygame Display Engine
screen = pygame.display.set_mode((WINDOW_W, WINDOW_H))
pygame.display.set_caption("6-Panel Game Show Matrix Simulator")


def _pin_window_always_on_top():
    """Pygame has no native Always-On-Top flag, so pin the actual OS window
    via the Win32 API once it exists -- keeps the simulator layered above
    Rekordbox and every other desktop window so its fixed top-left position
    (and therefore the OCR crop coordinates measured against it) can never
    get buried/occluded by a window click. Best-effort: a non-Windows box
    or a missing pywin32 install just leaves the window in normal Z-order."""
    try:
        import win32gui
        import win32con
        hwnd = pygame.display.get_wm_info()["window"]
        win32gui.SetWindowPos(
            hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE,
        )
        print("[CANVAS] Simulator window pinned Always-On-Top at (0, 0).")
    except Exception as e:
        print(f"[CANVAS] Could not set Always-On-Top (non-Windows or pywin32 missing?): {e}")


_pin_window_always_on_top()

matrix_surface = pygame.Surface((MATRIX_WIDTH, MATRIX_HEIGHT))

LINE_H = 8  # two 8px rows fit exactly in a 16px-tall panel

# DJ-mode top-display page cycling: page 0 = artist/title, page 1 = factoid.
_top_cycle_start = time.time()
_top_cycle_track_key = None

# Panel 3's AI-pipeline status indicator is transient: it holds for
# STATUS_PANEL_HOLD_SECONDS after the status changes, then panel 3 rejoins the
# random animation deal so all four bottom panels keep moving. Panel 3 was
# picked (rather than panel 2) so panels 1+2 can stay a single 64x16 text
# surface -- see _render_dj_mode.
_status_signature = None
_status_until = 0.0

# ==========================================
# DJ-MODE / DMX TEMPO BEAT-SYNC (Section 3)
# ==========================================
# The idle panel animations (graphics/animations.py) and the Mystery Band
# question-mark cycle were all originally tuned against the wall clock.
# Rather than rewrite every animation's internal period constants, this
# accumulator advances at a speed relative to the DJ-mode tap-tempo period
# (drivers/lighting_engine.py already drives the DMX uplighting themes off
# state.dj_tempo_period directly) -- at the default tempo it advances 1:1
# with real time (so nothing changes for a DJ who never taps tempo), and
# speeds up/slows down proportionally as the tap tempo gets faster/slower,
# putting the matrix's frame steps in lockstep with the DMX beat clock.
_beat_clock_accum = 0.0
_beat_clock_last_t = None


def _advance_beat_clock(real_t):
    global _beat_clock_accum, _beat_clock_last_t
    if _beat_clock_last_t is None:
        _beat_clock_last_t = real_t
    dt = max(0.0, real_t - _beat_clock_last_t)
    _beat_clock_last_t = real_t
    speed = TEMPO_PERIOD_DEFAULT_SECONDS / max(0.05, state.dj_tempo_period)
    _beat_clock_accum += dt * speed
    return _beat_clock_accum


def _get_top_page(has_factoid):
    # Page 0 is artist/track. Unless the factoid page is explicitly enabled in
    # config, panels 1+2 stay on page 0 permanently so the pair is always the
    # artist/track headline using the full 64px width.
    if not (has_factoid and TOP_SHOW_FACTOID_PAGE):
        return 0
    elapsed = time.time() - _top_cycle_start
    cycle = TOP_CYCLE_TRACK_SECONDS + TOP_CYCLE_FACTOID_SECONDS
    phase = elapsed % cycle
    return 0 if phase < TOP_CYCLE_TRACK_SECONDS else 1


def _current_track():
    """(title, artist) for the deck currently up on the crossfader."""
    track_info = get_rekordbox_track()
    if isinstance(track_info, tuple):
        return track_info[0], track_info[1]
    return str(track_info), ""


def _render_dj_mode(t):
    global _top_cycle_track_key, _top_cycle_start
    global _status_signature, _status_until

    song_title, artist_name = _current_track()

    # Quiz/factoid content is no longer fetched automatically here -- it's
    # gated behind DJ-mode Btn6 (see inputs/gamepad.py::handle_quiz_gate_button),
    # so no API call is spent just by having a confident track on screen.
    has_factoid = bool(state.factoid_headline)

    track_key = f"{song_title}|{artist_name}"
    if track_key != _top_cycle_track_key:
        _top_cycle_track_key = track_key
        _top_cycle_start = t
        # New track -> deal a fresh hand of animations across panels 3-6.
        deal_panel_animations()
        # New track -> force a DJ-mode uplighting color rotation, same
        # palette Btn7 cycles through manually (drivers/lighting_engine.py).
        state.dj_color_index = (state.dj_color_index + 1) % len(DJ_COLOR_PALETTE)

    # Mystery Band teaser (Section 2, drivers/mystery_band_engine.py): while
    # the 10s window is still live, panels 1+2 hide the title behind "Who is
    # this?" and panels 3-6 loop the question-mark/original cycle instead of
    # the normal display below. Once it times out unanswered, the reveal
    # phase blinks the real title (inverted colors) on panels 1+2 while the
    # bottom panels immediately resume their normal animations.
    mystery_live = state.mystery_active and not state.mystery_resolved
    mystery_reveal = state.mystery_active and state.mystery_resolved

    # Panels 1+2 are driven as one logical 64x16 surface, two 8px lines tall,
    # so artist and title each get the full double-panel width instead of
    # being crammed into 32px -- except during the periodic branding
    # takeover below, which uses the combined surface as a single line.
    if mystery_live:
        draw_marquee(matrix_surface, "mystery_teaser", "Who is this?", TOP_COMBINED, align="center")
    elif mystery_reveal:
        tx, ty, tw, th = TOP_COMBINED
        line1_rect = (tx, ty, tw, LINE_H)
        line2_rect = (tx, ty + LINE_H, tw, LINE_H)
        blink_on = int(t / MYSTERY_REVEAL_BLINK_PERIOD_SECONDS) % 2 == 0
        draw_marquee(matrix_surface, "dj_top_artist", artist_name, line1_rect, invert=blink_on)
        draw_marquee(matrix_surface, "dj_top_title", song_title, line2_rect, invert=blink_on)
    elif _branding_overlay_active(t) and get_current_text():
        _render_dj_branding_assembly(t)
    else:
        tx, ty, tw, th = TOP_COMBINED
        line1_rect = (tx, ty, tw, LINE_H)
        line2_rect = (tx, ty + LINE_H, tw, LINE_H)

        page = _get_top_page(has_factoid)
        if page == 1:
            fact1, fact2 = wrap_two_lines(state.factoid_headline, tw)
            draw_marquee(matrix_surface, "dj_top_fact1", fact1, line1_rect)
            draw_marquee(matrix_surface, "dj_top_fact2", fact2, line2_rect)
        elif artist_name:
            draw_marquee(matrix_surface, "dj_top_artist", artist_name, line1_rect)
            draw_marquee(matrix_surface, "dj_top_title", song_title, line2_rect)
        else:
            # No artist parsed yet -- let the title use both lines rather than
            # leaving half the surface dark.
            title1, title2 = wrap_two_lines(song_title, tw)
            draw_marquee(matrix_surface, "dj_top_title_only1", title1, line1_rect)
            draw_marquee(matrix_surface, "dj_top_title_only2", title2, line2_rect)

    # Refresh panel 3's status hold window whenever the AI pipeline's verdict
    # for the current track changes.
    kind = _status_kind()
    signature = (kind, state.factoid_track_key)
    if signature != _status_signature:
        _status_signature = signature
        _status_until = (t + STATUS_PANEL_HOLD_SECONDS) if kind else 0.0

    # DMX tempo beat-sync (Section 3): the idle animations and the Mystery
    # Band question-mark cycle both run off this beat-locked clock instead
    # of the raw wall clock `t`, so their frame steps stay in lockstep with
    # the DJ-mode tap-tempo period driving the DMX uplighting.
    anim_t = _advance_beat_clock(t)

    if mystery_live:
        _render_mystery_qmark_cycle(anim_t)
    else:
        for pid in (3, 4, 5, 6):
            rect = PANELS[pid]
            if pid == 6 and t < state.vol_overlay_until:
                _draw_volume_overlay(rect)
            elif pid == 3 and t < state.auto_dj_overlay_until:
                # Section 4: Gamepad Btn4 Auto-DJ toggle confirmation.
                draw_marquee(matrix_surface, "auto_dj_overlay", state.auto_dj_overlay_text,
                             rect, align="center")
            elif pid == 3 and t < state.auto_announce_overlay_until:
                # Gamepad Btn1 Auto-Announcement toggle confirmation ("v ON"/"v OFF").
                draw_marquee(matrix_surface, "auto_announce_overlay", state.auto_announce_overlay_text,
                             rect, align="center")
            elif pid == 3 and kind and t < _status_until:
                _render_status_panel(rect, t, kind)
            else:
                render_panel_animation(matrix_surface, pid, rect, anim_t)

    _draw_tempo_flash(t)


def _render_mystery_qmark_cycle(t):
    """Section 2: loops, in MYSTERY_QMARK_PHASE_SECONDS-long steps, through
    [question-mark on half the panels] -> [original animations] ->
    [question-mark on ALL 4 panels] -> [original animations] -- for as long
    as the Mystery Band teaser stays unresolved."""
    phase_idx = int(t / MYSTERY_QMARK_PHASE_SECONDS) % 4
    for i, pid in enumerate((3, 4, 5, 6)):
        rect = PANELS[pid]
        show_qmark = (phase_idx == 2) or (phase_idx == 0 and i % 2 == 0)
        if show_qmark:
            old_clip = matrix_surface.get_clip()
            matrix_surface.set_clip(pygame.Rect(rect))
            try:
                anim_question_mark(matrix_surface, rect, t)
            finally:
                matrix_surface.set_clip(old_clip)
        else:
            render_panel_animation(matrix_surface, pid, rect, t)


# Statuses that just mean "nothing to report yet" -- not an AI failure, so
# panel 3 stays on its dealt animation rather than showing the dancing-cat
# failure animation.
_AI_BENIGN_STATUSES = ("", "NO CONFIDENT TRACK ID", "FETCHING FACTOID...")


def _status_kind():
    """Which AI-pipeline indicator panel 3 owes the room right now:
    "credit" if a Btn6-triggered fetch just failed (out of credits/API
    error), "fail" for any other factoid/question failure, "ok" once a real
    AI-sourced question has loaded for the current track, or None when
    there's nothing to report (panel 3 then just animates)."""
    if state.coin_pop_flash_until and time.time() < state.coin_pop_flash_until:
        return "credit"
    if state.factoid_status not in _AI_BENIGN_STATUSES:
        return "fail"
    if state.factoid_headline and not state.quiz_is_test:
        return "ok"
    return None


def _render_status_panel(rect, t, kind):
    """Panel 3 briefly doubles as a status indicator for the AI factoid/quiz
    pipeline: a bursting star once a real question has loaded for the current
    track, a dancing cat face if the AI request failed or is unavailable (the
    reason is also echoed loudly to the console -- see
    drivers/factoid_engine.py), or a coin-pop specifically when a Btn6 quiz
    fetch attempt itself just failed ("out of credits")."""
    old_clip = matrix_surface.get_clip()
    matrix_surface.set_clip(pygame.Rect(rect))
    try:
        if kind == "credit":
            anim_coin_pop(matrix_surface, rect, t)
        elif kind == "fail":
            anim_dancing_cat(matrix_surface, rect, t)
        else:
            anim_star_burst(matrix_surface, rect, t)
    finally:
        matrix_surface.set_clip(old_clip)


def _branding_overlay_active(t):
    """Every BRANDING_OVERLAY_INTERVAL_SECONDS, for the first
    BRANDING_OVERLAY_DURATION_SECONDS of that window, the DJ branding string
    takes over the top strip (panels 1+2) in place of the artist/title
    display -- see _render_dj_branding_assembly."""
    if BRANDING_OVERLAY_INTERVAL_SECONDS <= 0:
        return False
    phase = t % BRANDING_OVERLAY_INTERVAL_SECONDS
    return phase < BRANDING_OVERLAY_DURATION_SECONDS


# ==========================================
# DJ BRANDING ASSEMBLY ANIMATION (top strip, panels 1+2)
# ==========================================
# Lightweight, hardware-friendly alternative to a physics/particle engine:
# each character gets a fixed target slot (its normal centered position in
# the combined 64x16 surface) and a random starting point just outside one
# of the surface's four edges, then linearly eases from start -> target over
# BRANDING_ASSEMBLY_DURATION_SECONDS. Once assembled it just holds in place
# (cheap: draw the same static positions every frame) for the rest of the
# BRANDING_OVERLAY_DURATION_SECONDS window, then _render_dj_mode cuts back to
# the normal artist/title display -- no fade-out pass needed.
_FLY_IN_MARGIN = 14

_branding_assembly_chars = []   # [{"char", "sx", "sy", "tx", "ty"}, ...]
_branding_assembly_text = None
_branding_assembly_cycle_id = None
_branding_assembly_cycle_start = 0.0


def _random_edge_start(x0, y0, w, h):
    """A random point just beyond one of the four edges of (x0, y0, w, h),
    so characters visibly fly in from top/bottom/left/right."""
    edge = random.choice(("top", "bottom", "left", "right"))
    if edge == "top":
        return random.uniform(x0, x0 + w), y0 - _FLY_IN_MARGIN
    if edge == "bottom":
        return random.uniform(x0, x0 + w), y0 + h + _FLY_IN_MARGIN
    if edge == "left":
        return x0 - _FLY_IN_MARGIN, random.uniform(y0, y0 + h)
    return x0 + w + _FLY_IN_MARGIN, random.uniform(y0, y0 + h)


def _deal_branding_assembly(text, rect):
    global _branding_assembly_chars
    x0, y0, w, h = rect
    total_w = text_width(text)
    target_x = x0 + max(0, (w - total_w) // 2)
    target_y = y0 + max(0, (h - GLYPH_HEIGHT) // 2)

    chars = []
    cx = target_x
    for ch in text:
        sx, sy = _random_edge_start(x0, y0, w, h)
        chars.append({"char": ch, "sx": sx, "sy": sy, "tx": cx, "ty": target_y})
        cx += char_width(ch) + GLYPH_GAP
    _branding_assembly_chars = chars


def _render_dj_branding_assembly(t):
    global _branding_assembly_text, _branding_assembly_cycle_id, _branding_assembly_cycle_start

    rect = TOP_COMBINED
    text = get_current_text()
    cycle_id = int(t // BRANDING_OVERLAY_INTERVAL_SECONDS)

    if cycle_id != _branding_assembly_cycle_id or text != _branding_assembly_text:
        _deal_branding_assembly(text, rect)
        _branding_assembly_cycle_id = cycle_id
        _branding_assembly_text = text
        _branding_assembly_cycle_start = cycle_id * BRANDING_OVERLAY_INTERVAL_SECONDS

    elapsed = t - _branding_assembly_cycle_start
    progress = 1.0
    if BRANDING_ASSEMBLY_DURATION_SECONDS > 0:
        progress = min(1.0, elapsed / BRANDING_ASSEMBLY_DURATION_SECONDS)
    eased = 1.0 - (1.0 - progress) ** 3  # ease-out cubic: fast approach, snappy settle

    old_clip = matrix_surface.get_clip()
    matrix_surface.set_clip(pygame.Rect(rect))
    try:
        for c in _branding_assembly_chars:
            x = c["sx"] + (c["tx"] - c["sx"]) * eased
            y = c["sy"] + (c["ty"] - c["sy"]) * eased
            draw_bitmap_text(matrix_surface, c["char"], int(round(x)), int(round(y)), color=RED_FULL)
    finally:
        matrix_surface.set_clip(old_clip)


def _render_price_banner(t):
    """70s/80s Price Game intro, phase 2: a temporary banner on the top
    strip (panels 1+2) while the bottom panels keep idle-animating."""
    label = f"{state.price_game_decade.upper()} PRICE GAME!"
    draw_marquee(matrix_surface, "price_game_banner", label, TOP_COMBINED, align="center")
    for pid in (3, 4, 5, 6):
        render_panel_animation(matrix_surface, pid, PANELS[pid], t)


def _draw_tempo_flash(t):
    """Btn5 tap-tempo visual feedback: a red outline on panels 3-6 that
    fades to 0 over TEMPO_FLASH_DECAY_SECONDS from the moment of the tap."""
    if not state.tempo_flash_at:
        return
    elapsed = t - state.tempo_flash_at
    if elapsed >= TEMPO_FLASH_DECAY_SECONDS:
        return
    intensity = 1.0 - (elapsed / TEMPO_FLASH_DECAY_SECONDS)
    color = tuple(int(c * intensity) for c in RED_FULL)
    for pid in (3, 4, 5, 6):
        x0, y0, w, h = PANELS[pid]
        pygame.draw.rect(matrix_surface, color, (x0, y0, w, h), 1)


def _draw_volume_overlay(rect):
    x0, y0, w, h = rect
    vol = state.music_volume
    draw_marquee(matrix_surface, "vol_label", f"{vol}%", (x0, y0, w, LINE_H), align="center")

    bar_outline = (x0 + 2, y0 + 9, w - 4, 5)
    pygame.draw.rect(matrix_surface, RED_DIM, bar_outline, 1)
    inner_w = w - 6
    fill_w = int(inner_w * (vol / 100.0))
    if fill_w > 0:
        pygame.draw.rect(matrix_surface, RED_FULL, (x0 + 3, y0 + 10, fill_w, 3))


def _ensure_quiz_content():
    """If no real AI-sourced question is loaded (no confident track ID yet,
    AI disabled, network down, etc.), auto-load a local placeholder so the
    select -> grade -> DMX/sound flow can still be tested end to end. Gets
    replaced the instant a real factoid arrives (see factoid_engine)."""
    if state.factoid_question:
        return
    mock = build_mock_question()
    state.factoid_question = mock["question"]
    state.factoid_choices = mock["choices"]
    state.factoid_correct_index = mock["correct_index"]
    state.factoid_correction = ""
    state.quiz_is_test = True
    state.quiz_selected_index = -1
    state.quiz_locked = False
    print("[QUIZ] No AI question loaded yet -- auto-loaded a TEST question so the answer flow can be exercised.")


def _draw_selected_panel(rect, key, text):
    """Selected-but-ungraded answer: dim red fill, black text. The matrix
    hardware is red-only, so "armed" is signalled by a dim (rather than
    bright) fill instead of a distinct hue."""
    draw_marquee(matrix_surface, key, text, rect, color=RED_DIM, invert=True)


def _draw_winning_flash(rect, key, text, t):
    """The graded-correct panel: brightly flashing red, pulsing at the
    same cadence as trigger_big_win()'s DMX pulse so the light and the
    panel read as one effect. Since the matrix can't show green, the win
    is made obvious by motion (pulsing) rather than color."""
    elapsed = t - state.quiz_graded_at
    cps = 4.0
    cycle_phase = (elapsed * cps) % 1.0
    saw = 1.0 - cycle_phase
    intensity = 0.35 + saw * 0.65
    color = tuple(int(c * intensity) for c in RED_FULL)
    draw_marquee(matrix_surface, key, text, rect, color=color, invert=True)


def _draw_demo_winner_hint(rect, key, text, t):
    """Demo/test mode only (state.quiz_is_test): blink a border around
    the actual correct answer before it's graded, so an operator can
    demo the select -> grade -> win flow clearly even when there's no
    real quiz question loaded (and therefore nothing to spoil)."""
    x0, y0, w, h = rect
    if int(t * 2.5) % 2 == 0:
        pygame.draw.rect(matrix_surface, RED_FULL, (x0, y0, w, h), 1)
    draw_marquee(matrix_surface, key, text, rect)


def _render_quiz_stats_or_return(t, elapsed):
    """Called once the win/loss celebration window has elapsed. Shows a
    "SCORE: X/Y" stats page on panels 1+2 for the remainder of
    GAME_SCORECARD_HOLD_SECONDS (QUIZ_CELEBRATION_HOLD_SECONDS +
    QUIZ_STATS_HOLD_SECONDS = 5s total), then either auto-advances to the
    next pre-fetched question for this track (staying in GAME_MODE) or
    returns to DJ mode if none remain."""
    stats_elapsed = elapsed - QUIZ_CELEBRATION_HOLD_SECONDS
    if stats_elapsed < QUIZ_STATS_HOLD_SECONDS:
        tx, ty, tw, th = TOP_COMBINED
        line1_rect = (tx, ty, tw, LINE_H)
        line2_rect = (tx, ty + LINE_H, tw, LINE_H)
        score_line = f"SCORE: {state.quiz_score_correct}/{state.quiz_score_total}"
        was_correct = state.quiz_selected_index == state.factoid_correct_index
        draw_marquee(matrix_surface, "quiz_stats1", score_line, line1_rect, align="center")
        draw_marquee(matrix_surface, "quiz_stats2",
                     "NICE ROUND!" if was_correct else "TRY AGAIN!", line2_rect, align="center")
        return

    if advance_to_next_queued_question():
        state.set_message("NEXT QUESTION", 1.0)
        print("[QUIZ] Auto-advancing to next pre-fetched question for this track -- staying in GAME_MODE.")
        return

    # Reset rule: Fixture 1 -> black (lighting_engine.py also forces this
    # every frame in DJ mode; this just makes the intent explicit here).
    state.mode = state.MODE_DJ
    state.fixture1_mode = "off"
    state.quiz_locked = False
    state.quiz_selected_index = -1
    state.active_option = None
    state.set_message("MODE: DJ", 1.5)
    print("[QUIZ] No more queued questions for this track -- auto-returning to DJ mode.")


def _render_quiz_mode(t):
    # Quiz content is loaded once, up front, by the Btn6 gate in
    # inputs/gamepad.py -- no per-frame fetching happens here anymore.
    if state.quiz_locked:
        elapsed = t - state.quiz_graded_at
        if elapsed >= QUIZ_CELEBRATION_HOLD_SECONDS:
            _render_quiz_stats_or_return(t, elapsed)
            return

    _ensure_quiz_content()

    tx, ty, tw, th = TOP_COMBINED
    choices = state.factoid_choices
    sel = state.quiz_selected_index
    correct = state.factoid_correct_index
    locked = state.quiz_locked
    is_correct_grade = locked and sel == correct

    # True/False correction reveal: once graded, if the statement's correct
    # answer was "False", swap the top display over to the actual true fact
    # (state.factoid_correction) instead of leaving the room with just the
    # word FALSE -- see drivers/factoid_engine.py's "correction" field.
    is_true_false = choices == ["True", "False"]
    show_correction = locked and is_true_false and correct == 1 and state.factoid_correction

    if show_correction:
        line1, line2 = wrap_two_lines(f"FALSE -- {state.factoid_correction}", tw)
        draw_marquee(matrix_surface, "quiz_correction1", line1, (tx, ty, tw, LINE_H))
        draw_marquee(matrix_surface, "quiz_correction2", line2, (tx, ty + LINE_H, tw, LINE_H))
    else:
        question_text = state.factoid_question
        if state.quiz_is_test:
            question_text = "[test] " + question_text
        line1, line2 = wrap_two_lines(question_text, tw)
        draw_marquee(matrix_surface, "quiz_q1", line1, (tx, ty, tw, LINE_H))
        draw_marquee(matrix_surface, "quiz_q2", line2, (tx, ty + LINE_H, tw, LINE_H))

    for i, pid in enumerate((3, 4, 5, 6)):
        rect = PANELS[pid]
        key = f"quiz_choice_{i}"

        if i >= len(choices):
            draw_marquee(matrix_surface, key, "----", rect, align="center")
            continue

        text = choices[i]

        if locked and is_correct_grade and i == sel:
            # The winning box: brightly pulsing red, ding + pulsing DMX
            # already fired by grade_quiz_selection() -> trigger_big_win().
            # The pulse (not a color change) is what makes it obvious.
            _draw_winning_flash(rect, key, text, t)
        elif locked and is_correct_grade:
            # The other three boxes celebrate the win, dim and static so
            # the pulsing winner still stands out.
            draw_marquee(matrix_surface, key, "winner!", rect, color=RED_DIM, invert=True, align="center")
        elif locked and i == sel:
            # Wrong answer selected -- steady bright red (not pulsing,
            # to stay visually distinct from the winning flash).
            draw_marquee(matrix_surface, key, text, rect, color=RED_FULL, invert=True)
        elif locked and i == correct:
            # Reveal the correct answer dimly so the room learns it.
            draw_marquee(matrix_surface, key, text, rect, color=RED_DIM, invert=True)
        elif not locked and i == sel:
            _draw_selected_panel(rect, key, text)
        elif not locked and i == correct and state.quiz_is_test:
            # Demo mode: no real quiz is loaded, so it's fine (and
            # helpful for demoing the flow) to hint the winning square.
            _draw_demo_winner_hint(rect, key, text, t)
        else:
            draw_marquee(matrix_surface, key, text, rect)


def _render_space_invaders(t):
    """Space Invaders mini-game (DJ-mode Btn1+Btn3 combo Easter egg): draws
    the cannon, invader formation, in-flight bullets, and brief explosion
    flashes straight from drivers/space_invaders_engine.py's state.si_*
    fields -- the engine owns all simulation, this only paints the current
    snapshot. Spans the full matrix canvas rather than a single panel (see
    config.py's SPACE INVADERS section for why)."""
    player_top_y = MATRIX_HEIGHT - SI_PLAYER_Y_FROM_BOTTOM - SI_PLAYER_HEIGHT
    pygame.draw.rect(
        matrix_surface, RED_FULL,
        (int(round(state.si_player_x)), player_top_y, SI_PLAYER_WIDTH, SI_PLAYER_HEIGHT),
    )

    for inv in state.si_invaders:
        if not inv["alive"]:
            continue
        pygame.draw.rect(
            matrix_surface, RED_FULL,
            (int(round(inv["x"])), int(round(inv["y"])), SI_INVADER_SIZE, SI_INVADER_SIZE),
        )

    for b in state.si_bullets:
        pygame.draw.rect(matrix_surface, RED_FULL, (int(round(b["x"])), int(round(b["y"])), 1, 2))

    for e in state.si_explosions:
        pygame.draw.rect(
            matrix_surface, RED_DIM,
            (int(round(e["x"])) - 1, int(round(e["y"])) - 1, SI_INVADER_SIZE + 2, SI_INVADER_SIZE + 2),
            1,
        )


def update_matrix_canvas():
    matrix_surface.fill(BLACK)
    t = time.time()

    if state.mode == state.MODE_SPACE_INVADERS:
        _render_space_invaders(t)
    elif state.price_game_active and state.price_game_phase == "banner":
        _render_price_banner(t)
    elif state.mode == state.MODE_DJ:
        _render_dj_mode(t)
    else:
        _render_quiz_mode(t)


def render_led_grid():
    screen.fill((5, 0, 0))
    for y in range(MATRIX_HEIGHT):
        for x in range(MATRIX_WIDTH):
            color = matrix_surface.get_at((x, y))
            if color == (0, 0, 0, 255):
                color = RED_OFF

            rect = (
                x * PIXEL_SCALE + GAP,
                y * PIXEL_SCALE + GAP,
                PIXEL_SCALE - GAP,
                PIXEL_SCALE - GAP
            )
            pygame.draw.rect(screen, color, rect)

    # Physical panel seam overlay -- one border per real 32x16 panel.
    seam_color = (80, 0, 0)
    for px, py, pw, ph in PANELS.values():
        rect = (px * PIXEL_SCALE, py * PIXEL_SCALE, pw * PIXEL_SCALE, ph * PIXEL_SCALE)
        pygame.draw.rect(screen, seam_color, rect, 1)

    pygame.display.flip()
