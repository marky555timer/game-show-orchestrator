"""drivers/status_display_engine.py
Rig health readout on a small I2C OLED (SSD1306/SH1106, 128x64) plus an
INA219/INA226 current sensor on the same bus (2026-10-14).

Shows: amps, estimated battery time, LAN IP, uptime, CPU temp. If the tunnel
isn't live once the Pi has been up TUNNEL_WARN_AFTER_SECONDS, the screen flips
to a big blinking "NO TUNNEL" so it's seen from across the room.

Everything is optional: no smbus2 / luma / hardware -> the thread logs once
and exits, the rest of the app is untouched. Runs on its own daemon thread, so
slow I2C never touches the main loop. Config: config.STATUS_DISPLAY_*.
"""
import threading
import time

import config

_I2C_BUS = 1


def _read_first_line(path):
    try:
        with open(path) as f:
            return f.readline().strip()
    except OSError:
        return ""


def _uptime_seconds():
    try:
        return float(_read_first_line("/proc/uptime").split()[0])
    except (IndexError, ValueError):
        return 0.0


def _cpu_temp_c():
    try:
        return int(_read_first_line("/sys/class/thermal/thermal_zone0/temp")) / 1000.0
    except ValueError:
        return None


def _fmt_uptime(sec):
    sec = int(sec)
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}d{h:02d}h"
    return f"{h}h{m:02d}m"


def _fmt_hours(hours):
    if hours is None:
        return "--"
    if hours >= 100:
        return ">99h"
    h = int(hours)
    return f"{h}h{int((hours - h) * 60):02d}m"


class _CurrentSensor:
    """INA226 (id 0x5449 at reg 0xFE) or INA219, auto-detected at the
    configured address. read() -> (volts, amps) or None."""

    def __init__(self, bus, addr, shunt_ohms):
        self.bus, self.addr, self.shunt = bus, addr, shunt_ohms
        ident = self._word(0xFE)
        self.kind = "ina226" if ident == 0x5449 else "ina219"
        if self.kind == "ina226":
            self._write(0x00, 0x4527)   # 16 averages, 1.1ms conversions, continuous
        else:
            self._write(0x00, 0x399F)   # 32V, +/-320mV shunt, 12-bit, continuous

    def _word(self, reg):
        raw = self.bus.read_word_data(self.addr, reg)
        return ((raw & 0xFF) << 8) | (raw >> 8)    # SMBus is little-endian, chip is big

    def _write(self, reg, value):
        self.bus.write_word_data(self.addr, reg, ((value & 0xFF) << 8) | (value >> 8))

    def read(self):
        try:
            if self.kind == "ina226":
                shunt_raw = self._word(0x01)
                volts = self._word(0x02) * 0.00125
                shunt_v = (shunt_raw - 65536 if shunt_raw > 32767 else shunt_raw) * 2.5e-6
            else:
                shunt_raw = self._word(0x01)
                volts = (self._word(0x02) >> 3) * 0.004
                shunt_v = (shunt_raw - 65536 if shunt_raw > 32767 else shunt_raw) * 1e-5
            return volts, shunt_v / self.shunt
        except OSError:
            return None


class _Battery:
    """Remaining time from bus voltage (linear empty..full) x capacity,
    divided by smoothed power draw. Rough by design -- label it "bat"."""

    def __init__(self):
        self.avg_w = None

    def update(self, volts, amps):
        w = max(0.0, volts * amps)
        self.avg_w = w if self.avg_w is None else self.avg_w * 0.9 + w * 0.1
        c = config
        span = max(0.1, c.STATUS_DISPLAY_BATTERY_FULL_V - c.STATUS_DISPLAY_BATTERY_EMPTY_V)
        soc = min(1.0, max(0.0, (volts - c.STATUS_DISPLAY_BATTERY_EMPTY_V) / span))
        if self.avg_w < 0.5:
            return None
        return c.STATUS_DISPLAY_BATTERY_WH * soc / self.avg_w


def _tunnel_ok():
    from drivers import tunnel_engine
    status = tunnel_engine.get_status()
    return status == "disabled" or status == "live"


def _lan_ip():
    try:
        from web import net_info
        return net_info.get_lan_ip() or "no network"
    except Exception:
        return "no network"


def _run():
    try:
        import smbus2
        from luma.core.interface.serial import i2c
        from luma.oled.device import sh1106, ssd1306
        from PIL import ImageFont
        from luma.core.render import canvas
    except ImportError as exc:
        print(f"[status-display] disabled, missing library: {exc}")
        return
    try:
        device_cls = sh1106 if config.STATUS_DISPLAY_CONTROLLER == "sh1106" else ssd1306
        device = device_cls(i2c(port=_I2C_BUS, address=config.STATUS_DISPLAY_ADDR),
                            width=128, height=64)
        device.contrast(config.STATUS_DISPLAY_CONTRAST)
    except Exception as exc:
        print(f"[status-display] OLED not found: {exc}")
        return

    sensor = None
    try:
        sensor = _CurrentSensor(smbus2.SMBus(_I2C_BUS), config.STATUS_DISPLAY_SENSOR_ADDR,
                                config.STATUS_DISPLAY_SHUNT_OHMS)
        print(f"[status-display] {sensor.kind} current sensor found")
    except Exception as exc:
        print(f"[status-display] no current sensor ({exc}); amps will show --")

    try:
        font_big = ImageFont.truetype("DejaVuSans-Bold.ttf", 22)
        font_mid = ImageFont.truetype("DejaVuSans-Bold.ttf", 13)
        font_sm = ImageFont.truetype("DejaVuSansMono.ttf", 11)
    except OSError:
        font_big = font_mid = font_sm = ImageFont.load_default()

    battery = _Battery()
    hours = None
    volts = amps = None
    while True:
        now = time.time()
        up = _uptime_seconds()
        if sensor is not None:
            reading = sensor.read()
            if reading:
                volts, amps = reading
                hours = battery.update(volts, amps)
        # burn-in guard: nudge everything a pixel or two every minute
        dx, dy = int(now // 60) % 3, int(now // 180) % 3
        warn = (not _tunnel_ok()) and up > config.STATUS_DISPLAY_TUNNEL_WARN_AFTER_SECONDS
        with canvas(device) as draw:
            if warn:
                blink_on = int(now * 2) % 2 == 0
                if blink_on:
                    draw.rectangle((0, 0, 127, 63), fill="white")
                fg = "black" if blink_on else "white"
                draw.text((4 + dx, 2 + dy), "NO TUNNEL", font=font_big, fill=fg)
                draw.text((4 + dx, 30 + dy), "guests can't join", font=font_mid, fill=fg)
                draw.text((4 + dx, 48 + dy), _lan_ip(), font=font_sm, fill=fg)
            else:
                amp_txt = f"{amps:.1f}A" if amps is not None else "--A"
                draw.text((2 + dx, 0 + dy), amp_txt, font=font_big, fill="white")
                draw.text((70 + dx, 5 + dy), "bat " + _fmt_hours(hours), font=font_mid, fill="white")
                draw.text((2 + dx, 30 + dy), _lan_ip(), font=font_mid, fill="white")
                temp = _cpu_temp_c()
                temp_txt = f"{temp:.0f}C" if temp is not None else "--C"
                draw.text((2 + dx, 48 + dy), f"up {_fmt_uptime(up)}   {temp_txt}",
                          font=font_sm, fill="white")
        time.sleep(0.25 if warn else 1.0)


def start():
    if not config.STATUS_DISPLAY_ENABLED:
        return
    threading.Thread(target=_run, name="status-display", daemon=True).start()
