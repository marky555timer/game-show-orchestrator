# graphics/rig_view.py
"""Practice rig TV view (2026-11-11, config.PRACTICE_RIG).

Draws the whole game board full-screen on an HDMI TV for a Pi that has no
board attached, so a show can be rehearsed without the rig:

  - the six LED panels, lit exactly as drivers/led_bridge.py would send them
    (any non-zero red = on; the real panels are 1-bit, single colour),
  - the 102 marquee bulbs at their photo-measured places
    (config.MARQUEE_LAYOUT), from the same pixel buffer drivers/wled_engine.py
    sends to the marquee board,
  - the outline strip as a band around the board. The real strip runs WLED's
    own effects on its board, which this does not reproduce: the band is
    held at one steady colour (the look's main colour and brightness), never
    blinking or moving, and the effect's name is printed underneath,
  - the four arcade button lights (drivers/simon_hardware.led_states()).


DMX lights are not drawn.

Runs as its OWN PROCESS (python -m graphics.rig_view), started and fed by
graphics/rig_feed.py inside the show app. On a Pi 3 every screen update costs
10-30 ms however little changed, which stalled the 40 fps show loop when this
drew in-process; here it has a CPU core to itself and the show never waits
for the TV. It imports no drivers: everything it draws arrives as one small
UDP datagram on localhost (config.RIG_VIEW_PORT), about 20 a second. Only the
LEDs, bulbs and strip cells that changed are redrawn.
"""
import json
import math
import os
import socket
import subprocess
import sys
import time

import numpy as np
import pygame

import config
from config import MATRIX_WIDTH, MATRIX_HEIGHT, PANELS, TOP_COMBINED

_BG = (0, 0, 0)
_BULB_OFF = (30, 30, 34)
_STRIP_OFF = (14, 14, 16)
_GAMMA = [int(round(255 * (v / 255.0) ** 0.55)) for v in range(256)]  # dim LED values read brighter in real life

_ready = False
_cell = 0
_areas = []        # (source rect in matrix pixels, screen rect) per LED panel area
_cell_x = None     # matrix [x, y] -> screen x of that LED's cell (-1: not on a panel)
_cell_y = None
_dot_on = None     # one lit / unlit panel LED, _cell px square
_dot_off = None
_bulbs = []        # (x, y) per marquee LED index, screen pixels
_bulb_r = 0
_bulb_boxes = []   # (first LED, count, screen rect) per marquee segment
_strip = []        # one rect per outline cell, in order around the loop
_strip_edges = []  # the strip's four sides, for display.update()
_strip_phase = None  # numpy: each strip cell's place around the loop, 0..1
_gamma_np = None
_buttons = []      # (color name, (x, y))
_btn_r = 0
_label_pos = (0, 0)
_font = None
_ip_font = None

# The Lenovo's own address where "OUTLINE: off" used to be (2026-11-26, operator): readable from across
# the room, so the host can always reach it (ssh / the admin page) without a keyboard. Looked up on a
# background thread every IP_REFRESH_SECONDS -- never on the draw path.
IP_REFRESH_SECONDS = 10.0
FOOT_MARGIN_FRACTION = 0.025    # bottom margin kept clear for TV overscan (the safe area)
_ip_text = {"v": "IP: looking..."}


def _wifi_signal():
    """{interface: "83% (-52 dBm)"} from /proc/net/wireless (link quality is out of 70 on Linux)."""
    out = {}
    try:
        with open("/proc/net/wireless") as f:
            for line in f.readlines()[2:]:
                f2 = line.split()
                if len(f2) >= 4:
                    link, level = float(f2[2].rstrip(".")), float(f2[3].rstrip("."))
                    out[f2[0].rstrip(":")] = f"{max(0, min(100, round(link / 70 * 100)))}% / {level:.0f} dBm"
    except Exception:
        pass
    return out


def _ip_lookup():
    out = subprocess.run(["ip", "-4", "-o", "addr", "show", "scope", "global"],
                         capture_output=True, text=True, timeout=5).stdout
    parts = []
    sig = _wifi_signal()
    for line in out.splitlines():
        f = line.split()
        if len(f) < 4 or "inet" not in f:
            continue
        dev, addr = f[1], f[f.index("inet") + 1].split("/")[0]
        if addr.startswith("192.168.42."):
            kind = "setup hotspot"
        elif dev.startswith("wl"):
            kind = "Wi-Fi"
        elif dev.startswith(("en", "eth")):
            kind = "cable"
        else:
            continue
        if kind == "Wi-Fi" and dev in sig:
            kind += ", signal " + sig[dev]     # 2026-11-26 (operator): strength next to the address
        parts.append(f"{addr} ({kind})")
    return "IP: " + ("   ".join(parts) if parts else "no network")


def _ip_worker():
    while True:
        try:
            _ip_text["v"] = _ip_lookup()
        except Exception as ex:
            _ip_text["v"] = "IP: unknown"
            print(f"[RIG VIEW] IP lookup failed: {ex}")
        time.sleep(IP_REFRESH_SECONDS)

_prev_view_label = "off"
_prev_lit = None
_prev_bulbs = None
_prev_strip = None
_prev_buttons = None
_prev_label = None
_first = True
_sign = None       # the name-in-lights sign beside the board (scaled), or None
_sign_pos = (0, 0)
_join = None       # "JOIN: triviacade.com/play" strip, bottom right (2026-11-17)
_join_pos = (0, 0)
_base = None       # the clean board; the real screen = this + flares
_stat_n = 0
_stat_sum = 0.0
_stat_max = 0.0
_stat_at = 0.0
_stat_parts = [0.0, 0.0, 0.0, 0.0]   # seconds in: panels, bulbs, strip+buttons, screen update


def _segment_points():
    """name -> [(photo x, photo y), ...] in LED order."""
    out = {}
    for name, runs in config.MARQUEE_LAYOUT.items():
        pts = []
        for x0, y0, x1, y1, n in runs:
            for k in range(n):
                f = k / (n - 1) if n > 1 else 0.0
                pts.append((x0 + (x1 - x0) * f, y0 + (y1 - y0) * f))
        out[name] = pts
    return out


def _init(screen):
    global _ready, _cell, _bulb_r, _btn_r, _font, _label_pos
    global _cell_x, _cell_y, _dot_on, _dot_off, _strip_phase, _gamma_np
    W, H = screen.get_size()
    seg_pts = _segment_points()
    allp = [p for pts in seg_pts.values() for p in pts]
    minx, maxx = min(p[0] for p in allp), max(p[0] for p in allp)
    miny, maxy = min(p[1] for p in allp), max(p[1] for p in allp)
    pad = 85.0                      # photo units from the outermost bulbs to the outline strip
    label_h = max(18, H // 40)
    # TVs crop the picture's edges (overscan, 2026-11-26: the bottom line was cut in half), so the
    # IP / JOIN line sits FOOT_MARGIN_FRACTION of the height up from the bottom, with the board above it.
    margin = int(H * FOOT_MARGIN_FRACTION)
    btn_col = int(W * 0.13)
    _btn_r = max(10, int(min(btn_col * 0.30, H * 0.075)))
    # Layout (2026-11-13): the board is centred on the screen; the name sign
    # and the button lights are each centred in their own black side band.
    global _sign, _sign_pos
    _sign = None
    side = int(btn_col)                                  # space kept clear on each side while sizing the board
    left, right = side, W - side

    k = min((H - label_h - 12) / (maxy - miny + 2 * pad), (right - left - 12) / (maxx - minx + 2 * pad))
    board_w, board_h = (maxx - minx + 2 * pad) * k, (maxy - miny + 2 * pad) * k
    ox = left + (right - left - board_w) / 2 + pad * k
    oy = (H - label_h - board_h) / 2 + pad * k

    def to_screen(p):
        return (int(round(ox + (p[0] - minx) * k)), int(round(oy + (p[1] - miny) * k)))

    _bulb_r = max(4, int(round(17 * k)))
    _bulbs[:] = [(0, 0)] * config.MARQUEE_TOTAL_LEDS
    boxes = {}
    for name, pts in seg_pts.items():
        start = config.MARQUEE_SEGMENTS[name]["start"]
        sp = [to_screen(p) for p in pts]
        for i, p in enumerate(sp):
            if 0 <= start + i < len(_bulbs):
                _bulbs[start + i] = p
        xs, ys = [p[0] for p in sp], [p[1] for p in sp]
        boxes[name] = (min(xs), min(ys), max(xs), max(ys))

    # LED panels: each sits centred inside its own bulb loop, all at one cell size.
    sources = {"title": TOP_COMBINED, "panel3": PANELS[3], "panel4": PANELS[4],
               "panel5": PANELS[5], "panel6": PANELS[6]}
    inset = _bulb_r + 5
    _cell = max(2, int(min(min((b[2] - b[0] - 2 * inset) / sources[n][2], (b[3] - b[1] - 2 * inset) / sources[n][3])
                           for n, b in boxes.items())))
    _dot_on = pygame.Surface((_cell, _cell))
    _dot_off = pygame.Surface((_cell, _cell))
    for dot, rgb in ((_dot_on, (255, 0, 0)), (_dot_off, (config.RIG_VIEW_OFF_PIXEL, 0, 0))):
        dot.fill(_BG)
        if _cell >= 5:
            pygame.draw.circle(dot, rgb, (_cell // 2, _cell // 2), max(1, (_cell - 1) // 2))
        else:
            dot.fill(rgb, (0, 0, max(1, _cell - 1), max(1, _cell - 1)))
    _cell_x = np.full((MATRIX_WIDTH, MATRIX_HEIGHT), -1, dtype=np.int32)
    _cell_y = np.full((MATRIX_WIDTH, MATRIX_HEIGHT), -1, dtype=np.int32)
    _areas.clear()
    _bulb_boxes.clear()
    for name, (x0, y0, x1, y1) in boxes.items():
        sx, sy, sw, sh = sources[name]
        pw, ph = sw * _cell, sh * _cell
        px0, py0 = (x0 + x1 - pw) // 2, (y0 + y1 - ph) // 2
        _cell_x[sx:sx + sw, sy:sy + sh] = (px0 + np.arange(sw) * _cell)[:, None]
        _cell_y[sx:sx + sw, sy:sy + sh] = (py0 + np.arange(sh) * _cell)[None, :]
        _areas.append(((sx, sy, sw, sh), pygame.Rect(px0, py0, pw, ph)))
        seg = config.MARQUEE_SEGMENTS[name]
        if name == "title":
            global _title_range
            _title_range = (seg["start"], seg["count"])
        m = _bulb_r + 4
        _bulb_boxes.append((seg["start"], seg["count"],
                            pygame.Rect(x0 - m, y0 - m, x1 - x0 + 2 * m, y1 - y0 + 2 * m)))

    # Outline strip: cells around a rectangle just outside the outermost bulbs.
    t = max(4, int(round(13 * k)))                       # band thickness
    rx0, ry0 = int(ox - pad * k * 0.72), int(oy - pad * k * 0.72)
    rx1 = int(ox + (maxx - minx) * k + pad * k * 0.72)
    ry1 = int(oy + (maxy - miny) * k + pad * k * 0.72)
    step = max(10, int(round(30 * k)))
    cells = []
    # Each side is split into a whole number of equal cells (2026-11-26): fixed-size steps left a
    # remainder that made the bottom overshoot the left corner and the top fall short of the right.
    nx = max(1, round((rx1 - rx0) / step))
    ny = max(1, round((ry1 - ry0) / step))
    xs = [rx0 + (rx1 - rx0) * i // nx for i in range(nx + 1)]
    ys = [ry0 + (ry1 - ry0) * i // ny for i in range(ny + 1)]
    for a, b in zip(xs, xs[1:]):
        cells.append(pygame.Rect(a, ry0 - t // 2, b - a - 2, t))                 # top, left -> right
    for a, b in zip(ys, ys[1:]):
        cells.append(pygame.Rect(rx1 - t // 2, a, t, b - a - 2))                 # right, down
    for a, b in reversed(list(zip(xs, xs[1:]))):
        cells.append(pygame.Rect(a, ry1 - t // 2, b - a - 2, t))                 # bottom, right -> left
    for a, b in reversed(list(zip(ys, ys[1:]))):
        cells.append(pygame.Rect(rx0 - t // 2, a, t, b - a - 2))                 # left, up
    _strip[:] = cells
    _strip_phase = np.arange(len(cells), dtype=np.float32) / len(cells)
    _gamma_np = np.array(_GAMMA, dtype=np.uint8)
    e = t + step
    _strip_edges[:] = [pygame.Rect(rx0 - e, ry0 - e, rx1 - rx0 + 2 * e, 2 * e),
                       pygame.Rect(rx0 - e, ry1 - e, rx1 - rx0 + 2 * e, 2 * e),
                       pygame.Rect(rx0 - e, ry0 - e, 2 * e, ry1 - ry0 + 2 * e),
                       pygame.Rect(rx1 - e, ry0 - e, 2 * e, ry1 - ry0 + 2 * e)]

    band_l = rx0 - t // 2                                # black band left of the outline strip
    band_r_x = rx1 + t // 2 + 1                          # where the right band starts

    # "TRIVIACADE" name-in-lights blade sign (2026-11-12): still picture,
    # drawn once, centred in the left band; skipped if the band is too narrow.
    try:
        img = pygame.image.load(os.path.join(os.path.dirname(os.path.abspath(__file__)), "art", "practice_sign.png"))
        sh = int(H * 0.94)
        sw = int(img.get_width() * sh / img.get_height())
        cap = min(int(W * 0.11), band_l - 12)
        if sw > cap:
            sw = cap
            sh = int(img.get_height() * sw / img.get_width())
        if sw >= 60:
            _sign = pygame.transform.smoothscale(img.convert(), (sw, sh))
            _sign_pos = ((band_l - sw) // 2, (H - sh) // 2)
    except Exception as ex:
        print(f"[RIG VIEW] No name sign: {ex}")

    # Arcade button lights: a column centred in the right band.
    bx = (band_r_x + W) // 2
    names = list(config.SIMON_HW_COLOR_ORDER)
    gap = _btn_r * 3
    top = H // 2 - gap * (len(names) - 1) // 2
    _buttons[:] = [(c, (bx, top + i * gap)) for i, c in enumerate(names)]

    pygame.font.init()
    _font = pygame.font.Font(None, label_h + 4)
    global _ip_font
    # IP / JOIN line (2026-11-26): just under the outline strip, at the biggest size that still ends
    # FOOT_MARGIN_FRACTION above the bottom (TV safe area); the board keeps its original size.
    text_top = ry1 + t // 2 + 3
    avail = H - margin - text_top
    fsize = label_h + 14
    while fsize > 14 and pygame.font.Font(None, fsize).get_height() > avail:
        fsize -= 1
    _ip_font = pygame.font.Font(None, fsize)
    _label_pos = (rx0, text_top)   # bottom left, level with the JOIN strip
    import threading
    threading.Thread(target=_ip_worker, name="rig-view-ip", daemon=True).start()
    # Join strip (2026-11-17, from a competitor's always-visible join code):
    # the stable redirector address, bottom right of the board, drawn once.
    global _join, _join_pos
    try:
        jf = _ip_font
        url = config.TUNNEL_REDIRECT_PLAY_URL.replace("https://", "").rstrip("/")
        _join = jf.render("JOIN: " + url, True, (235, 205, 80), _BG)
        _join_pos = (rx1 - _join.get_width(), text_top)
    except Exception as ex:
        print(f"[RIG VIEW] No join strip: {ex}")
    global _base
    _base = pygame.Surface((W, H)).convert()
    _build_flares()
    _ready = True
    print(f"[RIG VIEW] Practice board on a {W}x{H} screen: {_cell}px per panel LED, "
          f"{len(_bulbs)} marquee bulbs, {len(_strip)} outline cells.")


# ------------------------------------------------------------ star flares
# (2026-11-18) A cross-screen "star filter" look: a soft bloom plus four thin
# spikes, over the marquee bulbs only. Everything is drawn to _base (the clean
# board); each frame the changed areas are copied to the real screen and the
# flares are added on top, so a flare is erased just by not being drawn again.
#   Flares appear ONLY on the moving edge of the get-ready / song-change wipes, the
#   answer-panel sheen and the correct-answer chase (the show app says which is
#   running, view["edge"]); never otherwise. They turn slightly as they fade.
_FLARE_STEPS = 8
_flare_sprites = []   # grey sprites, small -> full size, all the same square size
_flare_cache = {}     # (step, tint, angle) -> tinted / rotated sprite
_flare_big = []       # the same, bigger: wipe flares
_eflares = []         # edge flares: {"i": bulb index, "born", "life"}
_title_range = (0, 0)  # (first LED, count) of the top title strip's bulbs
_last_edge_mode = None
_flare_prev = []      # rects the flares covered last frame


def _make_sprites(length_in_radii):
    r = _bulb_r
    length = max(16, int(r * length_in_radii))
    half = length + 2
    ys, xs = np.mgrid[-half:half + 1, -half:half + 1].astype(np.float32)
    ax, ay = np.abs(xs), np.abs(ys)
    w = max(1.2, r * 0.16)
    out = []
    for k in range(_FLARE_STEPS):
        f = (k + 1) / _FLARE_STEPS
        lk = length * f
        h = np.exp(-(ay / w) ** 2) * np.clip(1 - ax / lk, 0, 1) ** 1.15
        v = np.exp(-(ax / w) ** 2) * np.clip(1 - ay / lk, 0, 1) ** 1.15
        glow = np.exp(-(xs * xs + ys * ys) / (2 * (r * (0.9 + 0.7 * f)) ** 2)) * 1.0
        a = (np.clip(np.maximum(h, v) * 1.25 + glow, 0, 1) * (f ** 0.45) * 255).astype(np.uint8)
        out.append(pygame.surfarray.make_surface(np.stack([a, a, a], axis=2).transpose(1, 0, 2)))
    return out


def _build_flares():
    _flare_sprites.clear()
    _flare_cache.clear()
    _flare_sprites.extend(_make_sprites(config.RIG_FLARES_LENGTH))
    _flare_big.clear()
    _flare_big.extend(_make_sprites(config.RIG_FLARES_WIPE_LENGTH))


def _flare_sprite(step, color, angle, big=False):
    """The sprite for a step, tinted toward `color` (mixed with white) and turned `angle` degrees."""
    tint = tuple(min(255, ((int(v * 0.45 + 255 * 0.55)) // 16) * 16 + 15) for v in color)
    a = int(round(angle / 4.0)) * 4 % 360
    key = (step, tint, a, big)
    spr = _flare_cache.get(key)
    if spr is None:
        if len(_flare_cache) > 600:
            _flare_cache.clear()
        base = _flare_cache.get((step, tint, 0, big))
        if base is None:
            base = (_flare_big if big else _flare_sprites)[step].copy()
            base.fill(tint, special_flags=pygame.BLEND_RGB_MULT)
            _flare_cache[(step, tint, 0, big)] = base
        spr = pygame.transform.rotate(base, a) if a else base
        _flare_cache[key] = spr
    return spr


def _note_edge(old, px, now, view):
    """Edge flares: bulbs that just got much brighter (or whiter) while the app
    says a wipe, the sheen or the correct-answer chase is running. A wipe is
    the strongest moment ("next song!"): every bulb that changed to a bright
    colour is in the band, and up to config.RIG_FLARES_WIPE_DENSITY of them
    flare, even when the whole board changes at once (a flash-style wipe)."""
    global _last_edge_mode
    mode = view.get("edge")
    if mode != _last_edge_mode:
        _last_edge_mode = mode
        print(f"[RIG VIEW] flare edge mode: {mode or 'none'}")
    if not mode or old is None or not config.RIG_FLARES_ENABLED or not _flare_sprites:
        return
    new = np.frombuffer(px, dtype=np.uint8).reshape(-1, 3).astype(np.int16)
    prev = np.frombuffer(old, dtype=np.uint8).reshape(-1, 3).astype(np.int16)
    if mode == "wipe":
        up = (np.abs(new - prev).max(axis=1) >= 24) & (new.max(axis=1) >= config.RIG_FLARES_MIN_BRIGHT)
        cap, per_frame, reach = config.RIG_FLARES_WIPE_MAX, config.RIG_FLARES_WIPE_MAX, _bulb_r * 1.4
    else:
        up = (new.max(axis=1) - prev.max(axis=1) >= config.RIG_FLARES_EDGE_JUMP) | \
             (new.min(axis=1) - prev.min(axis=1) >= config.RIG_FLARES_EDGE_WHITE_JUMP)
        cap, per_frame, reach = config.RIG_FLARES_EDGE_MAX, 2, _bulb_r * 3.5
        if mode == "intro":                     # show-open ring chase: stars on the moving bulbs
            cap, per_frame, reach = config.RIG_FLARES_INTRO_MAX, 3, _bulb_r * 2.5
    if mode == "chase":                         # answer area only, never the top strip
        t0, tn = _title_range
        up[t0:t0 + tn] = False
    rising = np.nonzero(up)[0]
    if len(rising) == 0:
        return
    if mode != "wipe" and len(rising) > max(2, len(up) * 0.3):
        return                                  # a whole-board flash is not an edge (wipes handle it above)
    if mode == "wipe":
        rising = np.random.permutation(rising)[:max(1, int(round(len(rising) * config.RIG_FLARES_WIPE_DENSITY)))]
    live = [f for f in _eflares if now - f["born"] < f["life"]]
    added = 0
    for i in np.random.permutation(rising):
        if len(live) >= cap or added >= per_frame:
            break
        i = int(i)
        x, y = _bulbs[i]
        if all(abs(x - _bulbs[f["i"]][0]) + abs(y - _bulbs[f["i"]][1]) > reach for f in live):
            if mode == "wipe":
                f = {"i": i, "born": now, "wipe": True,
                     "life": float(np.random.uniform(*config.RIG_FLARES_WIPE_LIFE_SECONDS))}
            else:
                f = {"i": i, "born": now, "life": float(np.random.uniform(0.5, 0.75))}
            live.append(f)
            _eflares.append(f)
            added += 1


def _bulb_flare(f, now, px, out):
    o = f["i"] * 3
    c = (px[o], px[o + 1], px[o + 2])
    if max(c) < config.RIG_FLARES_MIN_BRIGHT // 3:
        return                              # the bulb went dark (the edge has moved on): let its flare go
    p = (now - f["born"]) / f["life"]
    top = len(_flare_sprites) - 1
    if f.get("wipe"):
        # Born at full strength right on the edge, shrinking away as the edge moves on.
        step = int(max(0.0, 1.0 - p) ** 0.6 * top + 0.5)
        spr = _flare_sprite(step, c, config.RIG_FLARES_WIPE_ANGLE * p, big=True)
        rect = spr.get_rect(center=_bulbs[f["i"]])
        out += [(spr, rect), (spr, rect)]
        return
    s = math.sin(math.pi * p)
    if s < 0.04:
        return
    s = s ** 0.5                            # strong: reach full size quickly, hold it
    spr = _flare_sprite(int(s * top + 0.5), c, config.RIG_FLARES_WIPE_ANGLE * p)
    rect = spr.get_rect(center=_bulbs[f["i"]])
    out += [(spr, rect), (spr, rect)]       # drawn twice: fully on


def _flare_frame(now, px):
    """Retire old edge flares; returns [(sprite, rect)] to add on top this frame."""
    if not config.RIG_FLARES_ENABLED or not _flare_sprites:
        return []
    out = []
    _eflares[:] = [f for f in _eflares if now - f["born"] < f["life"]]
    for f in _eflares:
        _bulb_flare(f, now, px, out)
    return out


def _scale(rgb, f):
    return (int(rgb[0] * f), int(rgb[1] * f), int(rgb[2] * f))


def _strip_colors(view):
    """The strip's one steady colour (gamma applied). Every look is held
    solid in its main colour at its brightness: no blink, chase, rainbow or
    sound pulse (2026-11-12 -- the animated stand-ins flashed too hard on a
    TV, and WLED's real patterns are not being reproduced anyway)."""
    if view["kind"] == "off":
        return _STRIP_OFF
    c = _scale(view["c1"], view["bri"] / 255.0)
    return (_GAMMA[c[0]], _GAMMA[c[1]], _GAMMA[c[2]]) if any(c) else _STRIP_OFF


# ------------------------------------------------------------------ Cliffhanger (drivers/cliffhanger_engine.py)
_cliff = {"art": None, "was_up": False, "snap": None}
# Climb camera (2026-11-26, operator): while he climbs, the TV zooms in on him and follows him up the
# ruler (so the back of the room can see), then eases back out to the whole mountain.
_cam = {"z": 1.0, "cx": None, "cy": None, "t": 0.0, "climb_end": 0.0, "world": None}


def _cliff_assets(screen):
    """The art scaled to fit the screen (letterboxed), the climber at the same scale, fonts."""
    W, H = screen.get_size()
    if _cliff["art"] is not None and _cliff["size"] == (W, H):
        return _cliff
    bg = _cliff_headroom(pygame.image.load(config.CLIFF_ART_BG).convert())
    spr = pygame.image.load(config.CLIFF_ART_CLIMBER).convert_alpha()
    k = min(W / bg.get_width(), H / bg.get_height())
    _cliff.update(size=(W, H), k=k, ox=(W - bg.get_width() * k) / 2, oy=(H - bg.get_height() * k) / 2,
                  art=pygame.transform.smoothscale(bg, (int(bg.get_width() * k), int(bg.get_height() * k))),
                  climber=pygame.transform.smoothscale(spr, (int(spr.get_width() * k), int(spr.get_height() * k))),
                  font=pygame.font.Font(None, max(18, int(44 * k))), big=pygame.font.Font(None, max(30, int(110 * k))),
                  small=pygame.font.Font(None, max(14, int(26 * k))))
    return _cliff


def _cliff_headroom(bg):
    """The art's top edge is a grey storm-cloud band; the ruler's top marks sit so close to it that
    the climber's head would leave the screen. Extend that band upward by config.CLIFF_HEADROOM px:
    the top row, smoothed sideways, fading to a darker grey."""
    pad = int(config.CLIFF_HEADROOM)
    if pad <= 0:
        return bg
    w, h = bg.get_size()
    row = [bg.get_at((x, 0))[:3] for x in range(w)]
    r = 40
    smooth = []
    for x in range(w):
        seg = row[max(0, x - r):x + r + 1]
        smooth.append(tuple(sum(c[i] for c in seg) // len(seg) for i in range(3)))
    out = pygame.Surface((w, h + pad)).convert()
    dark = (70, 72, 80)
    for y in range(pad):
        t = (pad - y) / pad           # 1 at the very top, 0 at the art's edge
        t = t * t
        for x in range(w):
            c = smooth[x]
            out.set_at((x, y), tuple(int(c[i] + (dark[i] - c[i]) * t) for i in range(3)))
    # blend the first few art rows into the smoothed band so there is no hard seam
    out.blit(bg, (0, pad))
    for y in range(6):
        a = 1 - y / 6
        for x in range(w):
            c, s = out.get_at((x, pad + y))[:3], smooth[x]
            out.set_at((x, pad + y), tuple(int(c[i] + (s[i] - c[i]) * a * 0.6) for i in range(3)))
    return out


def _cliff_pt(a, mark):
    """Screen point of ruler mark `mark` (0-25 on the art)."""
    x = config.CLIFF_RULER_ZERO[0] + config.CLIFF_RULER_STEP[0] * mark
    y = config.CLIFF_RULER_ZERO[1] + config.CLIFF_RULER_STEP[1] * mark + config.CLIFF_HEADROOM
    return a["ox"] + x * a["k"], a["oy"] + y * a["k"]


def _cliff_text(screen, font, text, pos, color, center=False):
    img = font.render(text, True, color)
    sh = font.render(text, True, (0, 0, 0))
    x, y = pos
    if center:
        x -= img.get_width() / 2
    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (2, 2)):
        screen.blit(sh, (x + dx, y + dy))
    screen.blit(img, (x, y))
    return img.get_height()


def _draw_cliff(screen, c):
    """The mountain: the climber slides up the ruler in one smooth, steady glide, one space per year
    off; over the limit he walks off the edge and tumbles."""
    a = _cliff_assets(screen)
    now = time.time()
    k = a["k"]
    W, H = screen.get_size()
    out = screen
    if _cam["world"] is None or _cam["world"].get_size() != (W, H):
        _cam["world"] = pygame.Surface((W, H)).convert()
    screen = _cam["world"]              # mountain + climber here; the camera crops it onto the TV
    screen.fill((0, 0, 0))
    screen.blit(a["art"], (a["ox"], a["oy"]))
    mps = config.CLIFF_MARKS_PER_SPACE
    # where he is
    hop = 0.0
    if c["phase"] == "climb":
        # One smooth slide up the ruler at a steady pace (2026-11-21: no per-space steps or hops).
        span = c["to"] - c["from"]
        s = c["from"] + min(span, max(0.0, (now - c["t0"]) / max(0.1, c["sps"])))
    elif c["phase"] in ("fall", "result") and c["result"] != "win" and c["fall_at"]:
        s = c["to"]
    else:
        s = c["shown"] if c["phase"] != "climb" else c["to"]
    if c["phase"] == "between" or (c["phase"] == "result" and c["result"] == "win"):
        s = min(c["total"], c["spaces"])
    x, y = _cliff_pt(a, s * mps)
    cx = x + config.CLIFF_CLIMBER_OFFSET[0] * k
    cy = y + config.CLIFF_CLIMBER_OFFSET[1] * k - hop
    spr = a["climber"]
    if c["fall_at"] and now >= c["fall_at"]:
        t = now - c["fall_at"]
        cx += 45 * k * t                                 # off the edge and down the cliff face
        cy += 0.5 * 800 * k * t * t - 110 * k * t        # a little hop, then gravity
        spr = pygame.transform.rotate(spr, -300 * t)
    screen.blit(spr, (cx, cy))
    _cliff_camera(out, screen, c, now, cx + spr.get_width() / 2, cy + spr.get_height() / 2)
    screen = out
    # the scoreboard, top left over the sky (drawn over the camera view, never zoomed)
    y0 = a["oy"] + 14 * k
    x0 = a["ox"] + 18 * k
    h = _cliff_text(screen, a["font"], "CLIFFHANGER", (x0, y0), (255, 210, 40))
    y0 += h * 1.05
    h = _cliff_text(screen, a["font"], f"Song {c['song']} of {c['songs']}", (x0, y0), (255, 255, 255))
    y0 += h * 1.05
    if c["guess"] is not None:
        if c.get("title"):
            song = f"“{c['title']}”" + (f"  —  {c['artist']}" if c.get("artist") else "")
            h = _cliff_text(screen, a["font"], song, (x0, y0), (140, 220, 255))
            y0 += h * 1.05
        h = _cliff_text(screen, a["font"], f"Guess {c['guess']}  ·  It was {c['year']}", (x0, y0), (255, 255, 255))
        y0 += h * 1.05
        h = _cliff_text(screen, a["font"], f"{c['off']} year{'s' if c['off'] != 1 else ''} off", (x0, y0), (255, 120, 120) if c["off"] else (120, 255, 140))
        y0 += h * 1.05
    _cliff_text(screen, a["font"], f"Total {c['total']} / {c['spaces']}", (x0, y0), (255, 210, 40))
    W, H = screen.get_size()
    if c["phase"] == "result" or (c["phase"] == "fall" and now - c["fall_at"] > 1.6):
        won = c["result"] == "win"
        _cliff_text(screen, a["big"], "SAFE!  +1500" if won else "HE FELL!  -1500", (W / 2, H * 0.40),
                    (90, 240, 120) if won else (255, 70, 70), center=True)


def _cliff_camera(out, world, c, now, fx, fy):
    """Blit `world` onto `out` through the climb camera: zoom config.CLIFF_CAM_ZOOM centred on the
    climber (fx, fy) during a climb (and CLIFF_CAM_HOLD_SECONDS after), 1x otherwise; zoom and centre
    glide (CLIFF_CAM_EASE_SECONDS) instead of jumping."""
    W, H = out.get_size()
    climbing = c["phase"] == "climb"
    if climbing:
        _cam["climb_end"] = now
    want = config.CLIFF_CAM_ZOOM if (climbing or now - _cam["climb_end"] < config.CLIFF_CAM_HOLD_SECONDS) else 1.0
    dt = min(0.25, max(0.0, now - (_cam["t"] or now)))
    _cam["t"] = now
    rate = 1.0 - math.exp(-dt * 3.0 / max(0.05, config.CLIFF_CAM_EASE_SECONDS))
    _cam["z"] += (want - _cam["z"]) * rate
    if _cam["cx"] is None:
        _cam["cx"], _cam["cy"] = fx, fy
    _cam["cx"] += (fx - _cam["cx"]) * min(1.0, rate * 2.5)      # follows his motion closely
    _cam["cy"] += (fy - _cam["cy"]) * min(1.0, rate * 2.5)
    z = _cam["z"]
    if z < 1.01:
        out.blit(world, (0, 0))
        return
    # the view's centre slides from the screen's centre to him as the zoom grows
    f = min(1.0, (z - 1.0) / max(0.01, config.CLIFF_CAM_ZOOM - 1.0))
    vx = W / 2 + (_cam["cx"] - W / 2) * f
    vy = H / 2 + (_cam["cy"] - H / 2) * f
    w, h = W / z, H / z
    x0 = min(max(0.0, vx - w / 2), W - w)
    y0 = min(max(0.0, vy - h / 2), H - h)
    crop = world.subsurface(pygame.Rect(int(x0), int(y0), max(1, int(w)), max(1, int(h))))
    out.blit(pygame.transform.smoothscale(crop, (W, H)), (0, 0))


def draw(screen, lit, px, view, leds):
    """One frame. lit: bool array [x, y] of the LED matrix; px: the marquee's
    RGB bytes; view: accent_engine.view_state(); leds: color -> lit."""
    global _prev_lit, _prev_bulbs, _prev_strip, _prev_buttons, _prev_label, _first
    global _stat_n, _stat_sum, _stat_max, _stat_at, _prev_view_label
    if not _ready:
        _init(screen)
    c = view.get("cliff")
    if c:
        # BEGIN GAME from a Cliffhanger round's pregame (2026-11-22): the board (with "What year did
        # this song debut?" on its panels) stays up for q_tv seconds, then fades into the mountain.
        el = time.time() - (c.get("q_t0") or 0.0)
        hold = c.get("q_tv") or 0.0
        if c.get("q_t0") and el < hold:
            _cliff["snap"] = None
            _cliff_assets(screen)       # load + scale the art now, so the fade starts without a hitch
        else:
            # Cliffhanger takes the whole TV; the board comes back fully redrawn afterwards.
            fade = c.get("q_fade") or 0.0
            fading = bool(c.get("q_t0")) and fade > 0 and el < hold + fade
            if fading and _cliff.get("snap") is None and not _cliff["was_up"]:
                _cliff["snap"] = screen.copy()          # the board as it was last shown
            _draw_cliff(screen, c)
            snap = _cliff.get("snap")
            if fading and snap is not None:
                t = (el - hold) / fade
                snap.set_alpha(int(255 * (1.0 - t * t * (3 - 2 * t))))   # smoothstep
                screen.blit(snap, (0, 0))
            elif snap is not None:
                _cliff["snap"] = None
            pygame.display.flip()
            _cliff["was_up"] = True
            return
    if _cliff["was_up"]:
        _cliff["was_up"] = False
        _first = True
        _prev_lit = _prev_bulbs = _prev_strip = _prev_buttons = _prev_label = None
    real = screen
    screen = _base        # everything below draws the clean board
    mono = time.monotonic()
    now = time.time()
    dirty = []
    if _first:
        screen.fill(_BG)
        if _sign is not None:
            screen.blit(_sign, _sign_pos)
        if _join is not None:
            screen.blit(_join, _join_pos)

    # 1. LED panels: only the LEDs that changed since the last draw
    diff = (_cell_x >= 0) if _prev_lit is None else (lit != _prev_lit) & (_cell_x >= 0)
    if diff.any():
        xs, ys = np.nonzero(diff)
        on, off = _dot_on, _dot_off
        screen.blits([(on if v else off, (x, y)) for v, x, y in
                      zip(lit[xs, ys].tolist(), _cell_x[xs, ys].tolist(), _cell_y[xs, ys].tolist())], False)
        for (sx, sy, sw, sh), rect in _areas:
            if diff[sx:sx + sw, sy:sy + sh].any():
                dirty.append(rect)
    _prev_lit = lit
    t1 = time.monotonic()

    # 2. Marquee bulbs: only the ones whose colour changed
    if px != _prev_bulbs:
        old = _prev_bulbs
        _prev_bulbs = px
        _note_edge(old, px, now, view)
        r = _bulb_r
        core = max(1, int(r * 0.45))
        circle = pygame.draw.circle
        for start, count, box in _bulb_boxes:
            o0, o1 = start * 3, (start + count) * 3
            if old is not None and px[o0:o1] == old[o0:o1]:
                continue
            dirty.append(box)
            for i in range(start, start + count):
                o = i * 3
                if old is not None and px[o:o + 3] == old[o:o + 3]:
                    continue
                cr, cg, cb = px[o], px[o + 1], px[o + 2]
                xy = _bulbs[i]
                if cr or cg or cb:
                    c = (_GAMMA[cr], _GAMMA[cg], _GAMMA[cb])
                    circle(screen, (c[0] * 3 // 8, c[1] * 3 // 8, c[2] * 3 // 8), xy, r + 3)
                    circle(screen, c, xy, r)
                    circle(screen, ((c[0] + 255) // 2, (c[1] + 255) // 2, (c[2] + 255) // 2), xy, core)
                else:
                    circle(screen, _BG, xy, r + 3)
                    circle(screen, _BULB_OFF, xy, r)
    t2 = time.monotonic()

    # 3. Outline strip: one steady colour. One-shot flashes (confirmations,
    # lightning) are skipped -- the strip keeps what it was showing.
    if view["label"] != "flash":
        colors = _strip_colors(view)
        if colors != _prev_strip:
            _prev_strip = colors
            fill = screen.fill
            for rect in _strip:
                fill(colors, rect)
            dirty.extend(_strip_edges)
        _prev_view_label = view["label"]
        if view["kind"] not in ("off", "solid"):
            _prev_view_label += "  (shown as a steady colour)"
    label = _ip_text["v"]          # was "OUTLINE: " + _prev_view_label (2026-11-26)
    if label != _prev_label:
        _prev_label = label
        text = _ip_font.render(label, True, (100, 100, 106), _BG)   # dim grey: a diagnostic, not for the room
        clear = pygame.Rect(_label_pos[0], _label_pos[1], screen.get_width() // 2, text.get_height())
        screen.fill(_BG, clear)
        screen.blit(text, _label_pos)
        if _join is not None:      # the label's clear rect can cover the join strip
            screen.blit(_join, _join_pos)
            dirty.append(pygame.Rect(_join_pos, _join.get_size()))
        dirty.append(clear)

    # 4. Button lights
    bkey = tuple(bool(leds.get(c)) for c, _p in _buttons)
    if bkey != _prev_buttons:
        _prev_buttons = bkey
        r = _btn_r
        for (color, (x, y)), lit_now in zip(_buttons, bkey):
            rgb = config.SIMON_HW_COLOR_RGB[color]
            pygame.draw.circle(screen, _BG, (x, y), r + 6)
            if lit_now:
                pygame.draw.circle(screen, _scale(rgb, 0.4), (x, y), r + 6)
                pygame.draw.circle(screen, rgb, (x, y), r)
                pygame.draw.circle(screen, tuple((v + 255) // 2 for v in rgb), (x, y), r // 2)
            else:
                pygame.draw.circle(screen, _scale(rgb, 0.16), (x, y), r)
                pygame.draw.circle(screen, _scale(rgb, 0.30), (x, y), r, 2)
            dirty.append(pygame.Rect(x - r - 6, y - r - 6, 2 * r + 13, 2 * r + 13))

    t3 = time.monotonic()
    flares = _flare_frame(now, px)
    if _first:
        _first = False
        real.blit(_base, (0, 0))
        for spr, rect in flares:
            real.blit(spr, rect, special_flags=pygame.BLEND_RGB_ADD)
        pygame.display.flip()
    elif dirty or flares or _flare_prev:
        areas = dirty + _flare_prev
        for rect in areas:
            real.blit(_base, rect, rect)
        _flare_prev[:] = []
        for spr, rect in flares:
            real.blit(spr, rect, special_flags=pygame.BLEND_RGB_ADD)
            _flare_prev.append(rect)
        pygame.display.update(areas + _flare_prev)

    # Cost of this view, logged every 10 minutes.
    t4 = time.monotonic()
    took = t4 - mono
    for i, d in enumerate((t1 - mono, t2 - t1, t3 - t2, t4 - t3)):
        _stat_parts[i] += d
    _stat_n += 1
    _stat_sum += took
    _stat_max = max(_stat_max, took)
    if not _stat_at:
        _stat_at = mono
    elif mono - _stat_at >= 600.0:
        p = [v / _stat_n * 1000 for v in _stat_parts]
        print(f"[RIG VIEW] {_stat_n / (mono - _stat_at):.0f} fps, draw avg {_stat_sum / _stat_n * 1000:.1f}ms, "
              f"worst {_stat_max * 1000:.0f}ms (panels {p[0]:.1f}, bulbs {p[1]:.1f}, strip {p[2]:.1f}, "
              f"screen {p[3]:.1f}).")
        _stat_at, _stat_n, _stat_sum, _stat_max = mono, 0, 0.0, 0.0
        _stat_parts[:] = [0.0, 0.0, 0.0, 0.0]


# ------------------------------------------------------------------ process
MAGIC = b"RIG1"
_LIT_BYTES = (MATRIX_WIDTH * MATRIX_HEIGHT + 7) // 8
_PX_BYTES = config.MARQUEE_TOTAL_LEDS * 3


def pack(lit, px, view, leds):
    """One datagram (graphics/rig_feed.py sends it): MAGIC, the LED matrix one
    bit per LED, the marquee's RGB bytes, then JSON for the strip and buttons."""
    extra = json.dumps({"view": view, "leds": leds}, separators=(",", ":")).encode()
    return MAGIC + np.packbits(lit).tobytes() + bytes(px) + extra


def _unpack(data):
    if len(data) < 4 + _LIT_BYTES + _PX_BYTES or data[:4] != MAGIC:
        return None
    bits = np.frombuffer(data, dtype=np.uint8, count=_LIT_BYTES, offset=4)
    lit = np.unpackbits(bits)[:MATRIX_WIDTH * MATRIX_HEIGHT].reshape(MATRIX_WIDTH, MATRIX_HEIGHT).astype(bool)
    o = 4 + _LIT_BYTES
    extra = json.loads(data[o + _PX_BYTES:].decode())
    view = extra["view"]
    view["c1"], view["c2"] = tuple(view["c1"]), tuple(view["c2"])
    return lit, bytes(data[o:o + _PX_BYTES]), view, extra["leds"]


def _set_tv_mode(mode):
    """Puts the TV in `mode` ("1280x720") through wlr-randr before the window
    opens: a Pi 3 cannot push 1080p frames fast enough. Best effort."""
    try:
        out = subprocess.run(["wlr-randr"], capture_output=True, text=True, timeout=10).stdout
        name = next((ln.split()[0] for ln in out.splitlines() if ln and not ln[0].isspace()), None)
        if name:
            subprocess.run(["wlr-randr", "--output", name, "--mode", mode], timeout=10)
            print(f"[RIG VIEW] TV output {name} set to {mode}.")
    except Exception as e:
        print(f"[RIG VIEW] Could not set the TV to {mode} ({e}) -- staying at its current mode.")


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", config.RIG_VIEW_PORT))
    except OSError:
        print("[RIG VIEW] Another practice-board window is already running -- this one exits.")
        return
    sock.settimeout(0.25)
    if config.RIG_VIEW_TV_MODE and sys.platform.startswith("linux"):
        _set_tv_mode(config.RIG_VIEW_TV_MODE)
        time.sleep(1.0)
    pygame.display.init()
    screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    pygame.display.set_caption("Practice board")
    pygame.mouse.set_visible(False)
    _init(screen)
    parent = os.getppid()
    frame = (np.zeros((MATRIX_WIDTH, MATRIX_HEIGHT), dtype=bool), bytes(_PX_BYTES),
             {"kind": "off", "c1": (0, 0, 0), "c2": (0, 0, 0), "bri": 0, "speed": 128, "label": "waiting for the show app"},
             {})
    draw(screen, *frame)
    while True:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return
        data = None
        try:
            data = sock.recv(4096)
            sock.setblocking(False)          # keep only the newest waiting datagram
            try:
                while True:
                    data = sock.recv(4096)
            except (BlockingIOError, OSError):
                pass
            sock.settimeout(0.25)
        except socket.timeout:
            if os.getppid() != parent:       # the show app is gone
                return
        if data:
            try:
                got = _unpack(data)
            except Exception:
                got = None
            if got is not None:
                frame = got
        draw(screen, *frame)


if __name__ == "__main__":
    main()
