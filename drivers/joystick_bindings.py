"""drivers/joystick_bindings.py
Configurable joystick button/combo bindings ("Joy Assign" page,
web/static/joyassign.html) -- 2026-08-20: replaces hardcoded raw button
numbers (`if btn == 5: ...`) throughout inputs/gamepad.py, so swapping to a
different physical joystick (different raw button numbering) no longer
needs a code change, just a re-bind from the web remote.

Every actual BEHAVIOR (tap/hold splits, debounce, show-phase overrides,
Space Invaders' "any non-exit button fires" catch-all) stays exactly where
it already lives in inputs/gamepad.py -- this module only owns WHICH raw
button number each named action currently listens for, persisted to disk
so a rebind survives a restart. Deliberately has no dependency on
inputs/gamepad.py (which imports THIS module) -- avoids a circular import,
and keeps this a pure data/lookup layer the same way config.py is, not a
second place actions get dispatched from.

Combos: generalized "N buttons held together continuously for H seconds
fires action X" rule, checked every frame the same way the original
hold-to-fire combos (Force Price Game, Shutdown) already worked -- Space
Invaders' entry combo, previously edge-triggered on the second button's
JOYBUTTONDOWN, is folded into the same polled-hold model with a near-zero
hold time (imperceptible to a human pressing two buttons at once, and lets
every combo -- built-in or user-defined -- share one code path instead of
three bespoke ones)."""
import json
import os
import time

import config

_BINDINGS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "joystick_bindings.json")

# ------------------------------------------------------------
# Action registry -- id -> metadata. kind="button" entries are remappable
# (persisted in _bindings below, looked up by inputs/gamepad.py's
# dispatch); kind="axis" entries are display-only context so the Joy
# Assign page's sections don't look empty (axes are deliberately out of
# scope for remapping) -- gamepad.py never looks these up.
# ------------------------------------------------------------
ACTIONS = [
    # --- Track & Volume ---
    {"id": "axis_next_track", "label": "Next Track", "section": "Track & Volume",
     "kind": "axis", "note": "X- axis (not remappable)"},
    {"id": "axis_prev_track", "label": "Previous Track", "section": "Track & Volume",
     "kind": "axis", "note": "X+ axis (not remappable)"},
    {"id": "axis_volume", "label": "Volume Up / Down", "section": "Track & Volume",
     "kind": "axis", "note": "Y axis / D-pad (not remappable)"},
    {"id": "dj_tempo_tap", "label": "Tempo Tap", "section": "Track & Volume",
     "kind": "button", "modes": ["DJ"], "default": 5},

    # --- Lighting ---
    {"id": "dj_color_cycle", "label": "Color Cycle", "section": "Lighting",
     "kind": "button", "modes": ["DJ"], "default": 9},
    {"id": "dj_theme_cycle", "label": "Theme Cycle", "section": "Lighting",
     "kind": "button", "modes": ["DJ"], "default": 10},

    # --- Trivia & Quiz ---
    {"id": "dj_trivia_pull", "label": "Trivia Pull", "section": "Trivia & Quiz",
     "kind": "button", "modes": ["DJ"], "default": 1},
    {"id": "dj_force_price_game", "label": "Force Price Game", "section": "Trivia & Quiz",
     "kind": "button", "modes": ["DJ"], "default": 6},
    {"id": "dj_swear_toggle", "label": "Swear Tag Toggle", "section": "Trivia & Quiz",
     "kind": "button", "modes": ["DJ"], "default": 2},
    {"id": "dj_auto_announce", "label": "Auto-Announce Toggle (tap) / Status Overlay (hold)",
     "section": "Trivia & Quiz", "kind": "button", "modes": ["DJ"], "default": 0},

    # --- Game Mode ---
    {"id": "game_select_1", "label": "Select Answer 1", "section": "Game Mode",
     "kind": "button", "modes": ["GAME"], "default": 3},
    {"id": "game_select_2", "label": "Select Answer 2", "section": "Game Mode",
     "kind": "button", "modes": ["GAME"], "default": 1},
    {"id": "game_select_3", "label": "Select Answer 3", "section": "Game Mode",
     "kind": "button", "modes": ["GAME"], "default": 2, "note": "also X+ axis"},
    {"id": "game_select_4", "label": "Select Answer 4", "section": "Game Mode",
     "kind": "button", "modes": ["GAME"], "default": 0},
    {"id": "game_clear", "label": "Clear Selection", "section": "Game Mode",
     "kind": "button", "modes": ["GAME"], "default": 5},
    {"id": "game_grade", "label": "Grade Selection", "section": "Game Mode",
     "kind": "button", "modes": ["GAME"], "default": 6},
    {"id": "game_exit_1", "label": "Early Exit (1)", "section": "Game Mode",
     "kind": "button", "modes": ["GAME"], "default": 9},
    {"id": "game_exit_2", "label": "Early Exit (2)", "section": "Game Mode",
     "kind": "button", "modes": ["GAME"], "default": 10},

    # --- Space Invaders ---
    {"id": "si_exit_1", "label": "Exit (1)", "section": "Space Invaders",
     "kind": "button", "modes": ["SPACE_INVADERS"], "default": 9},
    {"id": "si_exit_2", "label": "Exit (2)", "section": "Space Invaders",
     "kind": "button", "modes": ["SPACE_INVADERS"], "default": 10},
    {"id": "si_fire", "label": "Fire", "section": "Space Invaders",
     "kind": "axis", "note": "any button except Exit (not remappable)"},
    {"id": "axis_si_move", "label": "Move Cannon", "section": "Space Invaders",
     "kind": "axis", "note": "X axis / D-pad (not remappable)"},

    # --- Simon ---
    {"id": "simon_select_1", "label": "Pad 1", "section": "Simon",
     "kind": "button", "modes": ["SIMON"], "default": 0},
    {"id": "simon_select_2", "label": "Pad 2", "section": "Simon",
     "kind": "button", "modes": ["SIMON"], "default": 1},
    {"id": "simon_select_3", "label": "Pad 3", "section": "Simon",
     "kind": "button", "modes": ["SIMON"], "default": 2},
    {"id": "simon_select_4", "label": "Pad 4", "section": "Simon",
     "kind": "button", "modes": ["SIMON"], "default": 3},
    {"id": "simon_exit_1", "label": "Exit (1)", "section": "Simon",
     "kind": "button", "modes": ["SIMON"], "default": 9},
    {"id": "simon_exit_2", "label": "Exit (2)", "section": "Simon",
     "kind": "button", "modes": ["SIMON"], "default": 10},
]

_ACTIONS_BY_ID = {a["id"]: a for a in ACTIONS}
REMAPPABLE_IDS = [a["id"] for a in ACTIONS if a["kind"] == "button"]

# "Combo-only" targets: things a combo can fire that aren't also a normal
# single-button action (unlike e.g. dj_tempo_tap, which IS both). Mirrors
# what the three original hardcoded combos actually did.
_COMBO_ONLY_TARGETS = {
    "__space_invaders_entry__": "Enter Space Invaders",
    "__simon_entry__": "Enter Simon",
    "__force_price_game__": "Force Decade Price Game",
    "__shutdown__": "Shutdown",
}


def combo_target_choices():
    """id -> label for every action a combo can fire -- every remappable
    single-button action, plus the combo-only targets above. Used by the
    Joy Assign page's "fires" dropdown when building a new combo."""
    choices = {aid: _ACTIONS_BY_ID[aid]["label"] for aid in REMAPPABLE_IDS}
    choices.update(_COMBO_ONLY_TARGETS)
    return choices


# "modes" gates BOTH when a combo is allowed to fire and when it suppresses
# its buttons' own individual single-press actions while forming -- ["any"]
# means every mode. Space Invaders entry was DJ-only in the original
# hardcoded check (space_invaders_engine.enter_space_invaders() has no
# internal mode guard of its own, so this is the only thing stopping it
# from firing mid-Game-Mode); Force Price Game's original polling loop
# technically ran unconditionally and just relied on force_price_game()'s
# own "if state.mode != MODE_DJ: return" no-op guard, but that had zero
# observable effect outside DJ mode anyway (and let it silently eat a
# GAME-mode Clear+Grade hold without suppressing them) -- gating it here
# too is strictly safer and behaviorally equivalent in practice. Shutdown
# stays mode-independent, matching its original "regardless of mode"
# comment.
_DEFAULT_COMBOS = [
    {"id": "combo_space_invaders_entry", "label": "Space Invaders Entry",
     "buttons": list(config.SI_ENTRY_BUTTONS), "hold_seconds": 0.05,
     "fires": "__space_invaders_entry__", "modes": ["DJ"], "built_in": True},
    {"id": "combo_simon_entry", "label": "Simon Entry",
     "buttons": list(config.SIMON_ENTRY_BUTTONS), "hold_seconds": 0.05,
     "fires": "__simon_entry__", "modes": ["DJ"], "built_in": True},
    {"id": "combo_force_price_game", "label": "Force Decade Price Game",
     "buttons": list(config.FORCE_PRICE_GAME_COMBO_BUTTONS),
     "hold_seconds": config.FORCE_PRICE_GAME_COMBO_HOLD_SECONDS,
     "fires": "__force_price_game__", "modes": ["DJ"], "built_in": True},
    {"id": "combo_shutdown", "label": "Shutdown",
     "buttons": list(config.SHUTDOWN_COMBO_BUTTONS),
     "hold_seconds": config.SHUTDOWN_COMBO_HOLD_SECONDS,
     "fires": "__shutdown__", "modes": ["any"], "built_in": True},
]

_DEFAULT_BINDINGS = {a["id"]: a["default"] for a in ACTIONS if a["kind"] == "button"}

# ------------------------------------------------------------
# Persistence -- plain JSON, same "load once at import, save on every
# change" convention as drivers/light_prefs_engine.py.
# ------------------------------------------------------------
_bindings = dict(_DEFAULT_BINDINGS)
_combos = [dict(c) for c in _DEFAULT_COMBOS]

# Profiles (2026-08-20): named snapshots of _bindings/_combos, so switching
# physical joysticks (different raw button numbering, or just fewer/more
# buttons) is a one-click "load" instead of re-doing every rebind by hand.
# _bindings/_combos above stay the single live/active set at all times --
# a profile is only ever read from on load (copies OUT of the profile INTO
# the live set) or written to on save (copies the CURRENT live set INTO
# the named profile) -- there's no "linked" state to keep in sync, so
# tweaking bindings after loading a profile just diverges from it silently
# until the next explicit Save, same as any other "load a preset" UI.
_active_profile = None  # name of the last profile loaded/saved-as, or None
_profiles = {}  # name -> {"bindings": {...}, "combos": [...]}

# CPU temp overlay trigger (2026-08-20, Joy Assign page): the set of raw
# buttons that must ALL be held for graphics/matrix_canvas.py to draw the
# CPU-temp overlay on panel 5 -- inputs/gamepad.py polls this list against
# real hardware state every frame (same "held, not fired-once" shape as
# the Btn1 status-overlay hold, not the fire-once-then-latch combo model).
# Deliberately NOT part of the combo system above: combos require 2+
# buttons and fire a one-shot action, but this needs to support a single
# button too and has no "fire" step at all, just "show while held."
_cpu_temp_trigger = []


def _sanitized_bindings(raw):
    """Only trust known action ids and int-or-None values -- a stale file
    from a future/older version of this registry shouldn't inject an id
    gamepad.py's dispatch doesn't know what to do with."""
    out = dict(_DEFAULT_BINDINGS)
    for action_id, btn in (raw or {}).items():
        if action_id in _DEFAULT_BINDINGS and (btn is None or isinstance(btn, int)):
            out[action_id] = btn
    return out


def _merged_with_new_builtins(loaded_combos):
    """A saved file's "combos" list fully REPLACES _combos on load (so a
    user's own edits/deletes of a built-in stick) -- but that also means a
    built-in combo added by a later code update (e.g. combo_simon_entry,
    2026-08-20) would silently never appear on any install that already
    has a saved file from before that update. Appends any _DEFAULT_COMBOS
    entry whose id isn't already present, leaving everything else (user
    combos, edited built-ins) untouched."""
    have_ids = {c.get("id") for c in loaded_combos}
    merged = list(loaded_combos)
    for combo in _DEFAULT_COMBOS:
        if combo["id"] not in have_ids:
            merged.append(dict(combo))
    return merged


def _load():
    global _bindings, _combos, _active_profile, _profiles, _cpu_temp_trigger
    try:
        with open(_BINDINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        _bindings = _sanitized_bindings(data.get("bindings", {}))
        loaded_combos = data.get("combos", [])
        if isinstance(loaded_combos, list) and loaded_combos:
            _combos = _merged_with_new_builtins(loaded_combos)
        _profiles = data.get("profiles", {}) or {}
        _active_profile = data.get("active_profile")
        loaded_trigger = data.get("cpu_temp_trigger", [])
        if isinstance(loaded_trigger, list):
            _cpu_temp_trigger = [int(b) for b in loaded_trigger if isinstance(b, int)]
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[JOYSTICK BINDINGS] Could not load {_BINDINGS_PATH}: {e} -- using defaults.")


def _save():
    try:
        with open(_BINDINGS_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "bindings": _bindings, "combos": _combos,
                "active_profile": _active_profile, "profiles": _profiles,
                "cpu_temp_trigger": _cpu_temp_trigger,
            }, f, indent=2)
    except Exception as e:
        print(f"[JOYSTICK BINDINGS] Could not save {_BINDINGS_PATH}: {e}")


_load()


# ------------------------------------------------------------
# Profiles
# ------------------------------------------------------------
def list_profiles():
    return sorted(_profiles.keys())


def active_profile_name():
    return _active_profile


def save_profile(name):
    """Snapshots the CURRENT live bindings+combos under `name` (overwrites
    if that name already exists) and marks it active."""
    global _active_profile
    name = (name or "").strip()
    if not name:
        return False, "Profile needs a name."
    _profiles[name] = {"bindings": dict(_bindings), "combos": [dict(c) for c in _combos]}
    _active_profile = name
    _save()
    return True, None


def load_profile(name):
    """Replaces the live bindings+combos with `name`'s saved snapshot.
    Any unsaved tweaks made since the last save are lost -- same as
    loading any other preset; the web page confirms first."""
    global _bindings, _combos, _active_profile
    profile = _profiles.get(name)
    if profile is None:
        return False, "Profile not found."
    _bindings = _sanitized_bindings(profile.get("bindings", {}))
    loaded_combos = profile.get("combos", [])
    _combos = [dict(c) for c in loaded_combos] if loaded_combos else [dict(c) for c in _DEFAULT_COMBOS]
    _active_profile = name
    _save()
    return True, None


def delete_profile(name):
    global _active_profile
    if name not in _profiles:
        return False, "Profile not found."
    del _profiles[name]
    if _active_profile == name:
        _active_profile = None
    _save()
    return True, None


# ------------------------------------------------------------
# Lookup API -- consumed by inputs/gamepad.py's dispatch.
# ------------------------------------------------------------
def action_for_button(mode, btn):
    """Which remappable action (if any) `btn` currently triggers in
    `mode` ("DJ"/"GAME"/"SPACE_INVADERS"). Returns None if unbound in this
    mode -- same as today's silent no-op for an unmapped physical button."""
    for action_id, bound_btn in _bindings.items():
        if bound_btn != btn:
            continue
        action = _ACTIONS_BY_ID.get(action_id)
        if action and mode in action.get("modes", []):
            return action_id
    return None


def button_for(action_id):
    return _bindings.get(action_id)


def buttons_for(action_ids):
    """The set of currently-bound physical buttons for a list of action
    ids (e.g. si_exit_1/si_exit_2) -- unbound ones are skipped, not None."""
    return {b for a in action_ids if (b := _bindings.get(a)) is not None}


def cpu_temp_trigger():
    return list(_cpu_temp_trigger)


def set_cpu_temp_trigger(buttons):
    global _cpu_temp_trigger
    _cpu_temp_trigger = [int(b) for b in buttons]
    _save()


def set_binding(action_id, btn):
    if action_id not in _DEFAULT_BINDINGS:
        return False
    _bindings[action_id] = int(btn)
    _save()
    return True


def unbind_action(action_id):
    """Clears an action's binding entirely (e.g. "retire" the Game Mode
    answer-select buttons if you're not using the physical console for
    that anymore) -- action_for_button() already treats None the same as
    an always-unmapped button, silent no-op on press, no special-casing
    needed anywhere else."""
    if action_id not in _DEFAULT_BINDINGS:
        return False
    _bindings[action_id] = None
    _save()
    return True


def reset_to_defaults():
    """Restores the LIVE bindings/combos to the original hardcoded
    defaults -- deliberately does NOT touch saved profiles (those are
    presets the operator explicitly created; a factory reset of what's
    currently active shouldn't silently delete them)."""
    global _bindings, _combos
    _bindings = dict(_DEFAULT_BINDINGS)
    _combos = [dict(c) for c in _DEFAULT_COMBOS]
    _save()


def all_bindings():
    """Every action (remappable or not) with its current button/note, for
    the Joy Assign page -- grouped by section, in registry order."""
    out = []
    for a in ACTIONS:
        entry = dict(a)
        if a["kind"] == "button":
            entry["button"] = _bindings.get(a["id"])
        out.append(entry)
    return out


def combos():
    return [dict(c) for c in _combos]


def add_combo(label, buttons, hold_seconds, fires, modes=None):
    if len(buttons) < 2:
        return False, "A combo needs at least 2 buttons."
    if fires not in combo_target_choices():
        return False, "Unknown target action."
    combo_id = f"combo_custom_{int(time.time() * 1000)}"
    _combos.append({
        "id": combo_id, "label": label or "Custom Combo",
        "buttons": [int(b) for b in buttons],
        "hold_seconds": max(0.0, float(hold_seconds)),
        "fires": fires, "modes": modes or ["any"], "built_in": False,
    })
    _save()
    return True, combo_id


def update_combo(combo_id, buttons, hold_seconds=None):
    """Rebinds an EXISTING combo's (built-in OR custom) trigger buttons in
    place -- what "Rebind" on a Joy Assign combo card calls. Unlike
    add_combo() (always creates a new entry) this changes the combo's own
    "buttons" list, so a built-in like combo_simon_entry can move off its
    hardcoded default without a code change/redeploy. Deliberately doesn't
    touch "fires" -- what a built-in DOES stays fixed, only which buttons
    trigger it is reconfigurable, matching delete_combo()'s "can't delete,
    but nothing stops you rebinding it" stance below."""
    if len(buttons) < 2:
        return False, "A combo needs at least 2 buttons."
    match = next((c for c in _combos if c["id"] == combo_id), None)
    if match is None:
        return False, "Combo not found."
    match["buttons"] = [int(b) for b in buttons]
    if hold_seconds is not None:
        match["hold_seconds"] = max(0.0, float(hold_seconds))
    _save()
    return True, None


def delete_combo(combo_id):
    global _combos
    match = next((c for c in _combos if c["id"] == combo_id), None)
    if match is None:
        return False, "Combo not found."
    if match.get("built_in"):
        return False, "Built-in combos can't be deleted -- rebind their buttons instead."
    _combos = [c for c in _combos if c["id"] != combo_id]
    _save()
    return True, None


# ------------------------------------------------------------
# Capture mode ("press-to-bind") -- armed by the Joy Assign page, consumed
# by inputs/gamepad.py's JOYBUTTONDOWN handler, polled by the web remote.
# Module-level, not state.py -- same "driver owns its own runtime state"
# convention as drivers/led_bridge.py, since only this module and
# gamepad.py's event loop ever touch it.
# ------------------------------------------------------------
_capture_armed = False
_capture_result = None  # last captured raw button index, or None


def start_capture():
    global _capture_armed, _capture_result
    _capture_armed = True
    _capture_result = None


def cancel_capture():
    global _capture_armed
    _capture_armed = False


def capture_is_armed():
    return _capture_armed


def offer_capture(btn):
    """Called from inputs/gamepad.py's JOYBUTTONDOWN handler for EVERY
    press while capture mode is armed, before any normal action dispatch.
    Returns True if this press was consumed by capture (caller should NOT
    also dispatch it as a normal action this event)."""
    global _capture_armed, _capture_result
    if not _capture_armed:
        return False
    _capture_result = btn
    _capture_armed = False
    return True


def capture_result():
    """Poll target for the web remote -- returns the captured button index
    once offer_capture() has consumed a press, else None (still waiting)."""
    return _capture_result


def clear_capture_result():
    global _capture_result
    _capture_result = None
