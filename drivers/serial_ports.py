"""drivers/serial_ports.py
Tiny shared registry letting drivers/led_bridge.py (matrix panel ESP32) and
drivers/wled_engine.py (marquee WLED ESP32) coordinate which of two
same-VID/PID boards each one opens, instead of each independently grabbing
"the first port that matches config.ESP32_USB_SERIAL_VID_PIDS" and
racing/colliding once a second board with the same USB-UART chip shows up
(2026-09: confirmed live -- plugging in the marquee board made led_bridge
grab its port instead of the panel's, and led_bridge had no way to notice
since an open-but-wrong serial port never errors, it just never
heartbeats, and led_bridge only ever rescans while its link is None).

The two sides play asymmetric roles here because only one of them can
actually prove which board it's talking to: led_bridge.py reads back a
custom heartbeat byte Display.ino writes (firmware this project owns), so
it can positively confirm -- or rule out -- a given port. wled_engine.py's
board runs stock third-party WLED, which has no equivalent signal, so it
can never do better than a guess on its own.

That's why this module has a `hold`/`release` pair (soft, mutual "don't
open what the other one already has") plus a one-way `reject`/
`rejected_ports` signal that only led_bridge.py ever writes to: once it's
opened a port and proven -- by timeout, see led_bridge._NEVER_READY_TIMEOUT_S
-- that it's NOT Display.ino, led_bridge.py de-prioritizes it on future
scans so it doesn't keep re-trying a port it already knows is wrong.

2026-09-21 fix: `reject`/`rejected_ports` used to be a single overwritable
slot ("the one most-recently-rejected port"), not a set -- reasonable when
only two boards could ever share config.ESP32_USB_SERIAL_VID_PIDS (this
module's original two-board design, see history above), but with a third
CP2102 board (accent) now on the rig, each new rejection made the module
"forget" the previous one, and led_bridge.py's own candidate scan (which
only ever de-prioritizes, never permanently excludes, a rejected port)
would immediately re-try it -- confirmed live as an endless two-port
ping-pong (led_bridge rejects port A, tries port B, rejects port B
-- forgetting A was ever rejected -- tries A again, forever) that never
gave a third, genuinely-unheld candidate a chance. A real set fixes that:
every port led_bridge has ever ruled out this session stays de-prioritized
rather than just the last one.
"""

_holders = {}  # device path -> owner name ("led_bridge"/"wled_engine"/"accent_engine"), currently open
_rejected_by_led_bridge = set()  # device paths led_bridge has proven aren't its board


def hold(device, owner):
    _holders[device] = owner


def release(device):
    _holders.pop(device, None)


def held_by(device):
    """Owner name currently holding `device` open, or None."""
    return _holders.get(device)


def reject(device):
    """Called only by led_bridge.py when a connection it opened never
    heartbeats -- see module docstring."""
    _rejected_by_led_bridge.add(device)


def rejected_ports():
    return _rejected_by_led_bridge
