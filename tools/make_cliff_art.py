"""make_cliff_art.py -- writes the Cliffhanger TV art as original vector drawings (2026-11-27):

  graphics/art/cliffhanger_bg.svg       the mountain, 911 x 603 (the 911 x 527 layout + config.CLIFF_HEADROOM of sky)
  graphics/art/cliffhanger_climber.svg  the climber, 54 x 92 (his boots sit on the ruler point at local 26, 86)

  python tools/make_cliff_art.py

Everything is placed from config.CLIFF_RULER_ZERO / CLIFF_RULER_STEP / CLIFF_HEADROOM / CLIFF_CLIMBER_OFFSET, so
the trail markers 0-25, the summit ledge (where a fall starts, 25 + CLIFF_OVERHANG_SPACES) and the climber's
feet line up with graphics/rig_view.py exactly as before. Vector, so the TV's 5x climb camera stays sharp.
"""
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import config  # noqa: E402

W, H = 911, 527 + int(config.CLIFF_HEADROOM)
INK = "#1d1a24"


# Stroke digits (the TV's SVG renderer, nanosvg, draws no <text>): polylines in a 4 x 6 box.
_DIGITS = {"0": ["0,0 4,0 4,6 0,6 0,0"], "1": ["1,1 2,0 2,6", "1,6 3,6"], "2": ["0,0 4,0 4,3 0,3 0,6 4,6"],
           "3": ["0,0 4,0 4,6 0,6", "1,3 4,3"], "4": ["0,0 0,3 4,3", "3,0 3,6"], "5": ["4,0 0,0 0,3 4,3 4,6 0,6"],
           "6": ["4,0 0,0 0,6 4,6 4,3 0,3"], "7": ["0,0 4,0 2,6"], "8": ["0,0 4,0 4,6 0,6 0,0", "0,3 4,3"],
           "9": ["4,3 0,3 0,0 4,0 4,6 0,6"]}


def number(n, height, color, width):
    """<g> drawing the number n centred on (0, 0), `height` tall."""
    k = height / 6.0
    txt = str(n)
    adv = 5.8
    total = adv * len(txt) - 1.8
    out = [f'<g transform="scale({k:.3f}) translate({-total / 2:.2f},-3)" fill="none" stroke="{color}" '
           f'stroke-width="{width / k:.2f}" stroke-linecap="round" stroke-linejoin="round">']
    for i, ch in enumerate(txt):
        for line in _DIGITS[ch]:
            p = " ".join(f"{float(a) + i * adv:.1f},{b}" for a, b in (q.split(",") for q in line.split()))
            out.append(f'<polyline points="{p}"/>')
    out.append('</g>')
    return "".join(out)


def mark(m):
    x = config.CLIFF_RULER_ZERO[0] + config.CLIFF_RULER_STEP[0] * m
    y = config.CLIFF_RULER_ZERO[1] + config.CLIFF_RULER_STEP[1] * m + config.CLIFF_HEADROOM
    return x, y


def pts(seq):
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in seq)


def background():
    sx, sy = config.CLIFF_RULER_STEP
    ang = math.degrees(math.atan2(sy, sx))            # trail slope (negative = rising to the right)
    edge_m = 25 + config.CLIFF_OVERHANG_SPACES        # he walks this far, then drops
    ex, ey = mark(edge_m)
    x0, y0 = mark(-3)
    o = []
    o.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">')
    o.append('<defs>'
             '<linearGradient id="sky" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="0" y2="' f'{H}' f'">'
             '<stop offset="0" stop-color="#27508f"/><stop offset="0.55" stop-color="#5f9fd8"/>'
             '<stop offset="1" stop-color="#bfe3f5"/></linearGradient>'
             '<radialGradient id="sun" gradientUnits="userSpaceOnUse" cx="600" cy="92" r="70">'
             '<stop offset="0" stop-color="#fff6c8"/><stop offset="0.45" stop-color="#ffe27a"/>'
             '<stop offset="1" stop-color="#ffe27a" stop-opacity="0"/></radialGradient>'
             '<linearGradient id="rock" x1="0" y1="0" x2="1" y2="1">'
             '<stop offset="0" stop-color="#9b8c7a"/><stop offset="1" stop-color="#6c5f52"/></linearGradient>'
             '<linearGradient id="face" x1="0" y1="0" x2="1" y2="0">'
             '<stop offset="0" stop-color="#5d5147"/><stop offset="1" stop-color="#3f362f"/></linearGradient>'
             '<linearGradient id="far" x1="0" y1="0" x2="0" y2="1">'
             '<stop offset="0" stop-color="#9fb2d8"/><stop offset="1" stop-color="#c9d6ee"/></linearGradient>'
             '<linearGradient id="mid" x1="0" y1="0" x2="0" y2="1">'
             '<stop offset="0" stop-color="#6f86b8"/><stop offset="1" stop-color="#93a7cf"/></linearGradient>'
             '</defs>')
    o.append(f'<rect width="{W}" height="{H}" fill="url(#sky)"/>')
    # sun (right of the scoreboard, clear of the summit)
    o.append('<circle cx="600" cy="92" r="70" fill="url(#sun)"/><circle cx="600" cy="92" r="26" fill="#fff3b0"/>')

    def cloud(cx, cy, s, op=0.95):
        return (f'<g fill="#ffffff" fill-opacity="{op}" stroke="#d6e6f3" stroke-width="1.2">'
                f'<ellipse cx="{cx}" cy="{cy}" rx="{34*s}" ry="{13*s}"/>'
                f'<circle cx="{cx-14*s}" cy="{cy-7*s}" r="{12*s}"/><circle cx="{cx+6*s}" cy="{cy-12*s}" r="{15*s}"/>'
                f'<circle cx="{cx+22*s}" cy="{cy-4*s}" r="{10*s}"/></g>')
    o.append(cloud(470, 150, 1.0) + cloud(150, 255, 0.8, 0.8) + cloud(720, 220, 0.7, 0.85))
    # far range
    far = [(0, 420), (60, 360), (120, 395), (190, 330), (260, 385), (330, 345), (400, 400), (470, 352),
           (540, 410), (610, 370), (690, 420), (760, 380), (850, 430), (911, 400), (911, H), (0, H)]
    o.append(f'<polygon points="{pts(far)}" fill="url(#far)"/>')
    for a, b, c in ((60, 360, 25), (190, 330, 28), (330, 345, 24), (470, 352, 24), (610, 370, 22)):
        o.append(f'<polygon points="{pts([(a, b), (a - c * 0.7, b + c * 0.55), (a - c * 0.2, b + c * 0.4), (a + c * 0.15, b + c * 0.6), (a + c * 0.7, b + c * 0.5)])}" fill="#f4f8ff"/>')
    # mid range
    mid = [(0, 470), (80, 430), (150, 470), (230, 440), (300, 480), (380, 455), (450, 500), (911, 500),
           (911, H), (0, H)]
    o.append(f'<polygon points="{pts(mid)}" fill="url(#mid)"/>')
    # valley mist far below the cliff, where he falls
    o.append(f'<path d="M{ex-10:.0f},{H} L{ex-10:.0f},470 Q{ex+40:.0f},440 {ex+90:.0f},468 '
             f'Q{W-40},430 {W},455 L{W},{H} Z" fill="#e9f1fa" fill-opacity="0.85"/>')
    # the mountain: trail ridge from bottom-left up to the summit ledge, then a sheer face down the right
    ridge = [(x0, y0)]
    for i in range(-2, int(edge_m) + 1):
        x, y = mark(i)
        ridge.append((x, y + 6 + (1.5 if i % 2 else -1.0)))      # a slightly uneven ridge just under the trail
    ridge.append((ex + 2, ey + 6))
    face = [(ex + 6, ey + 14), (ex - 2, ey + 70), (ex + 10, ey + 130), (ex - 6, ey + 200), (ex + 8, ey + 270),
            (ex - 10, ey + 350), (ex + 4, H)]
    body = [(0, ridge[0][1] + 20)] + ridge + face + [(0, H)]
    o.append(f'<polygon points="{pts(body)}" fill="url(#rock)" stroke="{INK}" stroke-width="2.4" '
             f'stroke-linejoin="round"/>')
    # the cliff face, darker, with cracks
    fx = ex - 70
    face_poly = [(ex + 2, ey + 6)] + face + [(fx, H), (fx + 20, ey + 300), (fx + 40, ey + 160), (ex - 30, ey + 40)]
    o.append(f'<polygon points="{pts(face_poly)}" fill="url(#face)" stroke="{INK}" stroke-width="2.4" '
             f'stroke-linejoin="round"/>')
    for (a, b), (c, d) in (((ex - 20, ey + 60), (ex - 34, ey + 120)), ((ex - 10, ey + 170), (ex - 28, ey + 240)),
                           ((ex - 30, ey + 280), (ex - 18, ey + 360))):
        o.append(f'<path d="M{a:.0f},{b:.0f} L{(a + c) / 2 + 6:.0f},{(b + d) / 2:.0f} L{c:.0f},{d:.0f}" '
                 f'stroke="{INK}" stroke-width="1.8" fill="none" stroke-linecap="round"/>')
    # rock strata parallel to the trail
    for k, off in enumerate((40, 85, 140, 205, 280)):
        a = mark(-2 + k)
        b = mark(min(edge_m - 3, 23 - k * 3))
        o.append(f'<path d="M{a[0]:.0f},{a[1] + off:.0f} Q{(a[0] + b[0]) / 2:.0f},{(a[1] + b[1]) / 2 + off + 12:.0f} '
                 f'{b[0]:.0f},{b[1] + off:.0f}" stroke="#5a4e43" stroke-width="2" fill="none" '
                 f'stroke-linecap="round" stroke-opacity="0.7"/>')
    # snow on the upper ridge
    snow = []
    for i in range(17, int(edge_m) + 1):
        x, y = mark(i)
        snow.append((x, y + 5))
    snow.append((ex, ey + 5))
    lower = []
    for j, (x, y) in enumerate(reversed(snow)):
        lower.append((x - 4, y + 16 + (9 if j % 2 else 2)))
    o.append(f'<polygon points="{pts(snow + lower)}" fill="#f7fbff" stroke="{INK}" stroke-width="1.6" '
             f'stroke-linejoin="round"/>')
    # pine trees low on the slope
    for tx in (40, 95, 150, 205):
        ty = mark((tx - config.CLIFF_RULER_ZERO[0]) / sx)[1] + 95 + (tx % 3) * 6
        o.append(f'<g stroke="{INK}" stroke-width="1.4" stroke-linejoin="round">'
                 f'<rect x="{tx - 2}" y="{ty}" width="4" height="10" fill="#5a3a22"/>'
                 f'<polygon points="{tx},{ty - 34} {tx - 14},{ty + 2} {tx + 14},{ty + 2}" fill="#2f6b45"/>'
                 f'<polygon points="{tx},{ty - 46} {tx - 10},{ty - 18} {tx + 10},{ty - 18}" fill="#3b7f53"/></g>')
    # the trail: a packed-earth path the climber walks on (his boots stand on it)
    a, b = mark(-3), (ex, ey)
    o.append(f'<line x1="{a[0]:.1f}" y1="{a[1] + 3:.1f}" x2="{b[0]:.1f}" y2="{b[1] + 3:.1f}" stroke="{INK}" '
             f'stroke-width="9" stroke-linecap="round"/>')
    o.append(f'<line x1="{a[0]:.1f}" y1="{a[1] + 3:.1f}" x2="{b[0]:.1f}" y2="{b[1] + 3:.1f}" stroke="#d9b273" '
             f'stroke-width="6" stroke-linecap="round"/>')
    # trail markers 0-25: a post just below the path with a numbered sign; every 5th one red
    for m in range(0, 26):
        x, y = mark(m)
        big = m % 5 == 0
        fill = "#d8342c" if big else "#fff4d6"
        txt = "#ffffff" if big else INK
        w_, h_ = (17, 12) if big else (14, 10.5)
        o.append(f'<line x1="{x:.1f}" y1="{y + 6:.1f}" x2="{x:.1f}" y2="{y + 15:.1f}" stroke="{INK}" '
                 f'stroke-width="1.6"/>')
        o.append(f'<g transform="translate({x:.1f},{y + 21:.1f}) rotate({ang:.1f})">'
                 f'<rect x="{-w_ / 2:.1f}" y="{-h_ / 2:.1f}" width="{w_}" height="{h_}" rx="2.2" fill="{fill}" '
                 f'stroke="{INK}" stroke-width="1.3"/>'
                 + number(m, 6.6 if big else 5.6, txt, 1.25) + '</g>')
    # summit flag at the ledge
    o.append(f'<g stroke="{INK}" stroke-width="1.5" stroke-linejoin="round">'
             f'<line x1="{ex - 8:.0f}" y1="{ey + 3:.0f}" x2="{ex - 8:.0f}" y2="{ey - 30:.0f}"/>'
             f'<polygon points="{ex - 8:.0f},{ey - 30:.0f} {ex + 12:.0f},{ey - 24:.0f} {ex - 8:.0f},{ey - 18:.0f}" '
             f'fill="#ffd23f"/></g>')
    o.append('</svg>')
    return "\n".join(o)


def climber():
    """Original mountaineer: blue beanie, orange parka, green pack with a rope coil, ice axe. Faces right,
    walking up the trail; boot soles on the trail slope, centred on local (26, 86)."""
    sx, sy = config.CLIFF_RULER_STEP
    ang = math.degrees(math.atan2(sy, sx))
    o = ['<svg xmlns="http://www.w3.org/2000/svg" width="54" height="92" viewBox="0 0 54 92">',
         f'<g stroke="{INK}" stroke-width="1.1" stroke-linejoin="round" stroke-linecap="round">']
    # back arm (behind everything)
    o.append('<path d="M19,35 Q14,44 15,51" stroke="#c4501f" stroke-width="5.5" fill="none"/>'
             '<path d="M19,35 Q14,44 15,51" stroke="#e8632b" stroke-width="3.6" fill="none"/>')
    # legs: back leg down to the lower boot, front leg bent up to the higher one
    for d in ("M21,56 L18,70 L21,82", "M28,56 L37,65 L34,76"):
        o.append(f'<path d="{d}" stroke="{INK}" stroke-width="7.4" fill="none"/>'
                 f'<path d="{d}" stroke="#34405c" stroke-width="5.2" fill="none"/>')
    # boots, soles along the slope
    for bx, by in ((21, 85.5), (35, 78.5)):
        o.append(f'<g transform="translate({bx},{by}) rotate({ang:.1f})">'
                 f'<path d="M-4.5,-4 L2,-4 Q7,-3.5 7.5,1 L7.5,2.6 L-5,2.6 Z" fill="#7a4a24"/>'
                 f'<rect x="-5.2" y="2" width="13.2" height="2.2" rx="0.8" fill="#2a2320"/></g>')
    # backpack + rope coil
    o.append('<rect x="7" y="31" width="13" height="25" rx="4" fill="#2f7a46"/>'
             '<rect x="9" y="35" width="9" height="7" rx="2" fill="#3e9459"/>'
             '<circle cx="12.5" cy="54" r="6" fill="none" stroke="#c99a4e" stroke-width="2.6"/>'
             '<circle cx="12.5" cy="54" r="6" fill="none" stroke="#8a6630" stroke-width="0.7"/>')
    # parka (torso leaning into the climb) + hem
    o.append('<path d="M18,31 Q26,26 34,31 L34,46 Q33,55 31,58 L17,58 Q15,48 18,31 Z" fill="#e8632b"/>'
             '<path d="M17,55 L31,55 L31,58.5 L17,58.5 Z" fill="#c4501f"/>'
             '<path d="M26,30 L25,55" stroke="#c4501f" stroke-width="0.9" fill="none"/>')
    # scarf
    o.append('<path d="M23,28 Q29,31 35,28 L35,31.5 Q29,34 23,31.5 Z" fill="#ffd23f"/>'
             '<path d="M33,30 L37,37 L34.5,38 L31.5,31.5 Z" fill="#ffd23f"/>')
    # head
    o.append('<circle cx="30" cy="20" r="7.6" fill="#f2c39c"/>'
             '<path d="M36.8,19.5 Q40,21 37,23.2" fill="#f2c39c"/>'          # nose
             '<circle cx="33.8" cy="19.6" r="1.05" fill="#1d1a24" stroke="none"/>'
             '<path d="M31.8,23.6 Q34,25 36,23.2" fill="none" stroke-width="0.9"/>'   # smile
             '<path d="M23.4,18.4 Q25.5,16.6 28,17.6" fill="none" stroke="#6b4126" stroke-width="1.6"/>'  # hair
             '<path d="M24,22 Q23,26 26,27.5" fill="#6b4126" stroke-width="0.8"/>')  # hair at the neck
    # beanie with a pom-pom
    o.append('<path d="M22.4,15.6 Q22.6,7 30,6.6 Q37.6,7 37.6,15 Z" fill="#2a5bd7"/>'
             '<path d="M21.8,14.2 L38.1,13.6 L38.2,16.4 L22,17 Z" fill="#1f46a8"/>'
             '<circle cx="29.4" cy="5.4" r="3" fill="#ffffff"/>')
    # ice axe: shaft planted ahead on the trail, pick at the top
    o.append('<path d="M41,30 L47.5,74" stroke="#1d1a24" stroke-width="2.8" fill="none"/>'
             '<path d="M41,30 L47.5,74" stroke="#9b6a3a" stroke-width="1.6" fill="none"/>'
             '<path d="M35.5,30.5 Q41,26.5 47.5,29.8 L47,31.2 Q41.5,29 36,32 Z" fill="#b8c2cc"/>')
    # front arm reaching to the axe, mitten on the shaft
    o.append('<path d="M31,35 Q37,40 42.5,40.5" stroke="#c4501f" stroke-width="5.8" fill="none"/>'
             '<path d="M31,35 Q37,40 42.5,40.5" stroke="#e8632b" stroke-width="3.9" fill="none"/>'
             '<circle cx="43" cy="40.6" r="3.1" fill="#2a5bd7"/>')
    o.append('</g></svg>')
    return "\n".join(o)


def main():
    art = os.path.join(ROOT, "graphics", "art")
    for name, svg in (("cliffhanger_bg.svg", background()), ("cliffhanger_climber.svg", climber())):
        with open(os.path.join(art, name), "w", encoding="utf-8") as f:
            f.write(svg)
        print(f"graphics/art/{name}: {len(svg) // 1024 + 1} KB")


if __name__ == "__main__":
    main()
