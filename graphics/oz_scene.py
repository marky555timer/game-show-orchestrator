"""graphics/oz_scene.py -- the Wizard of Oz tornado story on the two top LED
panels (64x16, single colour: full red, dim red, off) for Pink Floyd's "On the
Run". Pure function of the song position (drivers/oz_story.py): render(pos, t)
returns a 64x16 Surface the matrix canvas blits over panels 1+2.

Acts: Dorothy and Toto run in the sun -> Miss Gulch pedals after them ->
the twister rises on the horizon -> the eerie lull at the farmhouse -> the
wind takes her bike and she comes back as the witch -> the long chase (witch
swooping at Toto, twister gaining) -> engulfed -> BOOM -> the house floats
down, the witch's legs under it, the rainbow.
"""
import math
import random

import pygame

from drivers import oz_story

W, H = 64, 16
GROUND = 15            # y of the ground line
FULL = (255, 0, 0)
DIM = (110, 0, 0)

_surf = pygame.Surface((W, H))
_cache = {}


# ------------------------------------------------------------------ sprites
def _sprite(rows):
    key = tuple(rows)
    s = _cache.get(key)
    if s is None:
        s = pygame.Surface((len(rows[0]), len(rows)))
        s.set_colorkey((0, 0, 0))
        for y, row in enumerate(rows):
            for x, c in enumerate(row):
                if c == "#":
                    s.set_at((x, y), FULL)
                elif c == "+":
                    s.set_at((x, y), DIM)
        _cache[key] = s
    return s


DOROTHY_TOP = [
    ".####.",
    "##++##",
    ".####.",
    "..##..",
    ".####.",
    ".#++#.",
    ".####.",
    ".####.",
]
DOROTHY_LEGS = [
    [".#..#.", "#....#"],
    ["..##..", "..##.."],
]
DOROTHY_STAND = DOROTHY_TOP + [".#..#.", ".#..#."]

TOTO = [
    ["#....#.", ".######", "..####.", "..#..#."],
    ["#....#.", ".######", "..####.", ".#..#.."],
]

HOUSE = [
    "....#....",
    "...###...",
    "..#####..",
    ".#######.",
    ".#+###+#.",
    ".#+#.#+#.",
    ".#######.",
]

COW = [
    "#.....#.",
    "########",
    "+######+",
    "#.#..#.#",
]

WITCH = [  # facing right: hat swept back, cape streaming, broom handle ahead
    "..#.............",
    "...##...........",
    "....###.........",
    "..#######.......",
    "....+##+........",
    "..+++###.+......",
    "#+#+####+#++#...",
    ".+#+#.##########",
    "..+...#..#......",
]

RUBY_LEGS = [  # two striped stockings + shoes under the house
    "##",
    "++",
    "##",
    "++",
    "##",
    "#+",
]


def _blit(spr, x, y, flip=False, rot=0):
    if flip:
        spr = pygame.transform.flip(spr, True, False)
    if rot:
        spr = pygame.transform.rotate(spr, rot)
    _surf.blit(spr, (int(x), int(y)))
    return spr


def _px(x, y, c=FULL):
    x, y = int(x), int(y)
    if 0 <= x < W and 0 <= y < H:
        _surf.set_at((x, y), c)


# ------------------------------------------------------------------ pieces
def _clouds(t, thick, flow=0.6):
    """Dithered cloud ceiling `thick` rows deep (0..~7), drifting."""
    n = int(thick)
    off = int(t * flow * 4)
    for y in range(min(n, 9)):
        for x in range(W):
            # lumpy lower edge
            edge = n - 1 + int(1.5 * math.sin(x * 0.45 + t * 0.4)) if y == n - 1 else n
            if y > edge:
                continue
            v = (x + off + y * 2) % 4
            if y < n - 2 or (x + y) % 2 == 0:
                _px(x, y, FULL if (v < 2 and y >= n - 3) else DIM)


def _ground(t, speed=0.0, dusty=False):
    for x in range(W):
        _px(x, GROUND, FULL if (x + int(t * speed)) % 6 else DIM)
    if dusty:
        for k in range(5):
            _px((k * 13 + int(t * speed * 1.5)) % W, GROUND - 1, DIM)


def _wind(t, amount, direction=1):
    """Streaks of blown dust/grass, more with `amount` (0..1)."""
    rnd = random.Random(7)
    for i in range(int(2 + amount * 9)):
        y = rnd.randint(2, GROUND - 2)
        sp = rnd.uniform(14, 34) * (0.5 + amount)
        ln = rnd.randint(2, 4)
        x0 = (rnd.random() * W * 2 + direction * t * sp) % (W + ln)
        x0 = x0 if direction > 0 else W - x0
        for k in range(ln):
            _px(x0 - direction * k, y, FULL if k == 0 else DIM)


def _dorothy(x, y_ground, t, rate=7, stand=False, flip=False):
    if stand:
        spr = _sprite(DOROTHY_STAND)
        _blit(spr, x, y_ground - spr.get_height() + 1, flip)
        return
    f = int(t * rate) % 2
    rows = DOROTHY_TOP + DOROTHY_LEGS[f]
    spr = _sprite(rows)
    bob = 0 if f == 0 else -1
    _blit(spr, x, y_ground - spr.get_height() + 1 + bob, flip)


def _toto(x, y_ground, t, rate=9, flip=False, hop=0):
    f = int(t * rate) % 2
    spr = _sprite(TOTO[f])
    _blit(spr, x, y_ground - spr.get_height() + 1 - (1 if f else 0) - hop, flip)


def _wheel(cx, cy, t):
    for dx, dy in ((-1, -2), (0, -2), (1, -2), (-2, -1), (2, -1), (-2, 0), (2, 0),
                   (-2, 1), (2, 1), (-1, 2), (0, 2), (1, 2)):
        _px(cx + dx, cy + dy)
    sp = int(t * 10) % 4
    d = [(-1, 0), (0, -1), (1, 0), (0, 1)][sp]
    _px(cx, cy, DIM)
    _px(cx + d[0], cy + d[1], DIM)


def _gulch(x, t, rate=6):
    """Miss Gulch on her bicycle, facing left; x = front wheel centre."""
    cy = GROUND - 3
    _wheel(x, cy, t)
    _wheel(x + 8, cy, t)
    for k in range(0, 9):                       # frame
        _px(x + k, cy - 2 + (1 if k > 4 else 0) * 0)
    for k in range(3):
        _px(x + 8 - k, cy - 1 - k)             # seat post / rear stay
    _px(x + 1, cy - 3)
    _px(x + 1, cy - 4)                          # handlebar stem
    _px(x, cy - 5)
    lean = int(t * rate) % 2                    # pedalling legs
    _px(x + 5 + lean, cy - 1, DIM)
    _px(x + 5 - lean, cy, DIM)
    for k in range(5):                          # torso leaning forward
        _px(x + 6 - k * 0.5, cy - 4 - k)
        _px(x + 7 - k * 0.5, cy - 4 - k)
    for dx, dy in ((2, 0), (3, 0), (2, 1), (3, 1)):   # head with a bun
        _px(x + 1 + dx, cy - 10 + dy, FULL)
    _px(x + 5, cy - 11)
    _px(x + 2, cy - 7, DIM)                     # arm reaching the bars
    _px(x + 1, cy - 6, DIM)


def _witch(x, y, t, flip=False, cackle=True):
    spr = _sprite(WITCH)
    if flip:
        spr = pygame.transform.flip(spr, True, False)
    halo = pygame.mask.from_surface(spr).to_surface(setcolor=(0, 0, 0), unsetcolor=None)
    for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):   # clear the clouds around her
        _surf.blit(halo, (int(x) + ox, int(y) + oy))
    _surf.blit(spr, (int(x), int(y)))
    # trailing sparks off the broom
    bx = x + (spr.get_width() if flip else -1)
    for k in range(3):
        _px(bx + (1 if flip else -1) * (k * 2), y + 7 + (k % 2), DIM if (int(t * 12) + k) % 2 else FULL)
    return spr


def _funnel(cx, top, w_top, w_bot, t, lean=0.0, speed=1.0):
    """Rotating dithered funnel: wide at `top`, narrow at the ground."""
    for y in range(top, GROUND + 1):
        f = (y - top) / max(1, (GROUND - top))
        hw = (w_bot + (w_top - w_bot) * (1 - f) ** 1.25) / 2.0
        c = cx + lean * (1 - f) * 6 + math.sin(t * 1.3 + f * 3.0) * (1 - f) * 1.8
        shift = int(t * 11 * speed) * (1 if y % 2 else -1)
        x0, x1 = int(c - hw), int(c + hw)
        for x in range(x0, x1 + 1):
            if x < 0 or x >= W:
                continue
            edge = x in (x0, x1)
            v = (x + shift + y) % 5
            if edge or v < 2:
                _px(x, y, FULL)
            elif v == 2:
                _px(x, y, DIM)
    return cx


def _debris(cx, top, hw_top, t, n=7, speed=1.0, seed=3):
    rnd = random.Random(seed)
    for i in range(n):
        f = rnd.uniform(0.0, 0.9)
        ph = rnd.uniform(0, 6.28) + t * rnd.uniform(3.5, 7.0) * speed
        y = top + f * (GROUND - top)
        hw = (2 + (hw_top - 2) * (1 - f)) + 2
        x = cx + math.cos(ph) * hw
        if math.sin(ph) > -0.2:                 # front half bright, back half dim
            _px(x, y, FULL)
        else:
            _px(x, y, DIM)


def _bolt(x, t, seed):
    rnd = random.Random(seed)
    xx = x
    for y in range(0, GROUND):
        _px(xx, y, FULL)
        xx += rnd.choice((-1, 0, 0, 1))
        if y % 4 == 3:
            _px(xx + 1, y, DIM)
            _px(xx - 1, y, DIM)


def _bolt_overlay(pos, t):
    """Draw any bolt that's live; return flash strength for the sky wash."""
    if oz_story.lightning(pos, raw=True) < 0.05 or pos >= oz_story.BOOM_AT - 0.01:
        return 0.0
    L = oz_story.lightning(pos)   # 0 before the crescendo: bolts are drawn, nothing flashes
    for b in oz_story.BOLTS:
        if 0 <= pos - b < 0.3 and not (0.09 < pos - b < 0.14):
            rnd = random.Random(int(b * 10))
            _bolt(rnd.randint(8, 56), t, int(b * 10))
    return L


def _flash_wash(L):
    """Lightning: briefly lights the sky dim wherever it's empty."""
    if L < 0.5:
        return
    for y in range(0, GROUND):
        for x in range(W):
            if _surf.get_at((x, y))[0] == 0 and (x + y) % 2 == 0:
                _px(x, y, DIM)


def _house(x, y, t, rot=0.0):
    spr = _sprite(HOUSE)
    if rot:
        spr = pygame.transform.rotate(spr, rot)
    w, h = spr.get_size()
    _surf.blit(spr, (int(x - w / 2), int(y - h / 2)))


def _cow(x, y, rot):
    spr = pygame.transform.rotate(_sprite(COW), rot)
    w, h = spr.get_size()
    _surf.blit(spr, (int(x - w / 2), int(y - h / 2)))


def _sparkle(x, y, t, seed=0):
    k = int(t * 8 + seed) % 4
    _px(x, y)
    if k < 2:
        _px(x - 1, y, DIM)
        _px(x + 1, y, DIM)
        _px(x, y - 1, DIM)
        _px(x, y + 1, DIM)


def _rainbow(cx, prog):
    """Arcs rising from the horizon behind the house; prog 0..1 sweeps them on."""
    for i, r in enumerate((16, 14, 12, 10)):
        shade = FULL if i % 2 == 0 else DIM
        for a in range(0, 181, 2):
            ang = math.radians(a)
            if a / 180.0 > prog:
                break
            x = cx + math.cos(ang) * r * 1.35
            y = GROUND + 1 - math.sin(ang) * r * 0.95
            _px(x, y, shade)


# ------------------------------------------------------------------ main
def render(pos, t):
    """pos = seconds into the song, t = wall clock (for free-running motion)."""
    _surf.fill((0, 0, 0))
    name, prog = oz_story.act_progress(pos)
    storm = oz_story.storm(pos)
    L = 0.0

    if name == "kansas":
        # a little sun setting, light wind, Dorothy + Toto trotting along
        sy = 3 + prog * 4
        for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1), (-1, 0), (2, 0), (0, -1), (1, -1)):
            _px(56 + dx, sy + dy, FULL if dy >= 0 and dx in (0, 1) else DIM)
        _clouds(t, 1 + prog * 1.5)
        _wind(t, 0.1 + prog * 0.2)
        _ground(t, 18)
        _dorothy(24, GROUND, t, 7)
        _toto(35, GROUND, t, 9)
    elif name == "gulch":
        thick = 2 + prog * 4
        _clouds(t, thick)
        _wind(t, 0.25 + prog * 0.2)
        _ground(t, 22, dusty=True)
        _dorothy(10, GROUND, t, 8)
        _toto(21, GROUND, t, 10)
        gx = 72 - prog * 34                      # she gains slowly
        _gulch(gx, t, 6)
        if prog > 0.8:                           # Toto looks back, bolts
            pass
    elif name == "twister":
        _clouds(t, 5 + prog * 2)
        fx = 54 - prog * 6
        top = int(8 - prog * 8)
        wt = 3 + prog * 17
        _funnel(fx, top, wt, 2 + prog * 3, t, speed=1.0 + prog)
        _debris(fx, top, wt / 2, t, n=int(3 + prog * 8))
        _wind(t, 0.5 + prog * 0.4)
        _ground(t, 30, dusty=True)
        _dorothy(10, GROUND, t, 10)
        _toto(21, GROUND, t, 12)
        _gulch(42 - prog * 8, t, 8)
        L = _bolt_overlay(pos, t)
    elif name == "lull":
        # standing still at the farmhouse, the funnel swaying far off
        _clouds(t, 7, flow=0.15)
        fx = 53
        _funnel(fx, 0, 17, 5, t, speed=0.4)
        _debris(fx, 0, 8, t, n=6, speed=0.4)
        _ground(t, 0)
        _house(8, GROUND - 3, t)
        _dorothy(22, GROUND, t, stand=True)
        _toto(30, GROUND, t, rate=1.5)
        # Gulch has stopped dead at the gate
        _gulch(33, t * 0.0, 0)
        if pos > 82:
            _wind(t, (pos - 82) / 5.0 * 0.8)
        L = _bolt_overlay(pos, t)
    elif name == "witch":
        _clouds(t, 7, flow=1.2)
        fx = 54
        _funnel(fx, 0, 17, 6, t, speed=1.3)
        _debris(fx, 0, 8, t, n=9, speed=1.3)
        _wind(t, 0.9)
        _ground(t, 0)
        if pos < 96:                              # Dorothy + Toto dash for the house
            k = (pos - 87) / 9.0
            _house(8, GROUND - 3, t)
            _dorothy(22 - k * 14, GROUND, t, 10, flip=True)
            _toto(31 - k * 17, GROUND, t, 12, flip=True)
            gx = 33
            _gulch(gx, t, 8)
        else:
            _house(8, GROUND - 3, t)
            kk = (pos - 96) / 11.0
            if pos < 99.5:                        # bike snatched up, spinning
                _surf_bike = (42 - (pos - 96) * 3, GROUND - 3 - (pos - 96) * 3.2)
                spin = (pos - 96) * 300
                bike = pygame.Surface((12, 12)); bike.set_colorkey((0, 0, 0))
                _bike_into(bike, t)
                rot = pygame.transform.rotate(bike, spin)
                _surf.blit(rot, (int(_surf_bike[0] - rot.get_width() / 2),
                                 int(_surf_bike[1] - rot.get_height() / 2)))
            else:                                 # the witch! swooping across the sky
                u = (pos - 99.5) / 7.5
                wx = 10 + u * 46 + math.sin(u * 9) * 5
                wy = 3 + math.sin(u * 7) * 3
                if 99.5 <= pos < 100.3:           # flash on transform
                    L = max(L, 1.0)
                _witch(wx, wy, t, flip=math.cos(u * 9) > 0)
        L = max(L, _bolt_overlay(pos, t))
    elif name == "chase":
        # Dorothy + Toto run right; the twister gains from the left; the
        # witch swoops at Toto on the beat, pulling up before she grabs him.
        u = (pos - 107.0) / (oz_story.CRESCENDO_AT - 107.0)
        _clouds(t, 6, flow=1.5)
        fx = -8 + u * 28 + math.sin(t * 0.8) * 1.5
        _funnel(fx, 0, 18 + u * 8, 5 + u * 3, t, lean=0.3, speed=1.6)
        _debris(fx, 0, 9 + u * 4, t, n=12, speed=1.6)
        _wind(t, 0.95)
        _ground(t, 40, dusty=True)
        dx = 30 + math.sin(t * 0.7) * 3
        _dorothy(dx, GROUND, t, 11)
        _toto(dx + 10, GROUND, t, 13, hop=1 if int(t * 3) % 4 == 0 else 0)
        # witch dive: period ~4s, closing on Toto then rising away
        ph = (t * 0.55) % 1.0
        dive = math.sin(ph * math.pi) ** 2
        wx = dx + 24 - dive * 12 + math.sin(t * 1.1) * 4
        wy = 1 + dive * 7
        _witch(wx, wy, t, flip=False)
        if 125 < pos < 135 and int(pos * 2) % 5 == 0:
            _cow(20 + (pos * 7) % 40, 4 + int(pos) % 5, pos * 200)
        if 148 < pos < 160:
            _cow(60 - ((pos - 148) * 6) % 70, 6, pos * 260)
        L = _bolt_overlay(pos, t)
    elif name == "engulf":
        u = (pos - oz_story.CRESCENDO_AT) / (oz_story.BOOM_AT - oz_story.CRESCENDO_AT)
        # the funnel sweeps right across everything
        fx = 12 + u * 48
        _clouds(t, 7 + 1, flow=2.5)
        _funnel(fx, 0, 20 + u * 36, 8 + u * 28, t, speed=2.2)
        _debris(fx, 0, 10 + u * 28, t, n=24, speed=2.4)
        ang = t * 7
        _house(fx + math.cos(ang) * 9 * (1 - 0.3 * u), 7 + math.sin(ang * 0.5) * 3,
               t, rot=(t * 220) % 360)
        _cow(fx + math.cos(ang + 2) * 12, 5 + math.sin(ang) * 3, t * 300)
        _dorothy(max(2, 24 - u * 22), GROUND - 4 * u, t, 12)
        _toto(max(2, 35 - u * 33), GROUND - 7 * u, t, 14)
        _witch(60 - u * 52, 2 + math.sin(t * 5) * 3, t, flip=True)
        L = _bolt_overlay(pos, t)
        # building white-out as the end nears
        if u > 0.75:
            rnd = random.Random(int(t * 15))
            for _ in range(int((u - 0.75) * 4 * 160)):
                _px(rnd.randrange(W), rnd.randrange(H), FULL)
    elif name == "boom":
        # white-out, then the picture shatters pixel by pixel into the dark
        d = pos - oz_story.BOOM_AT
        if d < 0.35:
            _surf.fill(FULL)
        else:
            k = min(1.0, (d - 0.35) / 2.6)
            rnd = random.Random(11)
            for y in range(H):
                for x in range(W):
                    if rnd.random() > k:
                        _px(x, y, FULL if rnd.random() < 0.5 else DIM)
    elif name == "ashes":
        # dark; a few specks drifting down
        rnd = random.Random(5)
        for i in range(8):
            x = rnd.randrange(W)
            y = (rnd.uniform(0, H) + (pos - 186.0) * rnd.uniform(1.5, 4.0)) % H
            _px(x, y, DIM)
    else:  # rainbow
        u = (pos - 190.0) / 23.6
        land = min(1.0, (pos - 190.0) / 6.5)
        hx = 22
        gy = GROUND - 3
        sway = math.sin(pos * 3.0) * (1 - land) * 5
        yy = -8 + (gy + 8) * (1 - (1 - land) ** 2)
        if pos > 196:
            _rainbow(40, min(1.0, (pos - 196.0) / 5.0))
        _ground(t, 0)
        if land >= 1.0:
            # witch's legs sticking out from under the house, ruby shoes twinkling
            leg = _sprite(RUBY_LEGS)
            _surf.blit(leg, (hx - 7, GROUND - leg.get_height() + 1))
            if pos > 197:
                _sparkle(hx - 7, GROUND - 1, t)
                _sparkle(hx - 6, GROUND - 1, t, 2)
            if pos > 199:
                _dorothy(hx + 8, GROUND, t, stand=True)
                _toto(hx + 15, GROUND, t, rate=2)
            if 191 < pos < 193.5:                # dust puff on landing
                for k in range(5):
                    _px(hx - 6 - (pos - 191) * 4 * k / 3, GROUND - 1 - (k % 2), DIM)
                    _px(hx + 6 + (pos - 191) * 4 * k / 3, GROUND - 1 - (k % 2), DIM)
        _house(hx + sway, min(yy, gy), t, rot=sway * 3)
    if L:
        _flash_wash(L)
    return _surf


# Short vignettes on the lower two panel rows: (start s, duration s, kind, row)
# row 0 = panels 3+4, row 1 = panels 5+6. Sparse on purpose -- the top is the show.
LOWER = oz_story.LOWER
_lower = pygame.Surface((W, H))
_dissolve = [[random.Random(x * 31 + y).random() for x in range(W)] for y in range(H)]


def render_lower(pos, t):
    """(64x16 Surface, row) while a vignette is on, else None. Dissolves in
    and out over 0.7s."""
    for start, dur, kind, row in LOWER:
        if start <= pos < start + dur:
            break
    else:
        return None
    u = (pos - start) / dur
    k = min(1.0, (pos - start) / 0.7, (start + dur - pos) / 0.7)
    _surf.fill((0, 0, 0))
    if kind == "run":
        _ground(t, 24, dusty=True)
        _wind(t, 0.3)
        _dorothy(22, GROUND, t, 8)
        _toto(33, GROUND, t, 10)
    elif kind == "funnel":
        fx = 20 + u * 24
        _funnel(fx, 0, 16, 4, t, speed=1.2)
        _debris(fx, 0, 8, t, n=8, speed=1.2, seed=9)
        _ground(t, 0)
    elif kind == "witch":
        wx = -16 + u * 96
        _witch(wx, 4 + math.sin(u * 14) * 3, t, flip=False)
    else:  # twirl: the house and a cow riding round the twister
        _funnel(32, 0, 14, 4, t, speed=1.5)
        a = t * 4
        _house(32 + math.cos(a) * 13, 6 + math.sin(a) * 2.5, t, rot=(t * 160) % 360)
        _cow(32 + math.cos(a + 3.1) * 15, 8 + math.sin(a + 3.1) * 2.5, t * 220)
        _ground(t, 0)
    _lower.blit(_surf, (0, 0))
    if k < 1.0:
        for y in range(H):
            for x in range(W):
                if _dissolve[y][x] > k:
                    _lower.set_at((x, y), (0, 0, 0))
    return _lower, row


# ------------------------------------------------- six-panel tornado finale
# The last ten seconds before the boom take over all six panels (a 64x48
# picture, three 64x16 rows): the twister seen head-on, its throat rushing at
# the viewer, with the house, the witch, Dorothy, Toto and a cow swirling out
# of the eye and growing as they come past.
FW, FH = 64, 48
_CX, _CY = FW / 2.0, FH / 2.0
_full = pygame.Surface((FW, FH))
_PX = [(x, y) for y in range(FH) for x in range(FW)]
_POLAR_R = []        # distance from the eye per pixel
_POLAR_A = []        # 3 arms * turns + 1.6 * ln(r + 1): the spiral phase (constant part)
for _x, _y in _PX:
    _dx, _dy = _x + 0.5 - _CX, _y + 0.5 - _CY
    _r = math.hypot(_dx, _dy)
    _POLAR_R.append(_r)
    _POLAR_A.append(3.0 * (math.atan2(_dy, _dx) / (2 * math.pi)) + 1.6 * math.log(_r + 1.0))
_NOISE = [random.Random(i * 7 + 3).random() for i in range(FW * FH)]
_NOISE2 = [random.Random(i * 13 + 5).random() for i in range(FW * FH)]
_FULL_RGB = bytes(FULL)
_DIM_RGB = bytes(DIM)
_OFF_RGB = bytes((0, 0, 0))

# (sprite kind, orbit start angle, loop seconds, loop offset)
_FLYERS = [("house", 0.5, 5.2, 0.00), ("witch", 4.0, 5.2, 0.18), ("girl", 2.2, 5.2, 0.38),
           ("toto", 2.6, 5.2, 0.45), ("cow", 5.4, 5.2, 0.62), ("witch", 1.3, 5.2, 0.82)]


def _scaled(spr, s):
    w, h = spr.get_size()
    return pygame.transform.scale(spr, (max(1, int(w * s)), max(1, int(h * s))))


def _full_blit(spr, cx, cy):
    """Blits `spr` centred at (cx, cy) with a one-pixel black halo so it reads
    over the spiral."""
    w, h = spr.get_size()
    x, y = int(cx - w / 2), int(cy - h / 2)
    halo = pygame.mask.from_surface(spr).to_surface(setcolor=(0, 0, 0), unsetcolor=None)
    for ox in range(-2, 3):
        for oy in range(-2, 3):
            if abs(ox) + abs(oy) <= 3:
                _full.blit(halo, (x + ox, y + oy))
    _full.blit(spr, (x, y))


def _flyer_sprite(kind, t):
    if kind == "house":
        return _sprite(HOUSE)
    if kind == "witch":
        return _sprite(WITCH)
    if kind == "cow":
        return _sprite(COW)
    if kind == "toto":
        return _sprite(TOTO[int(t * 9) % 2])
    return _sprite(DOROTHY_TOP + DOROTHY_LEGS[int(t * 8) % 2])


def _draw_flyers(pp, rot, t):
    items = []
    for kind, a0, period, off in _FLYERS:
        p = (pp / period + off) % 1.0
        if p < 0.06:
            continue
        items.append((p, kind, a0, period))
    items.sort()                                    # small (far) first, big (near) last
    for p, kind, a0, period in items:
        R = 2.0 + 44.0 * p ** 1.55
        ang = a0 + rot * 2 * math.pi + p * 2.2
        x, y = _CX + R * math.cos(ang), _CY + R * math.sin(ang) * 0.9
        spr = _flyer_sprite(kind, t)
        if kind == "witch" and -math.sin(ang) < 0:  # flying left: face left
            spr = pygame.transform.flip(spr, True, False)
        spr = _scaled(spr, 0.9 + 2.2 * p ** 1.3)
        if kind in ("house", "cow"):
            spr = pygame.transform.rotate(spr, (t * (200 if kind == "house" else 260)) % 360)
        elif kind in ("girl", "toto"):
            spr = pygame.transform.rotate(spr, math.sin(t * 3.0 + a0) * 35)
        _full_blit(spr, x, y)


def _full_bolt(seed):
    rnd = random.Random(seed)
    x = rnd.randint(8, FW - 8)
    for y in range(FH):
        for dx in (0, 1):
            if 0 <= x + dx < FW:
                _full.set_at((x + dx, y), FULL)
        x += rnd.choice((-1, 0, 0, 1))
        if y % 5 == 4:
            for ox in (-2, 3):
                if 0 <= x + ox < FW:
                    _full.set_at((x + ox, y), DIM)


def render_full(pos, t):
    """64x48 Surface for all six panels (rows y=0/16/32), while
    oz_story.fullscreen(pos); the caller slices it onto the panels."""
    rgb = bytearray(FW * FH * 3)
    if pos >= oz_story.BOOM_AT:
        # white-out, then the whole picture shatters pixel by pixel into the dark
        d = pos - oz_story.BOOM_AT
        k = 0.0 if d < 0.35 else min(1.0, (d - 0.35) / 2.6)
        for i in range(FW * FH):
            if k == 0.0 or _NOISE[i] > k:
                c = _FULL_RGB if (k == 0.0 or _NOISE2[i] < 0.5) else _DIM_RGB
            else:
                c = _OFF_RGB
            rgb[i * 3:i * 3 + 3] = c
        return pygame.image.frombuffer(bytes(rgb), (FW, FH), "RGB")

    pp = pos - oz_story.CRESCENDO_AT
    u = pp / (oz_story.BOOM_AT - oz_story.CRESCENDO_AT)
    rot = 0.5 * pp + 0.05 * pp * pp               # turns of spin, accelerating
    zoom = 0.9 * pp + 0.14 * pp * pp              # rings rushing outward at the viewer
    shift = rot * 3.0 + zoom
    eye = 2.0 + 9.0 * u * u
    flash = oz_story.lightning(pos) > 0.5
    wh = max(0.0, (u - 0.78) / 0.22)              # white-out creeping in at the very end
    noise_off = int(t * 30) * 97
    n = FW * FH
    for i in range(n):
        r = _POLAR_R[i]
        if r < eye:
            c = _DIM_RGB if _NOISE[(i + noise_off) % n] < 0.07 else _OFF_RGB     # the black eye, a few specks
        elif r < eye + 1.3:
            c = _FULL_RGB                                                          # bright lip of the throat
        else:
            f = (_POLAR_A[i] - shift) % 1.0
            c = _FULL_RGB if f < 0.34 else (_DIM_RGB if f < 0.52 else _OFF_RGB)
        if wh and c != _FULL_RGB and _NOISE[(i + noise_off) % n] < wh:
            c = _FULL_RGB
        elif flash and c is _OFF_RGB and (i + i // FW) % 2 == 0:
            c = _DIM_RGB
        rgb[i * 3:i * 3 + 3] = c
    _full.blit(pygame.image.frombuffer(bytes(rgb), (FW, FH), "RGB"), (0, 0))
    _draw_flyers(pp, rot, t)
    for b in oz_story.BOLTS:
        if oz_story.CRESCENDO_AT <= b and 0 <= pos - b < 0.3 and not (0.09 < pos - b < 0.14):
            _full_bolt(int(b * 10))
    return _full


def _bike_into(target, t):
    """The snatched bike, drawn tiny onto a 12x12 scratch surface."""
    for dx, dy in ((0, 7), (1, 6), (2, 7), (1, 8), (7, 7), (8, 6), (9, 7), (8, 8)):
        target.set_at((dx, dy), FULL)
    for k in range(2, 9):
        target.set_at((k, 5), FULL)
    for k in range(4):
        target.set_at((6, 5 - k), FULL)
    target.set_at((6, 1), FULL)
    target.set_at((7, 1), FULL)
