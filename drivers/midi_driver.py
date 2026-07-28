import time
import threading
import pygame.midi
from config import MIDI_PORT_NAME
from state import state

pygame.midi.init()

midi_out = None
midi_in = None
midi_status_str = "MIDI: DISCONNECTED"

CROSSFADER_CC = 8  # Standard Rekordbox Crossfader CC#

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


def trigger_rekordbox_poke():
    """Sends a MIDI CC#10 Pulse (Note On/Off behavior for Buttons)."""
    send_midi_cc(10, 127)  # Press down
    pygame.time.delay(30)
    send_midi_cc(10, 0)    # Release button


def handle_dj_volume(change):
    """Updates master volume state and streams MIDI CC#11 to Rekordbox."""
    state.music_volume = max(0, min(100, state.music_volume + change))
    midi_val = int((state.music_volume / 100.0) * 127)
    send_midi_cc(11, midi_val)
    print(f"[REKORDBOX MIDI] Volume -> {state.music_volume}% (CC#11 Val:{midi_val})")


def handle_dj_reject():
    """Triggers Rekordbox track skip via CC#10 pulse."""
    print("[REKORDBOX MIDI POKE] Fire 'NEXT TRACK' (CC#10 Val:127)")
    trigger_rekordbox_poke()


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