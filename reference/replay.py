#!/usr/bin/env python3
"""Replay a captured FlashLabel byte stream to a Y50P printer over Bluetooth SPP.

Historical: superseded by y50p.py once the frame format was solved.

This replays a capture verbatim, reproducing exactly the label that was
recorded. Kept because it is what first proved the printer would accept a
replayed session at all. For arbitrary labels use y50p.py.

Usage: python3 replay.py [capture.bin] [printer_mac]
"""
import socket
import sys
import time

CAPTURE = sys.argv[1] if len(sys.argv) > 1 else 'captures/y50p-flashlabel-label.bin'
ADDR = sys.argv[2] if len(sys.argv) > 2 else 'XX:XX:XX:XX:XX:XX'  # Y50P classic MAC

data = open(CAPTURE, 'rb').read()
s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
s.settimeout(15)
s.connect((ADDR, 1))  # RFCOMM channel 1

# Send frame-by-frame on 0x1a boundaries, mimicking the app's pacing
i = 0
while i < len(data):
    nxt = data.find(b'\x1a', i + 1)
    chunk = data[i:nxt] if nxt != -1 else data[i:]
    s.sendall(chunk)
    i = nxt if nxt != -1 else len(data)
    time.sleep(0.01)

print(f'replayed {len(data)} bytes')
time.sleep(2)
s.close()
