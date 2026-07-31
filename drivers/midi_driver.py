import time
import threading
import pygame.midi
from config import (
    MIDI_PORT_NAME, VOLUME_OVERLAY_HOLD_SECONDS,
    # MIDI_DECK_CUE_NOTE, MIDI_DECK_PLAY_NOTE, DECK_START_SEQUENCE_TICK_MS,
    # unused now that send_deck_start_sequence()/send_deck_stop_sequence()
    # are disabled below -- left commented for easy re-enable.
)
from state import state

pygame.midi.init()

midi_out = None
midi_in = None
midi_status_str = "MIDI: DISCONNECTED"

CROSSFADER_CC = 8  # Standard Rekordbox Crossfader CC#

# Channel 1/2 faders -- the same CC#11/CC#12 handle_dj_volume() already
# drives as the "master volume" pair.
CHANNEL1_FADER_CC = 11
CHANNEL2_FADER_CC = 12

# Last commanded fader level (0-100%), tracked independently of
# state.music_volume so a Price Game duck/restore tween can run without
# clobbering the DJ's actual volume setting -- outside of an active tween
# this always mirrors state.music_volume.
_current_fader_pct = 100.0
_fader_tween = None  # {"start_pct", "target_pct", "start_time", "duration"}

def connect_midi():
    """Scans Pygame MIDI outputs and inputs to connect loopMIDI / Python_PMC_Port."""
    global midi_out, midi_in, midi_status_str
    
    count = pygame.midi.get_count()
    out_target_id = None
    in_target_id = None
    
    for i in range(count):
        info = pygame.midi.get_device_info(i)
        name = info[1].decode('utf-8', errors='ignore')
        is_output = info[3] == 1
        is_input = info[2] == 1
        
        if MIDI_PORT_NAME in name or 'loopMIDI' in name:
            if is_output and out_target_id is None:
                out_target_id = i
                print(f"[MIDI SCAN] Found target output on ID {i}: '{name}'")
            elif is_input and in_target_id is None:
                in_target_id = i
                print(f"[MIDI SCAN] Found target input on ID {i}: '{name}'")

    # Connect Output
    if out_target_id is not None:
        try:
            if midi_out:
                midi_out.close()
            midi_out = pygame.midi.Output(out_target_id)
            midi_status_str = "MIDI: PORT ONLINE"
            print(f"[MIDI SUCCESS] Connected Output to device ID {out_target_id}")
        except Exception as e:
            midi_status_str = "MIDI: OPEN FAIL"
            print(f"[MIDI ERROR] Could not open output device ID {out_target_id}: {e}")
    else:
        midi_status_str = "MIDI: PORT NOT FOUND"
        print(f"[MIDI WARNING] Could not find '{MIDI_PORT_NAME}' in outputs.")

    # Connect Input
    if in_target_id is not None:
        try:
            if midi_in:
                midi_in.close()
            midi_in = pygame.midi.Input(in_target_id)
            print(f"[MIDI SUCCESS] Connected Input to device ID {in_target_id}")
        except Exception as e:
            print(f"[MIDI ERROR] Could not open input device ID {in_target_id}: {e}")

# Auto-connect on import
connect_midi()


def send_midi_cc(control, value):
    """Sends a raw 3-byte MIDI Control Change (0xB0, control, value)."""
    global midi_status_str
    if midi_out:
        try:
            midi_out.write_short(0xB0, int(control), int(value))
            midi_status_str = f"MIDI OUT: CC#{control} VAL:{value}"
        except Exception as e:
            midi_status_str = "MIDI: TX ERROR"
            print(f"[MIDI TX ERROR] {e}")


def send_cc_pulse(control, delay_ms=30):
    """Sends a press-then-release pulse (val 127 then 0) on a MIDI CC --
    the shape Rekordbox-style controller mappings expect for a momentary
    button. Used by the deck-switch TrackSearch triggers in
    drivers/deck_orchestrator.py."""
    send_midi_cc(control, 127)
    pygame.time.delay(delay_ms)
    send_midi_cc(control, 0)


def send_midi_note(status, note, velocity):
    """Sends a raw 3-byte MIDI Note On/Off message (status is the full
    0x9n byte -- channel is baked in, unlike send_midi_cc's fixed 0xB0).
    Used for the PMC-port Cue/Play-Pause deck-start sequence, which is
    mapped as Note messages rather than Control Change."""
    global midi_status_str
    if midi_out:
        try:
            midi_out.write_short(int(status), int(note), int(velocity))
            midi_status_str = f"MIDI OUT: NOTE {status:#04x}/{note} VAL:{velocity}"
        except Exception as e:
            midi_status_str = "MIDI: TX ERROR"
            print(f"[MIDI TX ERROR] {e}")


def send_note_pulse(status, note, delay_ms=30):
    """Press-then-release Note On/Off pulse (velocity 127 then 0) -- the
    Note-message analog of send_cc_pulse(), for momentary buttons (Cue,
    Play/Pause) mapped as PMC ports rather than CCs."""
    send_midi_note(status, note, 127)
    pygame.time.delay(delay_ms)
    send_midi_note(status, note, 0)


def send_deck_start_sequence(deck):
    """DISABLED: Deck pause/search glitch "fix" that primed the target deck
    with a Cue -> tick -> Play/Pause Note sequence on its PMC ports
    (900C/900B for Deck 1, 910C/910B for Deck 2) before TrackSearch. This
    was found to interfere with the playback engine and cause glitches
    during Next/Previous track navigation, so it's been turned into a
    no-op. Deck transitions now rely strictly on internal audio file
    loading / state tracking (see drivers/deck_orchestrator.py) rather
    than dispatching these MIDI Cue/Play-Pause messages. Left in place
    (rather than deleted) so it's easy to re-enable if the underlying
    Rekordbox/PMC-port issue is ever fixed.
    """
    # cue_status, cue_note = MIDI_DECK_CUE_NOTE[deck]
    # play_status, play_note = MIDI_DECK_PLAY_NOTE[deck]
    # send_note_pulse(cue_status, cue_note)
    # pygame.time.delay(DECK_START_SEQUENCE_TICK_MS)
    # send_note_pulse(play_status, play_note)
    return


def send_deck_stop_sequence(deck):
    """DISABLED: Post-crossfade deck cleanup that sent the same Cue ->
    tick -> Play/Pause Note pair as send_deck_start_sequence() to force the
    outgoing deck stopped/cued/inert after a crossfade. Disabled for the
    same reason -- these Cue/Play-Pause MIDI sends were interfering with
    the playback engine during deck transitions. No-op now; see
    send_deck_start_sequence() above.
    """
    # cue_status, cue_note = MIDI_DECK_CUE_NOTE[deck]
    # play_status, play_note = MIDI_DECK_PLAY_NOTE[deck]
    # send_note_pulse(cue_status, cue_note)
    # pygame.time.delay(DECK_START_SEQUENCE_TICK_MS)
    # send_note_pulse(play_status, play_note)
    return


def handle_dj_volume(change):
    """Updates master volume state and streams MIDI CC#11 to Rekordbox."""
    global _current_fader_pct, _fader_tween
    state.music_volume = max(0, min(100, state.music_volume + change))
    state.vol_overlay_until = time.time() + VOLUME_OVERLAY_HOLD_SECONDS
    midi_val = int((state.music_volume / 100.0) * 127)
    send_midi_cc(11, midi_val)
    send_midi_cc(12, midi_val)
    # A manual volume nudge always wins over an in-progress Price Game
    # duck/restore tween.
    cancel_fader_tween()
    _current_fader_pct = float(state.music_volume)
    print(f"[REKORDBOX MIDI] Volume -> {state.music_volume}% (CC#11 Val:{midi_val})")


def _set_channel_faders_raw(pct):
    """Immediately sets both channel faders (CC#11/CC#12) to `pct` (0-100),
    with no tween."""
    midi_val = int(max(0, min(100, pct)) / 100.0 * 127)
    send_midi_cc(CHANNEL1_FADER_CC, midi_val)
    send_midi_cc(CHANNEL2_FADER_CC, midi_val)


def cancel_fader_tween():
    """Kills whatever channel1/channel2 fader tween is currently in flight,
    if any -- called at the top of tween_channel_faders_to() so a new duck/
    restore command can never overlap a stale one (the stuttering/glitch
    failure mode this guards against). _current_fader_pct is left exactly
    where the killed tween left off, so the next tween starts smoothly from
    there rather than snapping."""
    global _fader_tween
    _fader_tween = None


def tween_channel_faders_to(target_pct, duration_seconds):
    """Smoothly tweens both channel faders from their current commanded
    level to target_pct (0-100%) over duration_seconds -- non-blocking,
    advanced a step per frame by update_fader_tween(). Used by
    drivers/price_game_engine.py to duck to 0% on Price Game entry and
    restore back to state.music_volume when it ends. Always cancels any
    tween already in flight first, so overlapping duck/restore calls can
    never fight each other."""
    global _fader_tween
    cancel_fader_tween()
    _fader_tween = {
        "start_pct": _current_fader_pct,
        "target_pct": float(target_pct),
        "start_time": time.time(),
        "duration": duration_seconds,
    }


def update_fader_tween(now):
    """Per-frame pump for tween_channel_faders_to() -- called from
    drivers/price_game_engine.py::update()."""
    global _fader_tween, _current_fader_pct
    if _fader_tween is None:
        return

    duration = _fader_tween["duration"]
    elapsed = now - _fader_tween["start_time"]
    phase = min(1.0, elapsed / duration) if duration > 0 else 1.0
    start_pct = _fader_tween["start_pct"]
    target_pct = _fader_tween["target_pct"]
    pct = start_pct + (target_pct - start_pct) * phase

    _current_fader_pct = pct
    _set_channel_faders_raw(pct)

    if phase >= 1.0:
        _fader_tween = None


def _midi_input_listener_loop():
    """Background listener loop that polls pygame.midi Input for crossfader changes."""
    global midi_in
    while True:
        try:
            if midi_in and midi_in.poll():
                midi_events = midi_in.read(10)
                for event in midi_events:
                    # event structure: [[status, data1, data2, data3], timestamp]
                    data = event[0]
                    status_byte = data[0]
                    control_num = data[1]
                    value_num = data[2]

                    # 0xB0 = Control Change
                    if (status_byte & 0xF0) == 0xB0 and control_num == CROSSFADER_CC:
                        # Crossfader Threshold: > 71 triggers Deck 2, <= 71 triggers Deck 1
                        if value_num > 71:
                            if state.active_deck != 2:
                                state.active_deck = 2
                                print(f"[MIDI IN] Crossfader CC#{control_num} Val:{value_num} -> Active Deck: 2")
                        else:
                            if state.active_deck != 1:
                                state.active_deck = 1
                                print(f"[MIDI IN] Crossfader CC#{control_num} Val:{value_num} -> Active Deck: 1")

        except Exception as e:
            print(f"[MIDI IN ERROR] {e}")

        time.sleep(0.01)

# Start background input polling thread
input_thread = threading.Thread(target=_midi_input_listener_loop, daemon=True)
input_thread.start()