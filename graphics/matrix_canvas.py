import time
import pygame
from config import (
    MATRIX_WIDTH, MATRIX_HEIGHT, PIXEL_SCALE, GAP, WINDOW_W, WINDOW_H,
    RED_FULL, RED_DIM, RED_OFF, BLACK, GREEN_FULL, GREEN_DIM,
    SELECT_OUTLINE_DIM, PANELS, TOP_COMBINED,
    TOP_CYCLE_TRACK_SECONDS, TOP_CYCLE_FACTOID_SECONDS,
)
from state import state
from drivers.rekordbox_driver import get_rekordbox_track
from drivers.factoid_engine import request_factoid, build_mock_question
from graphics.text_render import draw_marquee, wrap_two_lines
from graphics.animations import render_panel_animation

# Initialize Pygame Display Engine
screen = pygame.display.set_mode((WINDOW_W, WINDOW_H))
pygame.display.set_caption("6-Panel Game Show Matrix Simulator")

matrix_surface = pygame.Surface((MATRIX_WIDTH, MATRIX_HEIGHT))

LINE_H = 8  # two 8px rows fit exactly in a 16px-tall panel

# DJ-mode top-display page cycling: page 0 = artist/title, page 1 = factoid.
_top_cycle_start = time.time()
_top_cycle_track_key = None


def _get_top_page(has_factoid):
    if not has_factoid:
        return 0
    elapsed = time.time() - _top_cycle_start
    cycle = TOP_CYCLE_TRACK_SECONDS + TOP_CYCLE_FACTOID_SECONDS
    phase = elapsed % cycle
    return 0 if phase < TOP_CYCLE_TRACK_SECONDS else 1


def _render_dj_mode(t):
    global _top_cycle_track_key, _top_cycle_start

    track_info = get_rekordbox_track()
    if isinstance(track_info, tuple):
        song_title, artist_name = track_info
    else:
        song_title, artist_name = str(track_info), ""

    confident = state.deck1_confident if state.active_deck == 1 else state.deck2_confident
    request_factoid(song_title, artist_name, confident)
    has_factoid = bool(state.factoid_headline)

    track_key = f"{song_title}|{artist_name}"
    if track_key != _top_cycle_track_key:
        _top_cycle_track_key = track_key
        _top_cycle_start = t

    tx, ty, tw, th = TOP_COMBINED
    line1_rect = (tx, ty, tw, LINE_H)
    line2_rect = (tx, ty + LINE_H, tw, LINE_H)

    page = _get_top_page(has_factoid)
    if page == 1:
        draw_marquee(matrix_surface, "dj_top_fact", state.factoid_headline, line1_rect)
        draw_marquee(matrix_surface, "dj_top_fact_sub", artist_name, line2_rect)
    elif artist_name:
        draw_marquee(matrix_surface, "dj_top_artist", artist_name, line1_rect)
        draw_marquee(matrix_surface, "dj_top_title", song_title, line2_rect)
    else:
        draw_marquee(matrix_surface, "dj_top_title_only", song_title, line1_rect)

    for pid in (3, 4, 5):
        render_panel_animation(matrix_surface, pid, PANELS[pid], t)

    if t < state.vol_overlay_until:
        _draw_volume_overlay(PANELS[6])
    else:
        render_panel_animation(matrix_surface, 6, PANELS[6], t)


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
    """Selected-but-ungraded answer: dim outline around the frame, bright
    green fill for the interior."""
    x0, y0, w, h = rect
    pygame.draw.rect(matrix_surface, SELECT_OUTLINE_DIM, (x0, y0, w, h), 1)
    inner = (x0 + 1, y0 + 1, w - 2, h - 2)
    draw_marquee(matrix_surface, key, text, inner, color=GREEN_FULL, invert=True)


def _draw_winning_flash(rect, key, text, t):
    """The graded-correct panel: brightly flashing green, pulsing at the
    same cadence as trigger_big_win()'s DMX pulse so the light and the
    panel read as one effect."""
    elapsed = t - state.quiz_graded_at
    cps = 4.0
    cycle_phase = (elapsed * cps) % 1.0
    saw = 1.0 - cycle_phase
    intensity = 0.35 + saw * 0.65
    color = tuple(int(c * intensity) for c in GREEN_FULL)
    draw_marquee(matrix_surface, key, text, rect, color=color, invert=True)


def _render_quiz_mode(t):
    _ensure_quiz_content()

    tx, ty, tw, th = TOP_COMBINED
    question_text = state.factoid_question
    if state.quiz_is_test:
        question_text = "[TEST] " + question_text
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
            # The winning box: brightly flashing green, ding + pulsing DMX
            # already fired by grade_quiz_selection() -> trigger_big_win().
            _draw_winning_flash(rect, key, text, t)
        elif locked and is_correct_grade:
            # The other three boxes celebrate the win.
            draw_marquee(matrix_surface, key, "WINNER!", rect, color=GREEN_FULL, invert=True, align="center")
        elif locked and i == sel:
            # Wrong answer selected -- flag it red.
            draw_marquee(matrix_surface, key, text, rect, color=RED_FULL, invert=True)
        elif locked and i == correct:
            # Reveal the correct answer dimly so the room learns it.
            draw_marquee(matrix_surface, key, text, rect, color=GREEN_DIM, invert=True)
        elif not locked and i == sel:
            _draw_selected_panel(rect, key, text)
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
