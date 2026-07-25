#!/usr/bin/env python3
"""Probe the Y50P's SPP replies, one query frame at a time.

Sends each candidate query on its own and drains the socket afterwards, so every
reply is attributable to the frame that provoked it. Strictly read-only: the
feed/print frames (0x21, 0x37, 0x1a, 0x39) are deliberately not sent.

Usage: python3 probes/status-probe.py [label]
"""
import socket
import sys
import time

sys.path.insert(0, __file__.rsplit('/', 2)[0])
from y50p import ADDR, RFCOMM_CHANNEL, crc, frame  # noqa: E402
from parse_frames import parse  # noqa: E402

# Handshake first (the app always opens with these), then everything that looks
# like a query. Nothing here moves paper.
PROBES = [
    '0104010000', '01b7010000', '0107010000', '0102010000',
    '050b010000',
    '0519010000', '0536010000', '052001010000', '051101010008',
]


def drain(s, wait=1.2):
    s.settimeout(wait)
    buf = b''
    while True:
        try:
            chunk = s.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk
    return buf


def show(tag, raw):
    if not raw:
        print(f'  {tag}: (no reply)')
        return
    print(f'  {tag}: {len(raw)} bytes  {raw.hex()}')
    for kind, off, *rest in parse(raw):
        if kind == 'frame':
            payload, chk = rest
            good = crc(payload) == int.from_bytes(chk, 'little')
            printable = ''.join(chr(b) if 32 <= b < 127 else '.'
                                for b in payload)
            print(f'    @{off:04x} len={len(payload):<3} crc={"ok" if good else "BAD"} '
                  f'payload={payload.hex()}  |{printable}|')
        else:
            blob, = rest
            printable = ''.join(chr(b) if 32 <= b < 127 else '.' for b in blob)
            print(f'    @{off:04x} UNFRAMED len={len(blob)} '
                  f'{blob.hex()}  |{printable}|')


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else 'default'
    print(f'=== state: {tag}')
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
                      socket.BTPROTO_RFCOMM)
    s.settimeout(15)
    s.connect((ADDR, RFCOMM_CHANNEL))
    print('connected')
    show('on-connect (unsolicited)', drain(s, 1.5))
    for p in PROBES:
        s.sendall(frame(p))
        time.sleep(0.15)
        show(f'-> {p}', drain(s))
    s.close()


if __name__ == '__main__':
    main()
