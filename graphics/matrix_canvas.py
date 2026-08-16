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
    TEMPO_FLASH_DECAY_SECONDS, HOT_TRACK_FLASH_PHASE_SECONDS,
    QUIZ_CELEBRATION_HOLD_SECONDS, QUIZ_STATS_HOLD_SECONDS,
    QUIZ_TF_CORRECTION_HOLD_SECONDS,
    BRANDING_OVERLAY_INTERVAL_SECONDS, BRANDING_OVERLAY_DURATION_SECONDS,
    BRANDING_ASSEMBLY_DURATION_SECONDS, DJ_COLOR_PALETTE,
    MYSTERY_QMARK_PHASE_SECONDS, MYSTERY_REVEAL_BLINK_PERIOD_SECONDS,
    TEMPO_PERIOD_DEFAULT_SECONDS,
    SI_PLAYER_WIDTH, SI_PLAYER_HEIGHT, SI_PLAYER_Y_FROM_BOTTOM, SI_INVADER_SIZE,
    SI_ROW23_GAP_PX, PANEL_W, PANEL_H,
    CANVAS_WINDOW_TITLE, BTN1_STATUS_PULSE_INTERVAL_SECONDS,
)
import config
from state import state
from drivers.deck_orchestrator import get_now_playing as get_rekordbox_track
from drivers.factoid_engine import build_mock_question, advance_to_next_queued_question
from drivers.branding_engine import get_current_text
from drivers.announcement_engine import get_text_for as get_announcement_text_for
from drivers import led_bridge
from drivers.dmx_driver import dmx
from drivers import tunnel_engine
from drivers import idle_cycle_engine
from graphics.text_render import (
    draw_marquee, wrap_two_lines, draw_bitmap_text, text_width, char_width,
    GLYPH_GAP, GLYPH_HEIGHT,
)
from graphics.animations import (
    render_panel_animation, deal_panel_animations,
    anim_dancing_cat, anim_star_burst, anim_coin_pop, anim_question_mark,
    anim_bold_star, anim_bold_dollar, anim_arrow_down,
)
from graphics import overlay_panel
from graphics import secondary_canvas

# Initialize Pygame Display Engine
screen = pygame.display.set_mode((WINDOW_W, WINDOW_H))
pygame.display.set_caption(CANVAS_WINDOW_TITLE)


def _pin_window_always_on_top():
    """Pygame has no native Always-On-Top flag, so pin the actual OS window
    via the Win32 API once it exists. Originally kept the simulator layered
    above Rekordbox and every other desktop window so its fixed top-left
    position (and therefore the OCR crop coordinates measured against it)
    could never get buried/occluded by a window click. Rekordbox OCR
    (drivers/rekordbox_driver.py) is gone -- native track detection doesn't
    care about window position/Z-order at all -- so this is no longer
    called at import time (2026-08-11, "relax window focus"). Left in place,
    unused, in case a future reason to pin the window comes up. Best-effort:
    a non-Windows box or a missing pywin32 install just leaves the window
    in normal Z-order."""
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
    elif state.announcement_banner_from <= t < state.announcement_banner_until:
        # Announcement tag-phrase banner: a single-line plain text string
        # across the top 2 panels, right after a station-announcement VO's
        # board-wipe finishes. Takes priority over the periodic branding
        # takeover below since it's a short one-shot window tied to a
        # specific transition, not a recurring cycle. Diagnostic fallback
        # (2026-08-10): if the saved text ever comes back empty, show a
        # visible placeholder on the actual display instead of a silent
        # blank strip -- makes it obvious from the rig itself whether this
        # render path is even being reached vs. the text just being blank.
        banner_text = (state.announcement_banner_text_override
                       or get_announcement_text_for(state.last_announcement_filename)
                       or "[NO ANNOUNCEMENT TEXT SET]")
        draw_marquee(matrix_surface, "announcement_banner", banner_text, TOP_COMBINED, align="center")
    elif state.idle_cycle_phase == "goal" and not state.intermission_active:
        # GOAL PAGE header: "ROUND N" / "<win score> pt to WIN" -- the
        # panels 3-6 scorecard is drawn by _render_idle_cycle_panel().
        # Suppressed during intermission (2026-08-12 fix): every score was
        # just reset to 0 for the NEXT game, so "ROUND N / N pt to WIN"
        # showing up here read as stale/wrong info rather than a genuine
        # goal page -- panels 5+6 already carry the real "NEXT GAME M:SS"
        # countdown during this window, this just leaves 1+2 blank instead
        # of fighting it with unrelated numbers.
        tx, ty, tw, th = TOP_COMBINED
        line1_rect = (tx, ty, tw, LINE_H)
        line2_rect = (tx, ty + LINE_H, tw, LINE_H)
        draw_marquee(matrix_surface, "goal_round", f"ROUND {state.game_round_number}", line1_rect, align="center")
        draw_marquee(matrix_surface, "goal_score", f"{state.game_win_score} pt to WIN", line2_rect, align="center")
    elif state.idle_cycle_phase == "goal" and state.intermission_active:
        pass  # blank top strip -- see comment above
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

    if mystery_live and state.round_first_answer_at:
        # Beta-Fix Feature Set item 2: once anyone has answered the
        # teaser-answering window, panels 3-6 flip from the qmark/idle
        # cycle to the actual answer options -- panels 1+2 keep showing
        # "Who is this?" unchanged (see mystery_live branch above).
        _draw_answer_choice_panels(
            state.factoid_choices, state.factoid_correct_index,
            state.quiz_selected_index, state.quiz_locked, anim_t,
        )
    elif mystery_live:
        _render_mystery_qmark_cycle(anim_t)
    else:
        for pid in (3, 4, 5, 6):
            rect = PANELS[pid]
            if pid == 3 and (state.btn1_hold_overlay_active or t < state.btn1_hold_overlay_until):
                # Btn1 hold (+ its 10s post-release persistence window):
                # confidence/availability status overlay -- highest priority
                # on panel 3, takes over even the AI-pipeline status indicator.
                _render_btn1_status_overlay(rect, t)
            elif pid == 4 and state.btn3_swear_toggle_active:
                # Btn3 held: swear-tag toggle confirmation on the last-played
                # announcement clip (no persistence -- hides the instant the
                # button is released). Panel 4 has no other override in this
                # chain, so this doesn't fight anything on panel 3.
                _render_swear_toggle_overlay(rect, t)
            elif pid == 6 and t < state.vol_overlay_until:
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
            elif pid in (5, 6) and state.intermission_active:
                # Post-win intermission (2026-08-11): bottom panels show a
                # "NEXT GAME M:SS" countdown instead of their normal idle
                # content -- panels 3-4 keep cycling normally, music keeps
                # playing, only the mystery teaser is suppressed elsewhere
                # (drivers/mystery_band_engine.py).
                _render_intermission_countdown(pid, rect)
            else:
                _render_idle_cycle_panel(pid, rect, anim_t)

    _draw_tempo_flash(t)
    _draw_hot_track_flash(t)


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


# panel_id -> which two leaderboard ranks (0-indexed within the current
# page) that panel shows, stacked as two 8px text rows.
_LEADERBOARD_PANEL_RANK_SLOTS = {3: (0, 1), 4: (4, 5), 5: (2, 3), 6: (6, 7)}

# panel_id -> "PRESS START" prompt split across all 4 panels so the space
# isn't just 4 copies of the same text.
_START_GAME_PANEL_WORD = {3: "TRIVA", 4: "NITE", 5: "SCAN", 6: "QR!"}


def _render_idle_cycle_panel(pid, rect, t):
    """DJ-mode fallback content for panels 3-6 when no higher-priority
    overlay is active: cycles START GAME prompt -> leaderboard -> dancing
    animations (drivers/idle_cycle_engine.py owns the phase timing; this
    just draws whatever the current phase says). Beta-Fix Feature Set
    items 1 (fill wasted idle space) and 4 (leaderboard)."""
    phase = state.idle_cycle_phase

    if phase == "start_game":
        # One distinct word per panel, in reading order (2026-08-10 fix --
        # previously doubled the same word across panel pairs).
        draw_marquee(matrix_surface, f"idle_start_{pid}", _START_GAME_PANEL_WORD.get(pid, ""),
                     rect, align="center")
        return

    if phase == "leaderboard":
        ranked = idle_cycle_engine.ranked_players()
        page = state.idle_cycle_leaderboard_page
        page_start = page * config.IDLE_LEADERBOARD_PAGE_SIZE
        slot_a, slot_b = _LEADERBOARD_PANEL_RANK_SLOTS[pid]
        x0, y0, w, h = rect
        for slot, y in ((slot_a, y0), (slot_b, y0 + LINE_H)):
            overall_index = page_start + slot
            if overall_index >= len(ranked):
                continue
            p = ranked[overall_index]
            rank = overall_index + 1
            # "1-ABC" not "#1 ABC" -- shorter so it fits the 32px panel
            # width without triggering the marquee scroll (2026-08-10 fix).
            label = f"{rank}-{p['initials']}"
            draw_marquee(matrix_surface, f"idle_board_{pid}_{slot}", label, (x0, y, w, LINE_H))
        return

    if phase == "goal" and state.intermission_active:
        # Blank, same reason as the top-strip header in _render_dj_mode --
        # every score was just reset to 0 for the next game, so this
        # scorecard would show stale/wrong numbers during the break.
        return

    if phase == "goal":
        # GOAL PAGE scorecard: one player per panel-pair -- panels (3,4)
        # show the higher-ranked player of this page (name-dash / score),
        # panels (5,6) show the next one down. Top 4 players only, 2 per
        # page (config.IDLE_GOAL_PAGE_SIZE), header ("ROUND N" / win score)
        # drawn separately on the top-strip in _render_dj_mode.
        ranked = idle_cycle_engine.ranked_players()[:4]
        page = state.idle_cycle_goal_page
        page_start = page * config.IDLE_GOAL_PAGE_SIZE
        pair_index = 0 if pid in (3, 4) else 1
        player_index = page_start + pair_index
        if player_index < len(ranked):
            p = ranked[player_index]
            text = f"{p['initials']} -" if pid in (3, 5) else f"{p['score']} pt"
            draw_marquee(matrix_surface, f"idle_goal_{pid}", text, rect, align="center")
        return

    # "dancing" (or any unrecognized phase) -- today's existing behavior.
    render_panel_animation(matrix_surface, pid, rect, t)


def _render_intermission_countdown(pid, rect):
    """Post-win intermission (2026-08-11): panel 5 = "NEXT GAME" label
    (or "PAUSED" -- 2026-08-12, web remote Pause control), panel 6 = live
    M:SS countdown, frozen while paused instead of ticking down."""
    # Lazy import: drivers/win_sequence_engine.py imports drivers/
    # deck_orchestrator.py at its own top level, which lazily imports this
    # module back inside a function -- importing win_sequence_engine at
    # THIS module's top level would risk the same circularity.
    from drivers import win_sequence_engine
    if pid == 5:
        label = "PAUSED" if state.intermission_paused else "NEXT GAME"
        draw_marquee(matrix_surface, "intermission_label", label, rect, align="center")
        return
    remaining = win_sequence_engine.get_intermission_remaining_seconds(time.time())
    minutes, seconds = divmod(int(remaining), 60)
    draw_marquee(matrix_surface, "intermission_countdown", f"{minutes}:{seconds:02d}", rect, align="center")


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


def _btn1_overlay_kind():
    """Which icon the Btn1-hold status overlay shows: dollar if Price Game
    is armed/in-progress for the current track, star if track questions are
    queued and ready, otherwise the red arrow (no questions -- still
    fetching, or AI exhausted)."""
    if state.price_game_pending or state.price_game_active:
        return "dollar"
    if state.track_question_queue:
        return "star"
    return "arrow"


def _render_btn1_status_overlay(rect, t):
    """Btn1 hold (Section: joystick remap + status overlays), panel 3: bold
    pulsing star/dollar/arrow reflecting whether track questions and/or
    Price Game are available for the current track. See
    inputs/gamepad.py::_process_btn1_hold()/_handle_btn1_release()."""
    kind = _btn1_overlay_kind()
    old_clip = matrix_surface.get_clip()
    matrix_surface.set_clip(pygame.Rect(rect))
    try:
        if kind == "dollar":
            anim_bold_dollar(matrix_surface, rect, t)
        elif kind == "star":
            anim_bold_star(matrix_surface, rect, t)
        else:
            anim_arrow_down(matrix_surface, rect, t)
    finally:
        matrix_surface.set_clip(old_clip)


def _render_swear_toggle_overlay(rect, t):
    """Btn3 held, panel 4: confirms the swear-tag flip that just happened on
    the last-played announcement clip -- "*#@!" if the press just tagged it,
    "a-ok" if it just untagged it. Same marquee text path as the volume/
    toggle overlays."""
    draw_marquee(matrix_surface, "btn3_swear_toggle", state.btn3_swear_toggle_text, rect, align="center")


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
    decade = state.price_game_decade.upper()
    label = f"{decade} PRICE GAME!" if decade else "PRICE GAME!"
    draw_marquee(matrix_surface, "price_game_banner", label, TOP_COMBINED, align="center")
    for pid in (3, 4, 5, 6):
        render_panel_animation(matrix_surface, pid, PANELS[pid], t)


def _render_win_celebration(t):
    """drivers/win_sequence_engine.py's duck->applause->restore window
    (2026-08-12 fix): the top banner used to just go blank for this whole
    stretch, which read as the board having frozen or crashed right at the
    moment it should be the most exciting. Shows "<INITIALS> WINS!" instead,
    blinking in sync with the same rhythm the Mystery Band reveal uses so it
    reads as a deliberate celebration beat, not a stuck line. Bottom panels
    keep idle-animating rather than sitting dark too."""
    player = state.quiz_players.get(state.game_winner_player_id)
    initials = (player or {}).get("initials", "").strip()
    label = f"{initials} WINS!" if initials else "WINNER!"
    blink_on = int(t / MYSTERY_REVEAL_BLINK_PERIOD_SECONDS) % 2 == 0
    draw_marquee(matrix_surface, "win_celebration_banner", label, TOP_COMBINED,
                 align="center", invert=blink_on)
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


def _draw_hot_track_flash(t):
    """Hot Track "trivia ready" confirmation (drivers/hot_track_engine.py):
    4 alternating phases of HOT_TRACK_FLASH_PHASE_SECONDS each -- reverse
    (solid bright fill) / normal (no-op, whatever's already drawn stands) /
    reverse / normal -- then self-clears. Same cumulative-phase-boundary
    style as drivers/lighting_engine.py::_song_intro_state(). Drawn
    unconditionally right after _draw_tempo_flash(t) (see call site) so it
    always composites on top regardless of what else panels 3-6 are showing
    that frame -- same guarantee _draw_tempo_flash relies on."""
    if not state.hot_track_flash_started_at:
        return
    elapsed = t - state.hot_track_flash_started_at
    phase = HOT_TRACK_FLASH_PHASE_SECONDS
    t1, t2, t3, t4 = phase, phase * 2, phase * 3, phase * 4
    if elapsed >= t4:
        state.hot_track_flash_started_at = 0.0
        return
    reverse = elapsed < t1 or t2 <= elapsed < t3
    if not reverse:
        return  # "normal" phase -- whatever's already drawn this frame stands
    for pid in (3, 4, 5, 6):
        x0, y0, w, h = PANELS[pid]
        pygame.draw.rect(matrix_surface, RED_FULL, (x0, y0, w, h))  # solid fill, not an outline


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


def _draw_countdown_bar(rect):
    """30s question-timeout bar (drivers/live_round_engine.py) -- same
    outline+fill pixel math as _draw_volume_overlay's bar, just thinner and
    without a numeric label (it lives in the freed-up strip under the
    question text, only 2px tall)."""
    x0, y0, w, h = rect
    remaining = max(0.0, state.round_deadline_at - time.time())
    frac = min(1.0, remaining / config.QUESTION_TIMEOUT_SECONDS) if config.QUESTION_TIMEOUT_SECONDS > 0 else 0.0
    fill_w = int(w * frac)
    if fill_w > 0:
        pygame.draw.rect(matrix_surface, RED_FULL, (x0, y0, fill_w, h))


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


def _draw_selected_panel(rect, key, text, scroll=True):
    """Selected-but-ungraded answer: dim red fill, black text. The matrix
    hardware is red-only, so "armed" is signalled by a dim (rather than
    bright) fill instead of a distinct hue."""
    draw_marquee(matrix_surface, key, text, rect, color=RED_DIM, invert=True, scroll=scroll)


def _draw_winning_flash(rect, key, text, t, scroll=True):
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
    draw_marquee(matrix_surface, key, text, rect, color=color, invert=True, scroll=scroll)


def _draw_demo_winner_hint(rect, key, text, t, scroll=True):
    """Demo/test mode only (state.quiz_is_test): blink a border around
    the actual correct answer before it's graded, so an operator can
    demo the select -> grade -> win flow clearly even when there's no
    real quiz question loaded (and therefore nothing to spoil)."""
    x0, y0, w, h = rect
    if int(t * 2.5) % 2 == 0:
        pygame.draw.rect(matrix_surface, RED_FULL, (x0, y0, w, h), 1)
    draw_marquee(matrix_surface, key, text, rect, scroll=scroll)


def _render_quiz_stats_or_return(t, elapsed, celebration_hold):
    """Called once the win/loss celebration window has elapsed. Shows a
    "SCORE: X/Y" stats page on panels 1+2 for QUIZ_STATS_HOLD_SECONDS
    (`celebration_hold` is whichever hold duration actually applied --
    QUIZ_CELEBRATION_HOLD_SECONDS normally, or the longer
    QUIZ_TF_CORRECTION_HOLD_SECONDS after a wrong True/False answer -- so the
    stats page always gets its normal viewing time regardless of which one
    just ran), then either auto-advances to the next pre-fetched question
    for this track (staying in GAME_MODE) or returns to DJ mode if none
    remain."""
    stats_elapsed = elapsed - celebration_hold
    if stats_elapsed < QUIZ_STATS_HOLD_SECONDS:
        tx, ty, tw, th = TOP_COMBINED
        line1_rect = (tx, ty, tw, LINE_H)
        line2_rect = (tx, ty + LINE_H, tw, LINE_H)

        if state.quiz_players:
            # Multiplayer "NICE ROUND" scoreboard -- every signed-up
            # player's running score, not just this round's results (see
            # web/remote_server.py's /api/player/join for how players get
            # into state.quiz_players).
            correct_count = sum(1 for r in state.quiz_last_round_results if r["correct"])
            board = "  ".join(f"{p['initials']}:{p['score']}" for p in state.quiz_players.values())
            draw_marquee(matrix_surface, "quiz_stats1", "NICE ROUND!", line1_rect, align="center")
            draw_marquee(matrix_surface, "quiz_stats2", board or "NO SCORES YET", line2_rect)
        else:
            score_line = f"SCORE: {state.quiz_score_correct}/{state.quiz_score_total}"
            was_correct = state.quiz_selected_index == state.factoid_correct_index
            draw_marquee(matrix_surface, "quiz_stats1", score_line, line1_rect, align="center")
            draw_marquee(matrix_surface, "quiz_stats2",
                         "NICE ROUND!" if was_correct else "TRY AGAIN!", line2_rect, align="center")
        return

    # Price Game (Btn6, drivers/price_bank_engine.py) is a one-off bonus
    # round, never a segue into this track's normal trivia queue -- skip
    # the auto-advance check entirely so grading it always falls straight
    # through to the "back to DJ mode" cleanup below, even if this track
    # happens to have real pre-fetched trivia questions sitting queued.
    if state.factoid_category != "price_game" and advance_to_next_queued_question():
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
    state.game_active = False  # era-trivia scheduling: next question starts a fresh game
    # Confirmed cause of the reported double win/loss sound on the last
    # question of a track (2026-08-11 fix): quiz_locked resets to False
    # right above, but state.factoid_choices was never cleared -- on the
    # very next frame, drivers/live_round_engine.py's "active" check
    # (factoid_choices set AND not quiz_locked) saw this stale,
    # already-graded question as newly live again, and since its deadline
    # had already passed, immediately re-fired grade_quiz_selection()
    # (and its win/loss sound) a second time. Clearing these makes the
    # question genuinely gone, not just unlocked.
    state.factoid_choices = []
    state.factoid_question = ""
    state.round_deadline_at = 0.0
    state.set_message("MODE: DJ", 1.5)
    print("[QUIZ] No more queued questions for this track -- auto-returning to DJ mode.")


def _render_quiz_mode(t):
    # Quiz content is loaded once, up front, by the Btn6 gate in
    # inputs/gamepad.py -- no per-frame fetching happens here anymore.
    #
    # is_true_false/show_correction are computed here, ahead of the hold-time
    # gate below, because the hold duration itself depends on them: the
    # correction reveal is scrolling marquee text (graphics/text_render.py)
    # that needs QUIZ_TF_CORRECTION_HOLD_SECONDS to actually finish a scroll
    # pass, not the much shorter QUIZ_CELEBRATION_HOLD_SECONDS every other
    # grade result uses (2026-08-09 fix -- the correction was getting cut off
    # before the room could read it, sometimes before it even finished its
    # first scroll pass).
    choices = state.factoid_choices
    correct = state.factoid_correct_index
    locked = state.quiz_locked
    is_true_false = choices == ["True", "False"]
    show_correction = locked and is_true_false and correct == 1 and state.factoid_correction
    if state.round_timed_out:
        # 30s question timeout (drivers/live_round_engine.py): "TIMES UP!"
        # then correct-answer-only reveal, distinct (longer) hold than the
        # normal win/loss celebration.
        celebration_hold = config.TIMESUP_HOLD_SECONDS
    else:
        celebration_hold = QUIZ_TF_CORRECTION_HOLD_SECONDS if show_correction else QUIZ_CELEBRATION_HOLD_SECONDS

    if state.quiz_locked:
        elapsed = t - state.quiz_graded_at
        if elapsed >= celebration_hold:
            _render_quiz_stats_or_return(t, elapsed, celebration_hold)
            return

    _ensure_quiz_content()

    tx, ty, tw, th = TOP_COMBINED
    sel = state.quiz_selected_index
    is_correct_grade = locked and sel == correct

    # Multiplayer "who got it right" -- once graded, replaces the question/
    # correction text with the names of every player whose locked-in answer
    # was correct (state.quiz_last_round_results, set by
    # inputs/gamepad.py::_grade_multiplayer_round()). Falls through to the
    # normal single-player question/correction display the rest of the
    # time, and whenever nobody's signed up.
    if locked and state.round_timed_out:
        # 30s timeout: "TIMES UP!" takes priority over the normal
        # correction/correct-names display, then falls through to the
        # existing hold-and-advance flow via celebration_hold above.
        draw_marquee(matrix_surface, "quiz_timesup", "TIMES UP!", (tx, ty, tw, LINE_H), align="center")
        q_line1, _ = wrap_two_lines(state.factoid_question, tw)
        draw_marquee(matrix_surface, "quiz_timesup_q", q_line1, (tx, ty + LINE_H, tw, LINE_H))
    elif locked and state.quiz_players:
        correct_names = [r["initials"] for r in state.quiz_last_round_results if r["correct"]]
        line = ("CORRECT: " + ", ".join(correct_names)) if correct_names else "NOBODY GOT IT THIS ROUND"
        draw_marquee(matrix_surface, "quiz_correct_names", line, (tx, ty, tw, LINE_H))
    elif show_correction:
        line1, line2 = wrap_two_lines(f"FALSE -- {state.factoid_correction}", tw)
        draw_marquee(matrix_surface, "quiz_correction1", line1, (tx, ty, tw, LINE_H))
        draw_marquee(matrix_surface, "quiz_correction2", line2, (tx, ty + LINE_H, tw, LINE_H))
    elif not locked:
        # Question still live: two slightly-shortened text rows (7px each,
        # matching GLYPH_HEIGHT so no legibility is lost) free up a 2px
        # strip at the bottom of the top banner for the 30s countdown bar.
        row_h = LINE_H - 1
        line1, line2 = _quiz_banner_lines(tw)
        no_scroll = _is_price_question()
        draw_marquee(matrix_surface, "quiz_q1", line1, (tx, ty, tw, row_h), scroll=not no_scroll)
        draw_marquee(matrix_surface, "quiz_q2", line2, (tx, ty + row_h, tw, row_h), scroll=not no_scroll)
        _draw_countdown_bar((tx, ty + row_h * 2, tw, th - row_h * 2))
    else:
        line1, line2 = _quiz_banner_lines(tw)
        no_scroll = _is_price_question()
        draw_marquee(matrix_surface, "quiz_q1", line1, (tx, ty, tw, LINE_H), scroll=not no_scroll)
        draw_marquee(matrix_surface, "quiz_q2", line2, (tx, ty + LINE_H, tw, LINE_H), scroll=not no_scroll)

    if locked and state.round_timed_out:
        # Distractors dropped -- only the correct answer is revealed.
        reveal_choices = _price_fit_choices(choices)
        for i, pid in enumerate((3, 4, 5, 6)):
            rect = PANELS[pid]
            key = f"quiz_choice_{i}"
            if i == correct and i < len(choices):
                draw_marquee(matrix_surface, key, reveal_choices[i], rect, color=RED_DIM, invert=True,
                            scroll=not _is_price_question())
            else:
                draw_marquee(matrix_surface, key, "", rect)
    elif locked and state.quiz_players and state.round_winner_initials:
        # Per-round winner display (2026-08-12 redesign): panel 3 is a
        # "Wins:" label; the winning initials are distributed round-robin
        # across the other 3 panels (4, 5, 6) instead of repeating the
        # exact same text on all 4 -- each panel's own draw_marquee call
        # already auto-scrolls on its own if its assigned names don't fit,
        # satisfying "scroll if overflow" for free.
        draw_marquee(matrix_surface, "quiz_wins_label", "Wins:", PANELS[3], align="center")
        distribute_pids = (4, 5, 6)
        groups = {pid: [] for pid in distribute_pids}
        for i, initials in enumerate(state.round_winner_initials):
            groups[distribute_pids[i % len(distribute_pids)]].append(initials)
        for pid in distribute_pids:
            text = ",".join(groups[pid]) if groups[pid] else "--"
            draw_marquee(matrix_surface, f"quiz_wins_{pid}", text, PANELS[pid], align="center")
    else:
        _draw_answer_choice_panels(choices, correct, sel, locked, t)


def _is_price_question():
    return state.factoid_category == "price_game" and bool(state.price_game_item)


def _price_fit_choices(choices):
    """Price Game only: the four price panels share ONE presentation --
    either every one shows its "$" or none of them do (2026-08-12 rewrite,
    now symmetric in both directions).

    Normalizes every choice down to its bare digits first (regardless of
    whether the stored string already had a "$" -- the AI-fetch path
    doesn't always add one even when asked, which used to just show up on
    the board as a dollar-sign-less price with plenty of room to spare, e.g.
    "8"/"10"/"12"/"18" instead of "$8"/"$10"/"$12"/"$18"), then adds "$"
    back to ALL FOUR only if every one of them still fits the 32px panel
    with it on. If even one wouldn't fit with a "$" (e.g. a genuine 5-digit
    price is 33px wide and would hard-truncate a digit, as reported live on
    a 2011 new-car question), all four stay bare instead -- these are read
    as a set, so one panel reading "$298" next to another reading "29850"
    is worse than four clean, consistently-formatted numbers either way.

    Render-time only, deliberately: state.factoid_choices keeps whatever
    the source (CSV bank/AI fetch) actually produced, so phone clients
    (web/remote_server.py's player_question, which has room to spare) are
    unaffected."""
    if not _is_price_question() or not choices:
        return choices
    box_width = PANELS[3][2]
    bare = [c[1:] if c.startswith("$") else c for c in choices]
    with_dollar = [f"${b}" for b in bare]
    if all(text_width(c) <= box_width for c in with_dollar):
        return with_dollar
    return bare


def _quiz_banner_lines(box_width):
    """The two banner rows above the answer panels.

    Price Game gets a purpose-built layout -- the abbreviated item on line 1
    ("Doz. Eggs") and the when-line on line 2 ("in 1984?") -- instead of
    word-wrapping the full question sentence. "How much was a dozen eggs in
    1984?" simply doesn't fit 64px in two rows, so it used to scroll, and a
    scrolling prompt is hard to read against a 30s clock. Every other
    question type keeps the normal wrap."""
    if _is_price_question():
        return state.price_game_item, state.price_game_when

    question_text = state.factoid_question
    if state.quiz_is_test:
        question_text = "[test] " + question_text
    return wrap_two_lines(question_text, box_width)


def _draw_answer_choice_panels(choices, correct, sel, locked, t):
    """Draws the 4 answer-choice boxes on panels 3-6: dim-selected /
    bright-locked / winner-flash visuals. Factored out of _render_quiz_mode
    so the Mystery Band teaser-answering window (panels 3-6 flip to answer
    options the instant someone answers, Beta-Fix Feature Set item 2) can
    reuse the exact same rendering rather than duplicating it."""
    is_correct_grade = locked and sel == correct
    # Price Game answers are price strings that need to be compared against
    # each other at a glance -- a scrolling one is both hard to read and
    # hard to compare, so these truncate instead. _price_fit_choices() is
    # what keeps that truncation from ever eating a digit: it drops the
    # "$" from all four first, which is always enough to fit.
    scroll = not _is_price_question()
    choices = _price_fit_choices(choices)
    for i, pid in enumerate((3, 4, 5, 6)):
        rect = PANELS[pid]
        key = f"quiz_choice_{i}"

        if i >= len(choices):
            draw_marquee(matrix_surface, key, "----", rect, align="center")
            continue

        text = choices[i]

        if locked and state.quiz_players:
            # Multiplayer: no single operator selection to highlight (each
            # player picked independently), and only the correct answer
            # gets called out (2026-08-09) -- the other three render plain,
            # same as the live/ungraded look, rather than a dim "reveal"
            # that implied something about them specifically.
            if i == correct:
                _draw_winning_flash(rect, key, text, t, scroll=scroll)
            else:
                draw_marquee(matrix_surface, key, text, rect, scroll=scroll)
            continue

        if locked and is_correct_grade and i == sel:
            # The winning box: brightly pulsing red, ding + pulsing DMX
            # already fired by grade_quiz_selection() -> trigger_big_win().
            # The pulse (not a color change) is what makes it obvious.
            _draw_winning_flash(rect, key, text, t, scroll=scroll)
        elif locked and is_correct_grade:
            # The other three boxes celebrate the win, dim and static so
            # the pulsing winner still stands out.
            draw_marquee(matrix_surface, key, "winner!", rect, color=RED_DIM, invert=True, align="center")
        elif locked and i == sel:
            # Wrong answer selected -- steady bright red (not pulsing,
            # to stay visually distinct from the winning flash).
            draw_marquee(matrix_surface, key, text, rect, color=RED_FULL, invert=True, scroll=scroll)
        elif locked and i == correct:
            # Reveal the correct answer dimly so the room learns it.
            draw_marquee(matrix_surface, key, text, rect, color=RED_DIM, invert=True, scroll=scroll)
        elif not locked and i == sel:
            _draw_selected_panel(rect, key, text, scroll=scroll)
        elif not locked and i == correct and state.quiz_is_test:
            # Demo mode: no real quiz is loaded, so it's fine (and
            # helpful for demoing the flow) to hint the winning square.
            _draw_demo_winner_hint(rect, key, text, t, scroll=scroll)
        else:
            draw_marquee(matrix_surface, key, text, rect, scroll=scroll)


def _si_gap_split(x, y, w, h):
    """Splits one Space Invaders sprite rect into the pieces actually drawn,
    compensating for the row-2/3 physical panel-seam gap (SI_ROW23_GAP_PX,
    config.py) -- see that constant's comment for the full why. Row 1
    (y < PANEL_H) and rects that don't cross the x=PANEL_W seam pass
    through unchanged as a single piece. A rect is classified by its top
    edge only (sprites here are all a few px tall, never straddle a row
    boundary in practice) -- fine for a cosmetic Easter-egg effect."""
    if SI_ROW23_GAP_PX <= 0 or y < PANEL_H or w <= 0:
        return [(x, y, w, h)]

    seam = PANEL_W
    pieces = []
    left_w = max(0, min(x + w, seam) - x)
    if left_w > 0:
        pieces.append((x, y, left_w, h))
    right_x = max(x, seam)
    right_w = (x + w) - right_x
    if right_w > 0:
        pieces.append((right_x - SI_ROW23_GAP_PX, y, right_w, h))
    return pieces


def _si_draw_rect(color, rect, width=0):
    x, y, w, h = rect
    for px, py, pw, ph in _si_gap_split(x, y, w, h):
        pygame.draw.rect(matrix_surface, color, (px, py, pw, ph), width)


def _render_space_invaders(t):
    """Space Invaders mini-game (DJ-mode Btn1+Btn3 combo Easter egg): draws
    the cannon, invader formation, in-flight bullets, and brief explosion
    flashes straight from drivers/space_invaders_engine.py's state.si_*
    fields -- the engine owns all simulation, this only paints the current
    snapshot. Spans the full matrix canvas rather than a single panel (see
    config.py's SPACE INVADERS section for why). Every draw goes through
    _si_draw_rect() rather than pygame.draw.rect() directly so the row-2/3
    panel-seam gap compensation applies uniformly."""
    player_top_y = MATRIX_HEIGHT - SI_PLAYER_Y_FROM_BOTTOM - SI_PLAYER_HEIGHT
    _si_draw_rect(RED_FULL, (int(round(state.si_player_x)), player_top_y, SI_PLAYER_WIDTH, SI_PLAYER_HEIGHT))

    for inv in state.si_invaders:
        if not inv["alive"]:
            continue
        _si_draw_rect(RED_FULL, (int(round(inv["x"])), int(round(inv["y"])), SI_INVADER_SIZE, SI_INVADER_SIZE))

    for b in state.si_bullets:
        _si_draw_rect(RED_FULL, (int(round(b["x"])), int(round(b["y"])), 1, 2))

    for e in state.si_explosions:
        _si_draw_rect(
            RED_DIM,
            (int(round(e["x"])) - 1, int(round(e["y"])) - 1, SI_INVADER_SIZE + 2, SI_INVADER_SIZE + 2),
            1,
        )


def _render_westminster(t):
    """Westminster "Bat Clock" top-of-hour event (Feature Update): full-canvas
    takeover, highest render priority (checked before Space Invaders/Price
    Game/DJ/Quiz in update_matrix_canvas() below). Kept abstract/simple per
    the feature spec -- a full-red flash, then a lightweight particle
    scatter (drivers/westminster_engine.py owns advancing the particle
    positions each frame; this only draws the current snapshot, same
    "engine owns state, renderer owns pixels" split _render_space_invaders
    already uses), then a plain digital time readout spanning the whole
    matrix rather than a literal analog clock face (the rig is a 64x56
    red-only LED matrix -- not enough resolution for a legible clock face)."""
    phase = state.westminster_phase
    if phase == "flash":
        matrix_surface.fill(RED_FULL)
    elif phase == "particles":
        for p in state.westminster_particles:
            x, y = int(round(p["x"])), int(round(p["y"]))
            if 0 <= x < MATRIX_WIDTH and 0 <= y < MATRIX_HEIGHT:
                matrix_surface.set_at((x, y), RED_FULL)
    elif phase == "readout":
        time_str = time.strftime("%H:%M:%S")
        draw_marquee(matrix_surface, "westminster_readout", time_str,
                     (0, 0, MATRIX_WIDTH, MATRIX_HEIGHT), align="center")


# ------------------------------------------------------------
# Song-transition board wipe (2026-08-09): triggered by
# drivers/deck_orchestrator.py the instant a track transition fires.
# Panels 1+2 (top row) slide their content up and off; 3+5 (left column)
# slide left; 4+6 (right column) slide right -- each panel already sits on
# the matrix edge its assigned direction exits through, so nothing bleeds
# into a neighboring panel, it just exits the canvas cleanly. Boards then
# hold black for the rest of `hold_seconds` (the crossfade's own duration
# plus the requested 2s) before normal rendering resumes.
# ------------------------------------------------------------
_SLIDE_DIRECTIONS = {1: "up", 2: "up", 3: "left", 4: "right", 5: "left", 6: "right"}
_SLIDE_OUT_SECONDS = 0.6
_board_wipe_started_at = 0.0
_board_wipe_hold_seconds = 0.0
_board_wipe_snapshots = {}


def trigger_board_wipe(hold_seconds):
    """Snapshots the current content of all 6 panels and starts the wipe.
    Call this BEFORE matrix_surface gets overwritten for the new track --
    drivers/deck_orchestrator.py calls it from the same per-frame update()
    pass that fires the audio transition, which runs before
    update_matrix_canvas() this frame, so the snapshot still holds
    whatever was on screen the instant before the transition."""
    global _board_wipe_started_at, _board_wipe_hold_seconds, _board_wipe_snapshots
    _board_wipe_snapshots = {pid: matrix_surface.subsurface(rect).copy() for pid, rect in PANELS.items()}
    _board_wipe_started_at = time.time()
    _board_wipe_hold_seconds = hold_seconds


def _render_board_wipe(t):
    """Returns True if the wipe is still overriding this frame's rendering
    (caller should skip its own mode dispatch entirely when this is True)."""
    global _board_wipe_started_at
    if _board_wipe_started_at == 0.0:
        return False
    elapsed = t - _board_wipe_started_at
    if elapsed >= _board_wipe_hold_seconds:
        _board_wipe_started_at = 0.0
        return False

    for pid, rect in PANELS.items():
        x0, y0, w, h = rect
        pygame.draw.rect(matrix_surface, BLACK, rect)
        if elapsed >= _SLIDE_OUT_SECONDS:
            continue  # fully exited -- just held black for the rest of the hold
        snap = _board_wipe_snapshots.get(pid)
        if snap is None:
            continue
        phase = elapsed / _SLIDE_OUT_SECONDS
        direction = _SLIDE_DIRECTIONS[pid]
        if direction == "up":
            matrix_surface.blit(snap, (x0, y0 - int(phase * h)))
        elif direction == "left":
            matrix_surface.blit(snap, (x0 - int(phase * w), y0))
        elif direction == "right":
            matrix_surface.blit(snap, (x0 + int(phase * w), y0))

    # Announcement tag-phrase banner (2026-08-10 fix): update_matrix_canvas()
    # skips its ENTIRE mode dispatch (including _render_dj_mode(), where
    # this banner used to live) for as long as this wipe is active -- so
    # the banner code there was simply never being reached during exactly
    # the dark window it was meant to fill. Drawn directly here instead,
    # after the black fill above, so it actually shows up in that space.
    if state.announcement_banner_from <= t < state.announcement_banner_until:
        banner_text = (state.announcement_banner_text_override
                       or get_announcement_text_for(state.last_announcement_filename)
                       or "[NO ANNOUNCEMENT TEXT SET]")
        draw_marquee(matrix_surface, "announcement_banner", banner_text, TOP_COMBINED, align="center")

    return True


# ------------------------------------------------------------
# TRIVIA NIGHT SHOW FLOW (2026-08-13): Setup / Countdown / scripted-open /
# scripted-close full-matrix takeovers. See drivers/show_engine.py for the
# phase machine this reads; "live" phase isn't handled here at all -- it
# falls straight through to the normal dispatch below, unmodified.
# ------------------------------------------------------------
_SHOW_INTRO_SLIDE_SECONDS = 0.4


def _draw_show_headline(text, x_shift=0):
    """Draws one line of (unscrolled) headline text across TOP_COMBINED,
    centered plus an extra x_shift pixels -- the shift is how the intro's
    slide-in/out effect works (no generic slide primitive exists elsewhere
    in this codebase, so this is purpose-built here, same as
    _render_board_wipe's own slide math just above)."""
    x0, y0, w, h = TOP_COMBINED
    tw = text_width(text)
    center_x = x0 + max(0, (w - tw) // 2)
    draw_y = y0 + max(0, (h - GLYPH_HEIGHT) // 2)
    draw_bitmap_text(matrix_surface, text, center_x + x_shift, draw_y, clip_rect=TOP_COMBINED)


def _slide_in_shift(progress):
    """progress 0..1 -- starts fully off the right edge, eases to 0."""
    progress = max(0.0, min(1.0, progress))
    return int(TOP_COMBINED[2] * (1.0 - progress))


def _slide_out_shift(progress):
    """progress 0..1 -- starts at 0, eases off the left edge."""
    progress = max(0.0, min(1.0, progress))
    return int(-TOP_COMBINED[2] * progress)


def _show_intro_beat_window(elapsed):
    """Which config.SHOW_INTRO_BEATS entry (if any) is active at `elapsed`
    seconds into the intro. Returns (beat_index, local_elapsed, window_len)
    or None before the first beat's own timestamp (screen stays blank).
    The LAST beat's window never ends (float('inf')) -- the intro has no
    fixed end time anymore, so its final line (config.SHOW_INTRO_BEATS[-1])
    just holds until drivers/show_engine.py::skip_intro() ends the phase."""
    beats = config.SHOW_INTRO_BEATS
    for i, (start, _text) in enumerate(beats):
        window_end = beats[i + 1][0] if i + 1 < len(beats) else float("inf")
        if start <= elapsed < window_end:
            return i, elapsed - start, window_end - start
    return None


def _render_show_intro(t):
    """ShowStart.mp3 choreography (config.SHOW_INTRO_BEATS): each line
    slides in, holds, then slides out as the next line's timestamp arrives
    -- except the DJ-name reveal beat (config.SHOW_INTRO_FLASH_BEAT_INDEX),
    which slides in and then flashes instead of holding/sliding out, and
    the LAST beat, which just holds forever (see _show_intro_beat_window).
    The intro ends only via drivers/show_engine.py::skip_intro() (Joy X-,
    inputs/gamepad.py) -- there's no fixed end timestamp to cut to blank at
    anymore, so the live host's own spoken intro sets the pace."""
    elapsed = t - state.show_phase_started_at
    window = _show_intro_beat_window(elapsed)
    if window is None:
        return
    beat_index, local_elapsed, window_len = window
    text = config.SHOW_INTRO_BEATS[beat_index][1]

    # Most of these lines are wider than the 64px combined panel (a
    # centered static draw would crop BOTH ends, showing only whatever
    # middle fragment happens to land in frame -- worse than either a full
    # truncation or a scroll). The slide-in/out flourish uses the custom
    # x-shift math above; the held middle uses draw_marquee's existing
    # auto-scroll-if-it-doesn't-fit behavior instead, same tool every other
    # long headline in this app already reads through.
    key = f"show_intro_beat_{beat_index}"

    if beat_index == config.SHOW_INTRO_FLASH_BEAT_INDEX:
        if local_elapsed < _SHOW_INTRO_SLIDE_SECONDS:
            _draw_show_headline(text, _slide_in_shift(local_elapsed / _SHOW_INTRO_SLIDE_SECONDS))
        else:
            flash_phase = (local_elapsed - _SHOW_INTRO_SLIDE_SECONDS) / config.SHOW_INTRO_FLASH_PERIOD_SECONDS
            if int(flash_phase) % 2 == 0:
                draw_marquee(matrix_surface, key, text, TOP_COMBINED, align="center")
        return

    if local_elapsed < _SHOW_INTRO_SLIDE_SECONDS:
        _draw_show_headline(text, _slide_in_shift(local_elapsed / _SHOW_INTRO_SLIDE_SECONDS))
    elif local_elapsed >= window_len - _SHOW_INTRO_SLIDE_SECONDS:
        out_progress = (local_elapsed - (window_len - _SHOW_INTRO_SLIDE_SECONDS)) / _SHOW_INTRO_SLIDE_SECONDS
        _draw_show_headline(text, _slide_out_shift(out_progress))
    else:
        draw_marquee(matrix_surface, key, text, TOP_COMBINED, align="center")


def _render_show_outro(t):
    """ShowEnd.mp3 choreography: the champion (or thanks-for-playing)
    banner for as long as the song's playing, then an indefinite hold on
    the contact-info card -- both keyed off the same
    state.show_outro_music_ends_at drivers/lighting_engine.py's DMX side
    reads too, so LED and DMX can never drift out of sync with each other."""
    if t < state.show_outro_music_ends_at:
        draw_marquee(matrix_surface, "show_outro_banner", state.show_outro_message,
                     TOP_COMBINED, align="center")
    else:
        draw_marquee(matrix_surface, "show_outro_contact", config.SHOW_END_LED_CONTACT_TEXT,
                     TOP_COMBINED, align="center")


def _draw_setup_status_chips():
    """Four at-a-glance connectivity chips, one per remaining physical
    panel (3-6), while the operator is standing at the rig itself setting
    up -- the same signals graphics/overlay_panel.py's desktop overlay and
    the web Setup page's status row already show, just small enough to fit
    the matrix. scroll=False (truncate, not marquee) since these are meant
    to be read all at once, not waited on."""
    transport = led_bridge.current_transport()
    led_label = "USB" if transport.startswith("SERIAL") else "UDP"
    chips = (
        (3, led_label),
        (4, "DMX:Y" if dmx.active else "DMX:N"),
        (5, "TUN:Y" if tunnel_engine.get_current_tunnel_url() else "TUN:N"),
        (6, "NET:Y" if tunnel_engine.internet_reachable() else "NET:N"),
    )
    for panel_id, label in chips:
        draw_marquee(matrix_surface, f"setup_status_{panel_id}", label,
                     PANELS[panel_id], align="center", scroll=False)


def _render_show_phase(t):
    """Setup/Countdown share a static held screen (nothing dramatic should
    happen on the physical rig just because the operator is filling out
    the Setup form or waiting on a scheduled start) -- Intro/Outro run
    their scripted choreography above."""
    phase = state.show_phase
    if phase in ("setup", "countdown"):
        draw_marquee(matrix_surface, "show_setup_banner", config.SHOW_SETUP_LED_TEXT,
                     TOP_COMBINED, align="center")
        _draw_setup_status_chips()
    elif phase == "intro":
        _render_show_intro(t)
    elif phase == "outro":
        _render_show_outro(t)


def update_matrix_canvas():
    matrix_surface.fill(BLACK)
    t = time.time()

    if state.show_phase != "live":
        # Highest-priority override, ahead of even the board-wipe -- Setup/
        # Countdown/Intro/Outro take over the matrix completely. "live"
        # falls straight through to the normal dispatch below, unchanged.
        _render_show_phase(t)
        return

    if _render_board_wipe(t):
        return

    if state.win_sequence_active:
        # A win was just graded (drivers/win_sequence_engine.py is now
        # ducking/playing applause/restoring in the background) -- clear
        # whatever question or answer-reveal was on screen immediately
        # rather than let it run out its normal hold-then-advance cycle,
        # which could otherwise even pull up a BRAND NEW question from the
        # extended queue mid-applause (_render_quiz_stats_or_return's
        # advance_to_next_queued_question() call). This also covers the
        # mystery-teaser-answered-while-still-MODE_DJ case (_render_dj_mode's
        # mystery_live branch), which is otherwise completely unaware of
        # win_sequence_active. win_sequence_engine flips state.mode back to
        # MODE_DJ once intermission starts, so normal DJ-mode rendering (or
        # the intermission countdown) resumes on its own right after.
        state.factoid_choices = []
        state.factoid_question = ""
        state.round_deadline_at = 0.0
        _render_win_celebration(t)
        return

    if state.westminster_active:
        _render_westminster(t)
    elif state.mode == state.MODE_SPACE_INVADERS:
        _render_space_invaders(t)
    elif state.price_game_active and state.price_game_phase == "banner":
        _render_price_banner(t)
    elif state.mode == state.MODE_DJ:
        _render_dj_mode(t)
    else:
        _render_quiz_mode(t)


def render_led_grid():
    # Physical rig: matrix_surface is fully drawn by this point (called
    # right after update_matrix_canvas() in main.py's loop) -- fire it at
    # the real panels over UDP. Everything below this line is only the
    # on-screen simulator window.
    led_bridge.send_frame(matrix_surface)

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

    overlay_panel.render(screen, time.time())

    pygame.display.flip()

    # Secondary HDMI fullscreen fallback canvas (Feature Update): an
    # independent second pygame window mirroring the same matrix_surface
    # raster this function just drew to the primary simulator, scaled up
    # for an external monitor/projector. No-op if it was never opened
    # (config.SECONDARY_CANVAS_ENABLED False, or only one monitor detected
    # and config.SECONDARY_CANVAS_SHOW_ON_SINGLE_MONITOR is False).
    secondary_canvas.render(matrix_surface)
