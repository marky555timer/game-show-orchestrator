"""drivers/player_profile.py
Team names, emoji avatars and speed-scaled points for phone players.

A player is a team (a lone player is just a team of one). The phone sends a free
team name and one emoji picked from its own keyboard; the panels can only draw
plain ASCII, so every name also gets an LED-safe form.
"""
import re
import unicodedata

import config
from state import state

NAME_MAX = 20          # characters kept from what a phone sends
LED_MAX = 12           # characters the panels get
DEFAULT_EMOJI = "\U0001F3B5"   # musical note
_EMOJI_MAX_CODEPOINTS = 12     # a family / flag / skin-tone emoji is a few code points
_BAD_CHARS = re.compile(r"[<>&\"'\\\x00-\x1f\x7f]")
_ASCII_WORDCHAR = re.compile(r"[A-Za-z0-9<>&\"'\\\s]")


def clean_name(raw):
    """The team name as shown on phones: trimmed, single-spaced, no markup characters."""
    text = _BAD_CHARS.sub("", str(raw or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:NAME_MAX].strip()


def led_name(name):
    """ASCII, uppercase, at most LED_MAX characters, for the LED panels."""
    folded = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii")
    folded = re.sub(r"[^A-Za-z0-9 .&'!?-]", "", folded)
    folded = re.sub(r"\s+", " ", folded).strip().upper()
    return folded[:LED_MAX].strip()


def make_names(raw):
    """-> (name, initials) for a typed team name; falls back to a placeholder."""
    name = clean_name(raw)
    initials = led_name(name)
    if not name:
        name = "Team"
    if not initials:
        initials = "TEAM"      # an all-emoji name still needs something the panels can show
    return name, initials


def clean_emoji(raw, fallback=DEFAULT_EMOJI):
    """One emoji (possibly several code points joined into one picture), or the fallback."""
    text = str(raw or "").strip()
    if not text:
        return fallback
    text = text[:_EMOJI_MAX_CODEPOINTS * 2]
    # Keep the first picture: stop at the first plain letter/digit/space/markup character.
    out = []
    for ch in text:
        if _ASCII_WORDCHAR.match(ch):
            break
        out.append(ch)
        if len(out) >= _EMOJI_MAX_CODEPOINTS:
            break
    emoji = "".join(out).strip()
    if not emoji or not any(ord(c) > 0x2000 for c in emoji):
        return fallback
    return emoji


def speed_points(locked_at, deadline_at, timeout_seconds, multiplier=1):
    """What a right answer locked at `locked_at` is worth: SPEED_POINTS_MAX when the
    question opened, sliding down to SPEED_POINTS_MIN at the deadline."""
    if timeout_seconds and timeout_seconds > 0 and deadline_at and locked_at:
        frac = max(0.0, min(1.0, (deadline_at - locked_at) / timeout_seconds))
    else:
        frac = 0.5
    pts = config.SPEED_POINTS_MIN + (config.SPEED_POINTS_MAX - config.SPEED_POINTS_MIN) * frac
    return int(round(pts * multiplier))


def points_range(multiplier=1):
    return (int(config.SPEED_POINTS_MIN * multiplier), int(config.SPEED_POINTS_MAX * multiplier))


def phone_active_text():
    """(question, choices) wording for phones of the question live right now."""
    if (state.factoid_question_long and state.factoid_long_pair == state.factoid_question
            and len(state.factoid_choices_long) == len(state.factoid_choices)):
        return state.factoid_question_long, state.factoid_choices_long
    return state.factoid_question, state.factoid_choices


def round_multiplier():
    """Points multiplier for the live question ("Who is this?" is worth more; so is a price question
    in a "Wait, how much?" round, so the round can be won in about 10 questions)."""
    if state.factoid_category == "identify_band":
        return config.IDENTIFY_POINTS_MULT
    if state.factoid_category == "price_game" and state.round_type == "wait_how_much":
        return config.ROUND_PRICE_POINTS_MULT
    return 1
