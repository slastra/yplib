#!/usr/bin/env python3
"""Probe the Y50P's classic-Bluetooth SPP side: find the RFCOMM channel and
send a minimal TSPL test label. SIZE 2,1 keeps it safe on any media."""
import socket
import sys

ADDR = 'XX:XX:XX:XX:XX:XX'
TSPL = (
    b'SIZE 2,1\r\n'
    b'GAP 0.12,0\r\n'
    b'DIRECTION 0\r\n'
    b'CLS\r\n'
    b'TEXT 20,20,"4",0,1,1,"Y50P SPP OK"\r\n'
    b'PRINT 1\r\n'
    b'END\r\n'
)

for ch in (1, 2, 4, 6, 3, 5):
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    s.settimeout(8)
    try:
        s.connect((ADDR, ch))
        print(f'connected on RFCOMM channel {ch}')
        s.sendall(TSPL)
        print(f'sent {len(TSPL)} bytes of TSPL')
        # Some models talk back over SPP - listen briefly
        s.settimeout(3)
        try:
            data = s.recv(256)
            print(f'REPLY: {len(data)} bytes hex={data.hex()} ascii={data!r}')
        except socket.timeout:
            print('no reply (write-only, same as CTP800D)')
        s.close()
        sys.exit(0)
    except OSError as e:
        print(f'channel {ch}: {e}')
        s.close()
print('no RFCOMM channel accepted a connection')
