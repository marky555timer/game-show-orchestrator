import serial
from config import ENTTEC_PORT

class EnttecDMXPro:
    START_VAL = 0x7E
    END_VAL = 0xE7
    SEND_DMX_LABEL = 6

    def __init__(self, com_port, num_channels=512):
        self.port = com_port
        self.num_channels = num_channels
        self.dmx_data = bytearray(num_channels + 1)
        self.active = False
        
        try:
            self.serial = serial.Serial(com_port, baudrate=57600, timeout=1)
            self.active = True
            print(f"[DMX PRO] Successfully connected on {com_port}!")
            self.blackout()
        except Exception as e:
            print(f"[DMX ERROR] Could not open {com_port}: {e}")

    def set_rgb(self, r, g, b, dimmer=255):
        self.dmx_data[1] = max(0, min(255, int(dimmer)))
        self.dmx_data[2] = max(0, min(255, int(r)))
        self.dmx_data[3] = max(0, min(255, int(g)))
        self.dmx_data[4] = max(0, min(255, int(b)))

    def render(self):
        if not self.active:
            return
        data_len = len(self.dmx_data)
        header = bytearray([
            self.START_VAL,
            self.SEND_DMX_LABEL,
            data_len & 0xFF,
            (data_len >> 8) & 0xFF
        ])
        packet = header + self.dmx_data + bytearray([self.END_VAL])
        self.serial.write(packet)

    def blackout(self):
        self.dmx_data = bytearray(self.num_channels + 1)
        self.render()

# Instantiate global DMX interface
dmx = EnttecDMXPro(ENTTEC_PORT)