"""drivers/dmx_driver.py
Enttec DMX USB PRO driver.

Identified by USB VID/PID (2026-08-19 fix), not a fixed /dev/ttyUSBx or
COMx path: which physical device lands on which path is decided by USB
enumeration order at boot, which is NOT stable across boots -- confirmed
directly on the show Pi, where the DMX box and the LED panel's ESP32 swapped
ttyUSB0/ttyUSB1 between sessions. A hardcoded path can't tell the two apart
-- serial.Serial() opens successfully regardless of what's actually on the
other end, so "Successfully connected" printed even while this driver was
silently talking to the LED panel's chip instead of the DMX box (same VID/
PID-matching approach drivers/led_bridge.py already uses for the ESP32, for
the same reason).

Also retries on a timer instead of only ever trying once at import time
(maybe_reconnect(), polled every frame from drivers/lighting_engine.py) --
gives it the same "plug it in whenever, no app restart needed" tolerance
led_bridge.py already gives the LED panel, rather than requiring the DMX
box to already be present and enumerated at the exact moment this module
first imports.
"""
import time

import serial
import serial.tools.list_ports

from config import DMX_FIXTURE_CHANNELS, DMX_NUM_FIXTURES

# FTDI FT232R, as reported by the Enttec DMX USB PRO itself (Manufacturer
# "ENTTEC", Product "DMX USB PRO"). This is FTDI's generic default VID/PID,
# not unique to Enttec hardware -- fine on this rig since it's the only
# FT232R-based device in the chain (the LED panel's ESP32 is a completely
# different chip/VID, Silicon Labs CP210x), but would need the product-
# string check below to matter more if another generic FTDI device ever
# joined the USB chain.
_ENTTEC_VID_PID = (0x0403, 0x6001)
_RECONNECT_INTERVAL_S = 3.0


def _find_enttec_port():
    for port in serial.tools.list_ports.comports():
        if (port.vid, port.pid) == _ENTTEC_VID_PID:
            return port.device
    return None


class EnttecDMXPro:
    START_VAL = 0x7E
    END_VAL = 0xE7
    SEND_DMX_LABEL = 6

    def __init__(self, num_channels=512):
        self.port = None  # the actual device path once connected, for status display
        self.num_channels = num_channels
        self.dmx_data = bytearray(num_channels + 1)
        self.active = False
        self.serial = None
        self._last_reconnect_attempt = 0.0
        self._pulse_off_at = {}  # {channel: time.monotonic() deadline to zero it}
        self._try_connect()

    def _try_connect(self):
        self._last_reconnect_attempt = time.monotonic()
        device = _find_enttec_port()
        if device is None:
            return
        try:
            self.serial = serial.Serial(device, baudrate=57600, timeout=1)
            self.port = device
            self.active = True
            print(f"[DMX PRO] Successfully connected on {device}!")
            self.blackout()
        except Exception as e:
            print(f"[DMX ERROR] Could not open {device}: {e}")

    def maybe_reconnect(self):
        """Poll once per frame from drivers/lighting_engine.py::update() --
        retries finding+opening the Enttec box while not connected (never
        found it yet, or render() below dropped a failed connection).
        Cheap no-op while already active. Uses time.monotonic() throughout
        (not the epoch time.time() the rest of this app's frame loop
        otherwise runs on) so it can't be thrown off by a wall-clock
        adjustment -- same convention drivers/led_bridge.py's own
        reconnect timer uses."""
        if self.active:
            return
        if time.monotonic() - self._last_reconnect_attempt < _RECONNECT_INTERVAL_S:
            return
        self._try_connect()

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

    def set_channel(self, channel, value):
        """Raw single-channel write, for hardware beyond the 176-channel
        fixture table (e.g. the relay block starting at config.RELAY1_CHANNEL)
        that set_fixture()/set_uplight() deliberately don't touch."""
        self.dmx_data[channel] = max(0, min(255, int(value)))

    def pulse_channel(self, channel, value=255, duration=0.25):
        """One-shot: sets `channel` to `value` immediately, then queues it
        to zero again after `duration` seconds -- poll_pulses() (called
        every frame from lighting_engine.update(), same as
        maybe_reconnect()) does the actual timing check. Uses
        time.monotonic() so a wall-clock adjustment can't strand a pulse
        on, same convention maybe_reconnect() already uses."""
        self.set_channel(channel, value)
        self._pulse_off_at[channel] = time.monotonic() + duration

    def poll_pulses(self):
        """Zeroes out any channel whose pulse_channel() duration has
        elapsed. Cheap no-op with nothing pending."""
        if not self._pulse_off_at:
            return
        now = time.monotonic()
        done = [ch for ch, off_at in self._pulse_off_at.items() if now >= off_at]
        for ch in done:
            self.dmx_data[ch] = 0
            del self._pulse_off_at[ch]

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
        try:
            self.serial.write(packet)
        except Exception as e:
            # Device dropped (unplugged, USB hiccup, power loss) -- don't
            # let an unhandled SerialException here take the whole app
            # down (main.py's loop has no top-level try/except). Drop the
            # connection and let maybe_reconnect() pick it back up once
            # it's back, same tolerance led_bridge.py already has for its
            # own write failures.
            print(f"[DMX ERROR] Write failed ({e}) -- will keep retrying reconnect.")
            try:
                self.serial.close()
            except Exception:
                pass
            self.serial = None
            self.port = None
            self.active = False

    def blackout(self):
        self.dmx_data = bytearray(self.num_channels + 1)
        self.render()

# Instantiate global DMX interface
dmx = EnttecDMXPro()
