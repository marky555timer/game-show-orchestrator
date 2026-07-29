import time
import pygame
from config import (
    MATRIX_WIDTH, MATRIX_HEIGHT, PIXEL_SCALE, GAP, WINDOW_W, WINDOW_H,
    RED_FULL, RED_DIM, RED_OFF, BLACK, PANELS, TOP_COMBINED,
    TOP_CYCLE_TRACK_SECONDS, TOP_CYCLE_FACTOID_SECONDS,
    STATUS_PANEL_HOLD_SECONDS, TOP_SHOW_FACTOID_PAGE,
)
from state import state
from drivers.rekordbox_driver import get_rekordbox_track
from drivers.factoid_engine import request_factoid, build_mock_question
from graphics.text_render import draw_marquee, wrap_two_lines
from graphics.animations import (
    render_panel_animation, deal_panel_animations,
    anim_dancing_cat, anim_star_burst,
)

# Initialize Pygame Display Engine
screen = pygame.display.set_mode((WINDOW_W, WINDOW_H))
pygame.display.set_caption("6-Panel Game Show Matrix Simulator")

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

    confident = state.deck1_confident if state.active_deck == 1 else state.deck2_confident
    request_factoid(song_title, artist_name, confident)
    has_factoid = bool(state.factoid_headline)

    track_key = f"{song_title}|{artist_name}"
    if track_key != _top_cycle_track_key:
        _top_cycle_track_key = track_key
        _top_cycle_start = t
        # New track -> deal a fresh hand of animations across panels 3-6.
        deal_panel_animations()

    # Panels 1+2 are driven as one logical 64x16 surface, two 8px lines tall,
    # so artist and title each get the full double-panel width instead of
    # being crammed into 32px.
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

    for pid in (3, 4, 5, 6):
        rect = PANELS[pid]
        if pid == 6 and t < state.vol_overlay_until:
            _draw_volume_overlay(rect)
        elif pid == 3 and kind and t < _status_until:
            _render_status_panel(rect, t, kind)
        else:
            render_panel_animation(matrix_surface, pid, rect, t)


# Statuses that just mean "nothing to report yet" -- not an AI failure, so
# panel 3 stays on its dealt animation rather than showing the dancing-cat
# failure animation.
_AI_BENIGN_STATUSES = ("", "NO CONFIDENT TRACK ID", "FETCHING FACTOID...")


def _status_kind():
    """Which AI-pipeline indicator panel 3 owes the room right now:
    "fail" if the factoid/question request failed or is unavailable, "ok" once
    a real AI-sourced question has loaded for the current track, or None when
    there's nothing to report (panel 3 then just animates)."""
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
    drivers/factoid_engine.py)."""
    old_clip = matrix_surface.get_clip()
    matrix_surface.set_clip(pygame.Rect(rect))
    try:
        if kind == "fail":
            anim_dancing_cat(matrix_surface, rect, t)
        else:
            anim_star_burst(matrix_surface, rect, t)
    finally:
        matrix_surface.set_clip(old_clip)


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


def _render_quiz_mode(t):
    # Quiz mode has to drive the factoid/question pipeline too. Previously
    # only _render_dj_mode called request_factoid(), so a host who booted
    # straight into GAME mode -- or who stayed in GAME mode across a track
    # change -- never triggered an AI request and was stuck on the local TEST
    # placeholder question forever, no matter how good the API key was.
    # ...but only between rounds. request_factoid() resets the question and the
    # selection whenever the active track changes, and the crossfader watch is
    # deliberately hyper-sensitive (any pixel change at the fader flips
    # state.active_deck -- see rekordbox_driver), so a nudged fader used to swap
    # the question out from under the host mid-round. While an answer is armed
    # or graded the content is frozen; Btn6 / 'C' ends the round and the next
    # track's question loads on the following frame.
    round_in_progress = state.quiz_locked or state.quiz_selected_index >= 0
    if not round_in_progress:
        song_title, artist_name = _current_track()
        confident = state.deck1_confident if state.active_deck == 1 else state.deck2_confident
        request_factoid(song_title, artist_name, confident)

    _ensure_quiz_content()

    tx, ty, tw, th = TOP_COMBINED
    question_text = state.factoid_question
    if state.quiz_is_test:
        question_text = "[test] " + question_text
    line1, line2 = wrap_two_lines(question_text, tw)
    draw_marquee(matrix_surface, "quiz_q1", line1, (tx, ty, tw, LINE_H))
    draw_marquee(matrix_surface, "quiz_q2", line2, (tx, ty + LINE_H, tw, LINE_H))

    choices = state.factoid_choices
    sel = state.quiz_selected_index
    correct = state.factoid_correct_index
    locked = state.quiz_locked
    is_correct_grade = locked and sel == correct

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


def update_matrix_canvas():
    matrix_surface.fill(BLACK)
    t = time.time()

    if state.mode == state.MODE_DJ:
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
