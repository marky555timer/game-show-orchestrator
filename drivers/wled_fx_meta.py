"""Per-effect metadata for the outline board's WLED 16.0.1 effects, generated
2026-10-03 from WLED v16.0.1 wled00/FX.cpp (each effect's _data_FX_MODE_* string,
4th ";" field). Keyed by WLED effect id = index into config.ACCENT_EFFECT_NAMES.

dims: which layouts the effect is written for -- "0" single pixel, "1" strip, "2"
matrix. The outline is one 1D strip, so effects without "0"/"1" are matrix-only.
sound: "v" = reacts to volume (WLED UI note icon), "f" = reacts to frequency
(FFT, double-note icon), "" = not sound-reactive. Ids missing here are WLED's
empty "RSVD" slots (removed effects)."""

FX_META = {
    0: ('01', ''),  # Solid
    1: ('01', ''),  # Blink
    2: ('01', ''),  # Breathe
    3: ('1', ''),  # Wipe
    4: ('1', ''),  # Wipe Random
    5: ('01', ''),  # Random Colors
    6: ('1', ''),  # Sweep
    7: ('1', ''),  # Dynamic
    8: ('01', ''),  # Colorloop
    9: ('1', ''),  # Rainbow
    10: ('1', ''),  # Scan
    11: ('1', ''),  # Scan Dual
    12: ('01', ''),  # Fade
    13: ('1', ''),  # Theater
    14: ('1', ''),  # Theater Rainbow
    15: ('1', ''),  # Running
    16: ('1', ''),  # Saw
    17: ('1', ''),  # Twinkle
    18: ('1', ''),  # Dissolve
    19: ('1', ''),  # Dissolve Rnd
    20: ('1', ''),  # Sparkle
    21: ('1', ''),  # Sparkle Dark
    22: ('1', ''),  # Sparkle+
    23: ('01', ''),  # Strobe
    24: ('01', ''),  # Strobe Rainbow
    25: ('01', ''),  # Strobe Mega
    26: ('01', ''),  # Blink Rainbow
    27: ('1', ''),  # Android
    28: ('1', ''),  # Chase
    29: ('1', ''),  # Chase Random
    30: ('1', ''),  # Chase Rainbow
    31: ('1', ''),  # Chase Flash
    32: ('1', ''),  # Chase Flash Rnd
    33: ('1', ''),  # Rainbow Runner
    34: ('1', ''),  # Colorful
    35: ('1', ''),  # Traffic Light
    36: ('1', ''),  # Sweep Random
    37: ('1', ''),  # Chase 2
    38: ('1', ''),  # Aurora
    39: ('1', ''),  # Stream
    40: ('1', ''),  # Scanner
    41: ('1', ''),  # Lighthouse
    42: ('12', ''),  # Fireworks
    43: ('12', ''),  # Rain
    44: ('1', ''),  # Tetrix
    45: ('01', ''),  # Fire Flicker
    46: ('1', ''),  # Gradient
    47: ('1', ''),  # Loading
    48: ('1', ''),  # Rolling Balls
    49: ('1', ''),  # Fairy
    50: ('1', ''),  # Two Dots
    51: ('1', ''),  # Fairytwinkle
    52: ('1', ''),  # Running Dual
    53: ('12', ''),  # Image
    54: ('1', ''),  # Chase 3
    55: ('1', ''),  # Tri Wipe
    56: ('1', ''),  # Tri Fade
    57: ('1', ''),  # Lightning
    58: ('1', ''),  # ICU
    59: ('1', ''),  # Multi Comet
    60: ('1', ''),  # Scanner Dual
    61: ('1', ''),  # Stream 2
    62: ('1', ''),  # Oscillate
    63: ('1', ''),  # Pride 2015
    64: ('1', ''),  # Juggle
    65: ('12', ''),  # Palette
    66: ('1', ''),  # Fire 2012
    67: ('1', ''),  # Colorwaves
    68: ('1', ''),  # Bpm
    69: ('1', ''),  # Fill Noise
    70: ('1', ''),  # Noise 1
    71: ('1', ''),  # Noise 2
    72: ('1', ''),  # Noise 3
    73: ('1', ''),  # Noise 4
    74: ('1', ''),  # Colortwinkles
    75: ('1', ''),  # Lake
    76: ('1', ''),  # Meteor
    77: ('12', ''),  # Copy Segment
    78: ('1', ''),  # Railway
    79: ('12', ''),  # Ripple
    80: ('1', ''),  # Twinklefox
    81: ('1', ''),  # Twinklecat
    82: ('12', ''),  # Halloween Eyes
    83: ('1', ''),  # Solid Pattern
    84: ('1', ''),  # Solid Pattern Tri
    85: ('1', ''),  # Spots
    86: ('1', ''),  # Spots Fade
    87: ('1', ''),  # Glitter
    88: ('01', ''),  # Candle
    89: ('1', ''),  # Fireworks Starburst
    90: ('12', ''),  # Fireworks 1D
    91: ('1', ''),  # Bouncing Balls
    92: ('1', ''),  # Sinelon
    93: ('1', ''),  # Sinelon Dual
    94: ('1', ''),  # Sinelon Rainbow
    95: ('1', ''),  # Popcorn
    96: ('1', ''),  # Drip
    97: ('1', ''),  # Plasma
    98: ('1', ''),  # Percent
    99: ('12', ''),  # Ripple Rainbow
    100: ('01', ''),  # Heartbeat
    101: ('1', ''),  # Pacifica
    102: ('1', ''),  # Candle Multi
    103: ('1', ''),  # Solid Glitter
    104: ('1', ''),  # Sunrise
    105: ('1', ''),  # Phased
    106: ('1', ''),  # Twinkleup
    107: ('1', ''),  # Noise Pal
    108: ('1', ''),  # Sine
    109: ('1', ''),  # Phased Noise
    110: ('1', ''),  # Flow
    111: ('1', ''),  # Chunchun
    112: ('1', ''),  # Dancing Shadows
    113: ('1', ''),  # Washing Machine
    114: ('2', ''),  # Rotozoomer
    115: ('1', ''),  # Blends
    116: ('01', ''),  # TV Simulator
    117: ('1', ''),  # Dynamic Smooth
    118: ('2', ''),  # Spaceships
    119: ('2', ''),  # Crazy Bees
    120: ('2', ''),  # Ghost Rider
    121: ('2', ''),  # Blobs
    122: ('2', ''),  # Scrolling Text
    123: ('2', ''),  # Drift Rose
    124: ('2', ''),  # Distortion Waves
    125: ('2', ''),  # Soap
    126: ('2', ''),  # Octopus
    127: ('2', ''),  # Waving Cell
    128: ('1', 'v'),  # Pixels
    129: ('1', 'v'),  # Pixelwave
    130: ('01', 'v'),  # Juggles
    131: ('1', 'v'),  # Matripix
    132: ('1', 'v'),  # Gravimeter
    133: ('01', 'v'),  # Plasmoid
    134: ('1', 'v'),  # Puddles
    135: ('1', 'v'),  # Midnoise
    136: ('1', 'v'),  # Noisemeter
    137: ('01', 'f'),  # Freqwave
    138: ('01', 'f'),  # Freqmatrix
    139: ('2', 'f'),  # GEQ
    140: ('01', 'f'),  # Waterfall
    141: ('1', 'f'),  # Freqpixels
    143: ('01', 'v'),  # Noisefire
    144: ('1', 'v'),  # Puddlepeak
    145: ('01', 'f'),  # Noisemove
    146: ('2', ''),  # Noise2D
    147: ('1', ''),  # Perlin Move
    148: ('1', 'v'),  # Ripple Peak
    149: ('2', ''),  # Firenoise
    150: ('2', ''),  # Squared Swirl
    151: ('1', ''),  # PacMan
    152: ('2', ''),  # DNA
    153: ('2', ''),  # Matrix
    154: ('2', ''),  # Metaballs
    155: ('1', 'f'),  # Freqmap
    156: ('1', 'v'),  # Gravcenter
    157: ('1', 'v'),  # Gravcentric
    158: ('1', 'f'),  # Gravfreq
    159: ('01', 'f'),  # DJ Light
    160: ('2', 'f'),  # Funky Plank
    161: ('1', ''),  # Shimmer
    162: ('2', ''),  # Pulser
    163: ('1', 'f'),  # Blurz
    164: ('2', ''),  # Drift
    165: ('2', 'v'),  # Waverly
    166: ('2', ''),  # Sun Radiation
    167: ('2', ''),  # Colored Bursts
    168: ('2', ''),  # Julia
    172: ('2', ''),  # Game Of Life
    173: ('2', ''),  # Tartan
    174: ('2', ''),  # Polar Lights
    175: ('2', 'v'),  # Swirl
    176: ('2', ''),  # Lissajous
    177: ('2', ''),  # Frizzles
    178: ('2', ''),  # Plasma Ball
    179: ('11', ''),  # Flow Stripe
    180: ('2', ''),  # Hiphotic
    181: ('2', ''),  # Sindots
    182: ('2', ''),  # DNA Spiral
    183: ('2', ''),  # Black Hole
    184: ('1', ''),  # Wavesins
    185: ('01', 'f'),  # Rocktaves
    186: ('2', 'f'),  # Akemi
    187: ('2', ''),  # PS Volcano
    188: ('2', ''),  # PS Fire
    189: ('2', ''),  # PS Fireworks
    190: ('2', ''),  # PS Vortex
    191: ('2', ''),  # PS Fuzzy Noise
    192: ('2', ''),  # PS Ballpit
    193: ('2', ''),  # PS Box
    194: ('2', ''),  # PS Attractor
    195: ('2', ''),  # PS Impact
    196: ('2', ''),  # PS Waterfall
    197: ('2', 'v'),  # PS Spray
    198: ('2', 'f'),  # PS GEQ 2D
    199: ('2', 'f'),  # PS GEQ Nova
    200: ('2', ''),  # PS Ghost Rider
    201: ('2', 'v'),  # PS Blobs
    202: ('1', ''),  # PS DripDrop
    203: ('1', ''),  # PS Pinball
    204: ('1', ''),  # PS Dancing Shadows
    205: ('1', ''),  # PS Fireworks 1D
    206: ('1', ''),  # PS Sparkler
    207: ('1', ''),  # PS Hourglass
    208: ('1', ''),  # PS Spray 1D
    209: ('1', ''),  # PS 1D Balance
    210: ('1', ''),  # PS Chase
    211: ('1', ''),  # PS Starburst
    212: ('1', 'f'),  # PS GEQ 1D
    213: ('1', ''),  # PS Fire 1D
    214: ('1', 'f'),  # PS Sonic Stream
    215: ('1', 'f'),  # PS Sonic Boom
    216: ('1', 'f'),  # PS Springy
    217: ('2', ''),  # PS Galaxy
    218: ('1', ''),  # Color Clouds
    219: ('1', ''),  # Slow Transition
}


def is_strip_effect(fx):
    meta = FX_META.get(fx)
    return bool(meta) and ("0" in meta[0] or "1" in meta[0])
