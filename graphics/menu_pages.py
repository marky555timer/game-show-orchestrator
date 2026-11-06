"""graphics/menu_pages.py
Draws the panel menu (drivers/panel_menu.py::view()) onto the matrix:
the page on panels 1+2 (two 8px lines), and on panels 3-6 only what each
arcade button does, as a word, in the same 2x2 layout as the buttons
(3 = green, 4 = red, 5 = yellow, 6 = blue). A button with nothing to do on a
page leaves its panel black.
"""
import time

import config
from config import PANELS, TOP_COMBINED, RED_FULL
from graphics.text_render import draw_marquee, GLYPH_HEIGHT

_BUTTON_PANELS = {"green": 3, "red": 4, "yellow": 5, "blue": 6}


def draw(surface):
    from drivers import panel_menu
    v = panel_menu.view()
    surface.fill((0, 0, 0))
    if v["blink"]:
        period = config.MENU_BLINK_PERIOD_SECONDS
        if (time.monotonic() % period) >= period / 2.0:
            return
    x0, y0, w, h = TOP_COMBINED
    top, bottom = v["lines"]
    from state import state
    if time.time() < state.gamepad_connect_feedback_until:
        # recovery button's gamepad reconnect (drivers/recovery_button.py)
        top, bottom = "GAMEPAD", state.gamepad_connect_feedback_text
    half = h // 2
    draw_marquee(surface, "menu_line1", top, (x0, y0, w, half), color=RED_FULL, align="center")
    draw_marquee(surface, "menu_line2", bottom, (x0, y0 + half, w, h - half), color=RED_FULL, align="center")

    # The real panels are 1-bit (lit = any red, drivers/led_bridge.py), so no
    # tinted backgrounds or button colours: just the word, lit on black.
    cells = v.get("cells")
    for color, panel in _BUTTON_PANELS.items():
        if cells:
            # two 8px lines per panel: label over value (diagnostics page)
            label, value = cells.get(color, ("", ""))
            px, py, pw, ph = PANELS[panel]
            if value:
                draw_marquee(surface, f"menu_cell_{color}_a", label, (px, py, pw, ph // 2), color=RED_FULL, align="center")
                draw_marquee(surface, f"menu_cell_{color}_b", value, (px, py + ph // 2, pw, ph - ph // 2), color=RED_FULL, align="center")
            elif label:
                draw_marquee(surface, f"menu_btn_{color}", label, PANELS[panel], color=RED_FULL, align="center")
            continue
        label = v["buttons"].get(color, "")
        if label:
            draw_marquee(surface, f"menu_btn_{color}", label, PANELS[panel], color=RED_FULL, align="center")
