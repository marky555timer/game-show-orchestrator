import time
import pygame
from config import RED_FULL, BLACK, FONT_5X7, SCROLL_SPEED_PX_PER_SEC, SCROLL_PAUSE_SECONDS

GLYPH_GAP = 1        # 1px gap between adjacent glyphs
GLYPH_HEIGHT = 7
TRAIL_GAP = 6         # blank runway added after the last char before a marquee loops

# Per-key marquee timers, e.g. {"panel3_answer": {"text":..., "box_width":..., "start":...}}
_scroll_state = {}


def _glyph_for(char):
    """Bitmap font is case-sensitive (distinct upper/lower glyphs, since
    lowercase is deliberately narrower to save pixels). Falls back to the
    other case, then to a blank space, for characters we don't have art for."""
    return FONT_5X7.get(char) or FONT_5X7.get(char.upper()) or FONT_5X7.get(char.lower()) or FONT_5X7[' ']


def _glyph_width(glyph):
    return len(glyph[0]) if glyph else 5


def char_width(char):
    return _glyph_width(_glyph_for(char))


def text_width(text):
    text = str(text)
    if not text:
        return 0
    total = 0
    for char in text:
        total += char_width(char) + GLYPH_GAP
    return total - GLYPH_GAP


def draw_bitmap_text(surface, text, x, y, color=RED_FULL, invert=False, clip_rect=None):
    """Blits variable-width bitmap glyphs at (x, y), preserving the text's
    original case (lowercase glyphs are narrower, condensing pixel usage).
    If clip_rect (x0, y0, w, h) is given, pixels outside it are skipped --
    lets callers confine text to one panel."""
    curr_x = x
    text = str(text)

    surf_w, surf_h = surface.get_width(), surface.get_height()
    if clip_rect:
        cx0, cy0, cw, ch = clip_rect
        cx1, cy1 = cx0 + cw, cy0 + ch
    else:
        cx0, cy0, cx1, cy1 = 0, 0, surf_w, surf_h

    for char in text:
        glyph = _glyph_for(char)
        for row_idx, row in enumerate(glyph):
            for col_idx, pixel in enumerate(row):
                if pixel == '1':
                    px = curr_x + col_idx
                    py = y + row_idx
                    if cx0 <= px < cx1 and cy0 <= py < cy1 and 0 <= px < surf_w and 0 <= py < surf_h:
                        surface.set_at((px, py), BLACK if invert else color)
        curr_x += _glyph_width(glyph) + GLYPH_GAP


def get_scroll_offset(key, text, box_width, speed=None, pause=None):
    """Pause -> scroll-left -> pause -> loop marquee timer, keyed per-caller.
    Returns 0 if the text already fits in box_width (no scrolling needed)."""
    speed = speed or SCROLL_SPEED_PX_PER_SEC
    pause = SCROLL_PAUSE_SECONDS if pause is None else pause

    tw = text_width(text)
    if tw <= box_width:
        _scroll_state.pop(key, None)
        return 0

    now = time.time()
    entry = _scroll_state.get(key)
    if entry is None or entry["text"] != text or entry["box_width"] != box_width:
        entry = {"text": text, "box_width": box_width, "start": now}
        _scroll_state[key] = entry

    scroll_distance = tw - box_width + TRAIL_GAP
    scroll_time = scroll_distance / speed
    cycle = pause * 2 + scroll_time
    phase = (now - entry["start"]) % cycle

    if phase < pause:
        return 0
    elif phase < pause + scroll_time:
        return (phase - pause) * speed
    else:
        return scroll_distance


def draw_marquee(surface, key, text, rect, color=RED_FULL, invert=False, align="left"):
    """Draws one line of text clipped/scrolled to fit `rect` = (x, y, w, h).
    Static + optionally centered if it already fits; scrolls forever if not."""
    x0, y0, w, h = rect
    text = str(text)

    if invert:
        pygame.draw.rect(surface, color, (x0, y0, w, h))

    draw_y = y0 + max(0, (h - GLYPH_HEIGHT) // 2)
    tw = text_width(text)

    if tw <= w:
        draw_x = x0 + max(0, (w - tw) // 2) if align == "center" else x0
        draw_bitmap_text(surface, text, draw_x, draw_y, color=color, invert=invert, clip_rect=rect)
        return

    offset = get_scroll_offset(key, text, w)
    draw_bitmap_text(surface, text, x0 - offset, draw_y, color=color, invert=invert, clip_rect=rect)


def wrap_two_lines(text, box_width):
    """Greedy word-wrap into up to 2 lines. Line 1 fills as much as fits;
    any remaining words go entirely onto line 2 (which may still need to
    scroll on its own -- callers should marquee both lines independently)."""
    words = str(text).split()
    if not words:
        return "", ""

    line1 = ""
    idx = 0
    for i, word in enumerate(words):
        candidate = (line1 + " " + word).strip()
        if text_width(candidate) <= box_width or not line1:
            line1 = candidate
            idx = i + 1
        else:
            break

    line2 = " ".join(words[idx:])
    return line1, line2
