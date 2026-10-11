"""drivers/transition_shapes.py
Shared shape math for the song-change transitions (2026-10-05/06), so the
marquee (drivers/wled_engine.py, per bulb) and the matrix reveal
(graphics/matrix_canvas.py, per pixel block) sweep in exactly the same
order.

pos() says where a point sits along a style's sweep: 0 = first to change,
1 = last. Points are normalized board coordinates (x right, y DOWN, both
0..1); `rnd` is a per-point random 0..1, fixed for one transition, used by
the pixel dissolve.
"""

STYLES = ("wipe_lr", "center_out", "edges_in", "curtain", "diagonal",
          "flash", "sparkle_wipe", "pixel")


def pos(style, x, y, rnd=0.0):
    if style == "center_out":
        return max(abs(x - 0.5), abs(y - 0.5)) * 2.0
    if style == "edges_in":
        return 1.0 - max(abs(x - 0.5), abs(y - 0.5)) * 2.0
    if style == "curtain":
        return y
    if style == "diagonal":
        return (x + y) / 2.0
    if style == "pixel":
        return rnd
    if style == "flash":
        return 0.0  # everything at once
    return x  # wipe_lr, sparkle_wipe
