import serial
from config import ENTTEC_PORT, DMX_FIXTURE_CHANNELS, DMX_NUM_FIXTURES

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

    # ------------------------------------------------------------
    # Fixture table: all 11 fixtures (ch 1-176, 16 channels each) are now
    # Rockville MINIRF4 V2 uplighters sharing one channel layout -- ch1
    # dimmer, ch2 R, ch3 G, ch4 B, ch5 White, ch6 Amber, ch7 UV, ch8
    # Strobe, ch9 AutoModes, ch10 SoundActive, ch11-16 unused.
    #
    # Fixture 1 used to be a plain RGB-only par (win/loss indicator lamp,
    # no White/Amber/UV emitters), which is why it had its own narrower
    # setter and why colors built on those emitters had to fall back to an
    # RGB approximation on it. It was replaced with a matching MINIRF4 V2
    # (2026-08-11), so that special case is gone: every fixture can render
    # every look, UV included.
    #
    # Every fixture sits on a strict 16-channel boundary:
    # base(i) = 1 + (i-1)*16.
    # ------------------------------------------------------------
    def _fixture_base(self, index):
        return 1 + (index - 1) * DMX_FIXTURE_CHANNELS

    def set_fixture(self, index, dimmer, r, g, b, white=0, amber=0, uv=0,
                    strobe=0, auto=0, sound_active=0):
        """Writes one fixture's channel block. Valid for fixtures 1-11."""
        if not (1 <= index <= DMX_NUM_FIXTURES):
            return
        base = self._fixture_base(index)
        values = (dimmer, r, g, b, white, amber, uv, strobe, auto, sound_active)
        for offset, val in enumerate(values):
            self.dmx_data[base + offset] = max(0, min(255, int(val)))
        for ch in range(base + len(values), base + DMX_FIXTURE_CHANNELS):
            self.dmx_data[ch] = 0

    def set_fixture1(self, intensity, r, g, b, white=0, amber=0, uv=0,
                     strobe=0, auto=0, sound_active=0):
        """Fixture 1 -- the win/loss indicator lamp. Same hardware and
        channel layout as the uplights now; kept as a named method because
        it's a distinct role in the show, not a distinct device."""
        self.set_fixture(1, intensity, r, g, b, white, amber, uv,
                         strobe, auto, sound_active)

    def set_uplight(self, index, dimmer, r, g, b, white=0, amber=0, uv=0,
                     strobe=0, auto=0, sound_active=0):
        """Fixtures 2-11, the venue uplighters. Guarded to 2+ so uplight
        loops can't accidentally stomp Fixture 1's win/loss state."""
        if index < 2:
            return
        self.set_fixture(index, dimmer, r, g, b, white, amber, uv,
                         strobe, auto, sound_active)

    def set_all_uplights(self, dimmer, r, g, b, white=0, amber=0, uv=0,
                          strobe=0, auto=0, sound_active=0):
        """Applies the same uplight values to all of fixtures 2-11 at once
        (unified DJ-mode themes)."""
        for index in range(2, DMX_NUM_FIXTURES + 1):
            self.set_uplight(index, dimmer, r, g, b, white, amber, uv,
                              strobe, auto, sound_active)

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
