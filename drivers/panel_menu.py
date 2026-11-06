"""drivers/panel_menu.py
Panel Show Maker (2026-10-17, docs/panel_show_maker_design.md): set up and
start a show from the arcade buttons and LED panels alone, no phone.
Runs while state.show_phase == "setup" (boot, or the admin's reset).

Pages show on LED panels 1+2; panels 3-6 show only what each button does,
as a word (graphics/menu_pages.py draws view()):

  NETWORK      WiFi name, IP, tunnel -- moves on once all are good, or after
               30s regardless (blue = skip ahead)
  LOAD SHOW    only if saves exist. green PREV / red MORE browse (most recently
               played first; green is DIAG on the first one), yellow NEW = the
               designer, blue START = run it
  INTRO?       \\ designer: green YES / red NO, or green OLDER / red NEWER,
  QUESTIONS     } yellow BACK, blue NEXT (LOW pushes HIGH up; HIGH stops at LOW)
  LOW DECADE    }
  HIGH DECADE  /
  Panels 3-6 are labelled in plain words; a button with nothing to do (end of
  a list, the answer already chosen) stays dark, light off.
  START        blue START -> saved as a new show
  then blink SAVED AS <NAME> x4, NETWORK STILL DOWN x2 (only if it is), and
  hold BLACK (show_phase "dark") until any button.

Long presses (1s, act on release, never also the short press): yellow = page 1,
blue = GO NOW, green = diagnostics. Hands-off: nobody touched a button or
opened the admin remote for 90s since boot -> the last played show runs by
itself (a default one is made and named if none exist).
"""
import os
import re
import subprocess
import sys
import threading
import time

import config
from state import state
from drivers import show_library

DESIGN_PAGES = ("intro", "questions", "low", "high", "start")

_page = "network"
_page_at = 0.0
_boot_mono = None
_touched = False
_load_index = 0
_load_names = []
_values = dict(show_library.DEFAULTS)
_press_at = {}
_diag_return = "network"
_blink = []           # [(line1, line2, until_mono)] then -> BLACK
_after_blink = None   # (show name, hands_off)

_ssid = ""
_ssid_thread = None


# ------------------------------------------------------------- network
def _read_ssid():
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True,
                                 text=True, timeout=4).stdout
            m = re.search(r"^\s*SSID\s*:\s*(.+)$", out, re.M)
            return m.group(1).strip() if m else ""
        for exe in ("/usr/sbin/iwgetid", "/sbin/iwgetid"):
            if os.path.exists(exe):
                return subprocess.run([exe, "-r"], capture_output=True, text=True, timeout=4).stdout.strip()
    except Exception:
        pass
    return ""


def _ssid_loop():
    global _ssid
    while True:
        _ssid = _read_ssid()
        time.sleep(5.0)


def _ensure_ssid_thread():
    global _ssid_thread
    if _ssid_thread is None:
        _ssid_thread = threading.Thread(target=_ssid_loop, daemon=True)
        _ssid_thread.start()


_net_cache = (0.0, None)


def _wifi_dbm():
    """Signal strength of wlan0 in dBm (Pi only; None elsewhere)."""
    try:
        with open("/proc/net/wireless") as f:
            for line in f.readlines()[2:]:
                parts = line.split()
                if len(parts) > 3:
                    return int(float(parts[3]))
    except (OSError, ValueError):
        pass
    return None


def _cpu_temp_c():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000.0
    except (OSError, ValueError):
        return None


def network_status():
    """WiFi name, IP, tunnel, internet -- re-read at most once a second (the
    view is drawn every frame)."""
    global _net_cache
    mono = time.monotonic()
    if _net_cache[1] is not None and mono - _net_cache[0] < 1.0:
        return _net_cache[1]
    from web.net_info import get_lan_ip
    from drivers import tunnel_engine
    ip = get_lan_ip()
    st = {
        "ssid": _ssid,
        "ip": "" if ip.startswith("127.") else ip,
        "tunnel": bool(tunnel_engine.get_current_tunnel_url()),
        "internet": tunnel_engine.internet_reachable(),
        "dbm": _wifi_dbm(),
        "temp": _cpu_temp_c(),
    }
    _net_cache = (mono, st)
    return st


def network_ok(st=None):
    st = st or network_status()
    return bool(st["ssid"] and st["ip"] and st["tunnel"])


# ------------------------------------------------------------- touched
def mark_touched(source="button"):
    """A physical button press or the admin remote: never hands-off again this boot."""
    global _touched
    if not _touched and state.show_phase == "setup":
        print(f"[MENU] Touched ({source}) -- no hands-off start this boot.")
    _touched = True


def touched():
    return _touched


def reset(touched=False):
    """Back to the start of the menu (admin full reset)."""
    global _page, _page_at, _touched, _blink, _after_blink, _press_at
    _touched = _touched or touched
    _blink, _after_blink, _press_at = [], None, {}
    _refresh_saves()
    _go("load" if _load_names else "intro")
    _page_at = time.monotonic()


def refresh_saves():
    """drivers/web_sync.py: shows changed on the web page."""
    global _load_index
    with_name = _load_names[_load_index] if _load_names else None
    _refresh_saves()
    if with_name in _load_names:
        _load_index = _load_names.index(with_name)


def _refresh_saves():
    global _load_names, _load_index
    _load_names = [s["name"] for s in show_library.all_shows("played")]
    _load_index = min(_load_index, max(0, len(_load_names) - 1))


def _go(page):
    global _page, _page_at
    _page = page
    _page_at = time.monotonic()


def _first_page():
    return "load" if _load_names else "intro"


# -------------------------------------------------------------- actions
def _run(name, created, hands_off=False):
    """Blink the confirmations, then load the show and hold BLACK."""
    global _blink, _after_blink
    now = time.monotonic()
    period = config.MENU_BLINK_PERIOD_SECONDS
    seq = []
    t = now
    if created:
        t += period * config.MENU_SAVED_BLINKS
        seq.append(("SAVED AS", name, t))
    if not network_ok():
        t += period * config.MENU_NETWORK_DOWN_BLINKS
        seq.append(("NETWORK", "STILL DOWN", t))
    _blink = seq
    _after_blink = (name, hands_off)
    _go("blink")
    if not seq:
        _finish_blink()


def _finish_blink():
    global _after_blink
    name, hands_off = _after_blink
    _after_blink = None
    from drivers import show_engine
    show_engine.start_show(name, hands_off=hands_off)


def _create_from_values():
    show = show_library.create(intro=_values["intro"], questions=_values["questions"],
                               low=_values["low"], high=_values["high"])
    return show["name"]


def _go_now():
    """Blue long press: start with what is on screen now."""
    if _page == "load" and _load_names:
        _run(_load_names[_load_index], created=False)
    elif _page in DESIGN_PAGES:
        _run(_create_from_values(), created=True)
    else:
        show = show_library.last_played()
        if show:
            _run(show["name"], created=False)
        else:
            _run(_create_from_values(), created=True)


def _step_decade(key, delta):
    d = show_library.DECADES
    i = max(_decade_floor(key), min(len(d) - 1, d.index(_values[key]) + delta))
    _values[key] = d[i]
    if key == "low" and d.index(_values["high"]) < i:
        _values["high"] = _values["low"]   # LOW pushes HIGH up; HIGH stops at LOW


def _decade_floor(key):
    """Lowest index a decade page can step to: HIGH never goes below LOW."""
    return show_library.DECADES.index(_values["low"]) if key == "high" else 0


def _short(color):
    global _load_index, _diag_return
    page = _page
    if page == "diag":
        if color == "green":
            _go(_diag_return)
        return
    if page == "blink":
        return
    if page == "network":
        if color == "blue":
            _go(_first_page())
        return
    if page == "load":
        if color == "green":
            if _load_index > 0:
                _load_index -= 1
            else:
                _diag_return = "load"   # first show: green is DIAG, not PREV
                _go("diag")
        elif color == "red" and _load_index < len(_load_names) - 1:
            _load_index += 1
        elif color == "yellow":
            _values.update(show_library.DEFAULTS)
            _go("intro")
        elif color == "blue" and _load_names:
            _run(_load_names[_load_index], created=False)
        return
    i = DESIGN_PAGES.index(page)
    if color == "yellow":
        _go(DESIGN_PAGES[i - 1] if i > 0 else ("load" if _load_names else "intro"))
        return
    if color == "blue":
        if page == "start":
            _run(_create_from_values(), created=True)
        else:
            _go(DESIGN_PAGES[i + 1])
        return
    if page in ("intro", "questions"):
        if color in ("green", "red"):
            _values[page] = color == "green"   # green = YES, red = NO
    elif page in ("low", "high"):
        if color == "green":
            _step_decade(page, -1)
        elif color == "red":
            _step_decade(page, +1)


def _long(color):
    global _diag_return
    if _page == "blink":
        return
    if color == "yellow":
        _go(_first_page())
    elif color == "blue":
        _go_now()
    elif color == "green" and _page != "diag":
        _diag_return = _page
        _go("diag")


def on_press(color, now):
    mark_touched("panel button")
    _press_at[color] = time.monotonic()


def held():
    """The button held down most recently (None if none) -- the outline strip
    glows its colour (drivers/accent_engine.py)."""
    return max(_press_at, key=_press_at.get) if _press_at else None


def on_release(color, now):
    started = _press_at.pop(color, None)
    if started is None:
        return
    if time.monotonic() - started >= config.MENU_LONG_PRESS_SECONDS:
        _long(color)
    else:
        _short(color)


# ---------------------------------------------------------------- frame
def update(now):
    """Pumped from show_engine.update() while show_phase == "setup"."""
    global _boot_mono
    mono = time.monotonic()
    if _boot_mono is None:
        _boot_mono = mono
        _ensure_ssid_thread()
        _refresh_saves()
        _go("network")
    if _page == "network":
        waited = mono - _page_at
        if (network_ok() and waited >= 3.0) or (
                network_status()["ip"] and waited >= config.MENU_NETWORK_WAIT_SECONDS):
            _go(_first_page())
        elif not network_status()["ip"]:
            return  # no IP: hold here (blue SKIP only); no timeout, no hands-off start
    elif _page == "blink":
        if _blink and mono >= _blink[-1][2] and _after_blink:
            _finish_blink()
        return
    if not _touched and mono - _boot_mono >= config.MENU_HANDS_OFF_SECONDS:
        show = show_library.last_played()
        print("[MENU] Hands-off for 90s -- running the last played show.")
        if show:
            _run(show["name"], created=False, hands_off=True)
        else:
            _run(show_library.create()["name"], created=True, hands_off=True)


# ----------------------------------------------------------------- view
def _yes_no(v):
    return "YES" if v else "NO"


def view():
    """What graphics/menu_pages.py draws: {"lines": (top, bottom),
    "buttons": {color: label}, "blink": bool}."""
    mono = time.monotonic()
    buttons = {}
    page = _page
    if page == "network":
        st = network_status()
        line1 = f"WIFI {st['ssid']}" if st["ssid"] else "NO WIFI"
        if int((mono - _page_at) / 2.5) % 2 == 0:
            line2 = st["ip"] or "NO IP"
        else:
            line2 = "TUNNEL OK" if st["tunnel"] else "TUNNEL ..."
        buttons = {"blue": "SKIP"}
        return {"lines": (line1, line2), "buttons": buttons, "blink": False}
    if page == "load":
        name = _load_names[_load_index] if _load_names else ""
        buttons = {"green": "PREV" if _load_index > 0 else "DIAG",
                   "red": "MORE" if _load_index < len(_load_names) - 1 else "",
                   "yellow": "NEW", "blue": "START"}
        return {"lines": ("LOAD SHOW", name), "buttons": buttons, "blink": False}
    if page == "diag":
        # One page: IP + (cycling) extras on panels 1+2, the readings on the
        # four button panels (graphics/menu_pages.py "cells"), green = BACK.
        st = network_status()
        from drivers import led_bridge
        from drivers.dmx_driver import dmx
        from inputs import gamepad
        extras = [
            f"WIFI {st['ssid'] or '-'}",
            "LED " + ("USB" if led_bridge.current_transport().startswith("SERIAL") else "UDP")
            + "  DMX " + ("OK" if dmx.active else "NO"),
            ("JOY " + ("OK" if gamepad.joysticks else "NO")) + f"  SHOWS {len(_load_names)}",
        ]
        dbm, temp = st["dbm"], st["temp"]
        tunnel = "OK" if st["tunnel"] else ("NO" if st["internet"] else "NO NET")
        cells = {
            "green": ("BACK", ""),
            "red": ("WIFI", f"{dbm}dB" if dbm is not None else "--"),
            "yellow": ("TEMP", f"{temp:.0f}C" if temp is not None else "--"),
            "blue": ("TUNL", tunnel),
        }
        return {"lines": (st["ip"] or "NO IP", extras[int((mono - _page_at) / 2.5) % len(extras)]),
                "buttons": {}, "cells": cells, "blink": False}
    if page == "blink":
        current = next((b for b in _blink if mono < b[2]), _blink[-1] if _blink else ("", "", 0))
        return {"lines": (current[0], current[1]), "buttons": {}, "blink": True}
    back = {"yellow": "BACK"}
    if page in ("intro", "questions"):
        # only the button that would change the answer is lit
        on = _values[page]
        return {"lines": ("INTRO?" if page == "intro" else "QUESTIONS", _yes_no(on)),
                "buttons": dict(back, green="" if on else "YES", red="NO" if on else "", blue="NEXT"),
                "blink": False}
    if page in ("low", "high"):
        d = show_library.DECADES
        i = d.index(_values[page])
        label = show_library.decade_label(_values[page])
        return {"lines": ("LOW DECADE" if page == "low" else "HIGH DECADE", label),
                "buttons": dict(back, green="OLDER" if i > _decade_floor(page) else "",
                                red="NEWER" if i < len(d) - 1 else "", blue="NEXT"), "blink": False}
    return {"lines": ("START", "PRESS BLUE"),
            "buttons": dict(back, blue="START"), "blink": False}
