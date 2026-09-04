#!/usr/bin/env python3
"""DTR/RTS soft-reset of the ESP32 over the existing USB serial link.

No power interruption: this drives the CH340 handshake lines that the ESP32
dev board wires to EN/BOOT, which is the same reset the Arduino IDE uses.
Requires exclusive access to the port -- stop dt-bridge first.
"""
import sys, time, serial

PORT, BAUD = "/dev/ttyUSB0", 115200

with serial.Serial(PORT, BAUD, timeout=0.2) as ser:
    print("[pre-reset] listening 2s for existing traffic ...", flush=True)
    t0 = time.time()
    while time.time() - t0 < 2:
        line = ser.readline()
        if line:
            print("  PRE |", line.decode("utf-8", "replace").rstrip(), flush=True)

    print("[reset] pulsing DTR/RTS ...", flush=True)
    ser.setDTR(False)
    ser.setRTS(True)
    time.sleep(0.1)
    ser.setRTS(False)
    t_reset = time.time()

    print("[post-reset] capturing 8s of boot output ...", flush=True)
    while time.time() - t_reset < 8:
        line = ser.readline()
        if line:
            dt = (time.time() - t_reset) * 1000
            print("  +%6.0fms | %s" % (dt, line.decode("utf-8", "replace").rstrip()), flush=True)
