#!/usr/bin/env python3
"""Identify a USB printer-class device by speaking the Y50P protocol to it.

The Y50P turns out to enumerate over USB as a printer-class device (class 07,
subclass 01, protocol 02 = bidirectional), so the same framed protocol used over
Bluetooth SPP should work over /dev/usb/lpN. This sends only the group-01 info
queries, which are read-only and never move paper.

Usage: python3 probes/usb-probe.py [/dev/usb/lp1]
"""
import os
import select
import sys

sys.path.insert(0, __file__.rsplit('/', 2)[0])
from y50p import QUERIES, describe_status, request, read_replies  # noqa: E402

DEVICE = sys.argv[1] if len(sys.argv) > 1 else '/dev/usb/lp1'
TIMEOUT = 2.0


def ask(fd, group, cmd):
    os.write(fd, request(group, cmd))
    buf = b''
    while select.select([fd], [], [], TIMEOUT)[0]:
        try:
            chunk = os.read(fd, 4096)
        except BlockingIOError:
            break
        if not chunk:
            break
        buf += chunk
        if buf.endswith(b'\xa1'):
            break
    return buf


def main():
    print(f'=== {DEVICE}')
    fd = os.open(DEVICE, os.O_RDWR | os.O_NONBLOCK)
    try:
        for name, (group, cmd) in QUERIES.items():
            raw = ask(fd, group, cmd)
            if not raw:
                print(f'  {name:10} (no reply)')
                continue
            for g, c, d, value in read_replies(raw):
                if (g, c) != (group, cmd):
                    continue
                if name == 'status':
                    b = value[0] if isinstance(value, bytes) and value else value
                    print(f'  {name:10} 0x{b:02x}  ({describe_status(b)})')
                elif isinstance(value, bytes):
                    txt = (value.decode('ascii', 'replace')
                           if all(32 <= x < 127 for x in value) else value.hex())
                    print(f'  {name:10} {txt}')
                else:
                    print(f'  {name:10} {value}')
    finally:
        os.close(fd)


if __name__ == '__main__':
    main()
