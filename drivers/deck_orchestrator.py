import time
from config import (
    MIDI_CC_DECK1_NEXT, MIDI_CC_DECK1_BACK, MIDI_CC_DECK2_NEXT, MIDI_CC_DECK2_BACK,
    MIDI_CC_CROSSFADER, TRACK_LOAD_WAIT_SECONDS, CROSSFADER_TWEEN_DURATION_SECONDS,
)
from state import state
from drivers.midi_driver import send_cc_pulse, send_midi_cc, send_deck_start_sequence
from drivers.branding_engine import notify_deck_change

_NEXT_CC = {1: MIDI_CC_DECK1_NEXT, 2: MIDI_CC_DECK2_NEXT}
_BACK_CC = {1: MIDI_CC_DECK1_BACK, 2: MIDI_CC_DECK2_BACK}
_DECK_EXTREME = {1: 0, 2: 127}  # crossfader CC value fully favoring each deck

# The single in-flight deck-move object, or None. Triggering a new move
# always overwrites this outright -- that IS the "kill the active tween"
# safeguard; a half-finished move is simply discarded, never allowed to
# complete, in favor of the fresh 3-second buffer sequence.
_pending = None


def trigger_track_move(direction):
    """direction: "next" or "back". Sends a TrackSearch pulse to the
    INACTIVE deck, waits TRACK_LOAD_WAIT_SECONDS for Rekordbox to load the
    track, then tweens the crossfader over to that deck."""
    global _pending

    active = state.active_deck
    inactive = 2 if active == 1 else 1

    # Deck pause/search glitch fix: prime the target deck (Cue -> tick ->
    # Play/Pause on its PMC ports) before TrackSearch, so a paused/unstarted
    # deck doesn't silently no-op the search.
    try:
        send_deck_start_sequence(inactive)
    except Exception as e:
        print(f"[DECK ORCHESTRATOR] Failed to send deck-start sequence: {e}")

    cc_table = _NEXT_CC if direction == "next" else _BACK_CC
    cc = cc_table[inactive]

    try:
        send_cc_pulse(cc)
    except Exception as e:
        print(f"[DECK ORCHESTRATOR] Failed to send TrackSearch pulse: {e}")

    print(f"[DECK ORCHESTRATOR] {direction.upper()} -> TrackSearch on Deck {inactive} "
          f"(CC#{cc}), {TRACK_LOAD_WAIT_SECONDS}s load wait, then crossfade to Deck {inactive}")

    state.deck_change_count += 1
    notify_deck_change()

    _pending = {
        "phase": "waiting_load",
        "target_deck": inactive,
        "start_time": time.time(),
        "tween_start_val": None,
        "tween_target_val": None,
        "tween_start_time": None,
    }


def update(now):
    """Per-frame pump -- called once per frame from
    inputs/gamepad.py::process_events()."""
    global _pending
    if _pending is None:
        return

    if _pending["phase"] == "waiting_load":
        if now - _pending["start_time"] >= TRACK_LOAD_WAIT_SECONDS:
            target_deck = _pending["target_deck"]
            current_deck = state.active_deck
            _pending["tween_start_val"] = _DECK_EXTREME[current_deck]
            _pending["tween_target_val"] = _DECK_EXTREME[target_deck]
            _pending["tween_start_time"] = now
            _pending["phase"] = "tweening"

    if _pending is not None and _pending["phase"] == "tweening":
        elapsed = now - _pending["tween_start_time"]
        duration = CROSSFADER_TWEEN_DURATION_SECONDS
        phase = min(1.0, elapsed / duration) if duration > 0 else 1.0
        start_val = _pending["tween_start_val"]
        target_val = _pending["tween_target_val"]
        value = int(start_val + (target_val - start_val) * phase)

        try:
            send_midi_cc(MIDI_CC_CROSSFADER, value)
        except Exception as e:
            print(f"[DECK ORCHESTRATOR] Crossfader tween CC send failed: {e}")

        if phase >= 1.0:
            state.active_deck = _pending["target_deck"]
            print(f"[DECK ORCHESTRATOR] Crossfade complete -> Active Deck: {state.active_deck}")
            _pending = None


def has_pending_move():
    return _pending is not None
