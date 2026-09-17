"""drivers/color_utils.py
Small shared color-math helpers. Originally lived only in wled_engine.py
(as a private `_hue_shift`) since the marquee was the only surface that
needed hue rotation for its complement-chase/bounce-comet patterns.
Relocated here 2026-09-17 so drivers/lighting_engine.py (DMX) and
drivers/accent_engine.py (outline) can reuse the exact same rotation math
for their own "adjacent"/"complementary" gradient modes instead of each
re-deriving it.
"""
import colorsys


def hue_shift(r, g, b, degrees):
    """Rotates an RGB color's hue by `degrees` (0-360), holding saturation
    and value fixed -- used for color-complement (180 deg) and adjacent-hue
    (small offsets) treatments, a much cleaner "opposite/neighbor color"
    than naive RGB math (255-r etc.) gives."""
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    h = (h + degrees / 360.0) % 1.0
    r2, g2, b2 = colorsys.hsv_to_rgb(h, s, v)
    return r2 * 255, g2 * 255, b2 * 255
