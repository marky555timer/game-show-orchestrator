import time
import pygame.midi

pygame.midi.init()

print("\n--- AVAILABLE MIDI INPUT DEVICES ---")
count = pygame.midi.get_count()
for i in range(count):
    info = pygame.midi.get_device_info(i)
    name = info[1].decode('utf-8', errors='ignore')
    is_input = info[2] == 1
    if is_input and 'loopMIDI' not in name and 'Python_PMC' not in name:
        print(f"ID {i}: '{name}'")

# Prompt for the ID number from the list above
dev_id = int(input("\nEnter the ID number for your physical DJ controller: "))
midi_in = pygame.midi.Input(dev_id)

print("\nListening for fader movements... Move your CROSSFADER now! (Press Ctrl+C to stop)")
try:
    while True:
        if midi_in.poll():
            events = midi_in.read(10)
            for event in events:
                data = event[0]
                status, cc_num, val = data[0], data[1], data[2]
                if (status & 0xF0) == 0xB0:  # Control Change
                    print(f"[MIDI EVENT] Status: {hex(status)} | CC#{cc_num} | Value: {val}")
        time.sleep(0.01)
except KeyboardInterrupt:
    print("\nTest stopped.")