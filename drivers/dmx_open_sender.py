"""Standalone "Open DMX" sender -- run as a subprocess by drivers/dmx_driver.py (2026-11-19).

A no-buffer FTDI USB-RS485 cable makes the computer produce the DMX break/mark-after-break and keep a
steady refresh itself. Done on a thread inside the show app, GIL contention at a song change stretched
the break from 0.2 ms to 3-40 ms and the fixtures read it as signal loss (strobing). This process does
nothing else, so its timing stays clean however busy the app is.

Usage: dmx_open_sender.py <device> <fps>
stdin: frames as <uint16 little-endian length><bytes> (start code 0 + channels). Newest frame wins and
is repeated at <fps> until replaced. Exits on stdin EOF (parent gone) or any serial error.
"""
import os
import struct
import sys
import threading
import time

import serial


def main():
    device, fps = sys.argv[1], max(5.0, float(sys.argv[2]))
    ser = serial.Serial(device, baudrate=250000, bytesize=8, parity="N", stopbits=2,
                        timeout=1, write_timeout=1)
    latest = [None]

    def reader():
        rd = sys.stdin.buffer
        while True:
            head = rd.read(2)
            if len(head) < 2:
                break
            n = struct.unpack("<H", head)[0]
            data = rd.read(n)
            if len(data) < n:
                break
            latest[0] = data
        os._exit(0)  # parent closed the pipe / died

    threading.Thread(target=reader, daemon=True).start()

    period = 1.0 / fps
    next_at = time.monotonic()
    last_end = 0.0
    last_warn = 0.0
    try:
        while True:
            frame = latest[0]
            if frame is None:
                time.sleep(0.005)
                next_at = time.monotonic()
                continue
            delay = next_at - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            t0 = time.monotonic()
            next_at = t0 + period
            ser.flush()                     # previous frame fully out before the break
            ser.break_condition = True
            tb = time.monotonic()
            time.sleep(0.0002)              # break >= 92 us
            ser.break_condition = False
            tc = time.monotonic()
            time.sleep(0.00003)             # mark after break
            ser.write(frame)
            te = time.monotonic()
            gap = (t0 - last_end) if last_end else 0.0
            last_end = te
            if (tc - tb > 0.002 or gap > 0.1) and te - last_warn > 1.0:
                last_warn = te
                print(f"[DMX SENDER] break held {(tc - tb) * 1000:.1f} ms, frame gap {gap * 1000:.0f} ms",
                      file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[DMX SENDER] serial error: {e}", file=sys.stderr, flush=True)
        os._exit(1)


if __name__ == "__main__":
    main()
