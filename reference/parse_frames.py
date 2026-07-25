#!/usr/bin/env python3
"""Parse a captured Y50P byte stream into control frames + raster blobs.

Frame format (solved and hardware-verified, FINDINGS.md):
    1a 01 <len:u16 LE> <payload:len bytes> <checksum:4 bytes> a1

Anything between a frame's trailing 0xa1 and the next 0x1a that does not parse
as a frame is treated as a raw raster blob and reported separately.

Usage: python3 parse_frames.py [capture.bin ...]
"""
import sys


def parse(data):
    """Yield ('frame', off, payload, checksum) / ('gap', off, blob) tuples."""
    i = 0
    n = len(data)
    while i < n:
        if data[i] == 0x1A and i + 4 <= n and data[i + 1] == 0x01:
            ln = int.from_bytes(data[i + 2:i + 4], 'little')
            end = i + 4 + ln + 4
            if end < n and data[end] == 0xA1:
                yield ('frame', i, data[i + 4:i + 4 + ln], data[i + 4 + ln:end])
                i = end + 1
                continue
        # not a valid frame start: accumulate until the next plausible one
        j = i + 1
        while j < n:
            if data[j] == 0x1A and j + 4 <= n and data[j + 1] == 0x01:
                ln = int.from_bytes(data[j + 2:j + 4], 'little')
                end = j + 4 + ln + 4
                if end < n and data[end] == 0xA1:
                    break
            j += 1
        yield ('gap', i, data[i:j])
        i = j


def main():
    paths = sys.argv[1:] or ['captures/y50p-flashlabel-label.bin']
    for path in paths:
        data = open(path, 'rb').read()
        print(f'=== {path} ({len(data)} bytes)')
        nf = ng = 0
        for kind, off, *rest in parse(data):
            if kind == 'frame':
                payload, chk = rest
                nf += 1
                print(f'  @{off:05x} frame len={len(payload):<3} '
                      f'payload={payload.hex()} chk={chk.hex()}')
            else:
                blob, = rest
                ng += 1
                print(f'  @{off:05x} GAP   len={len(blob):<5} '
                      f'head={blob[:24].hex()}')
        print(f'  -> {nf} frames, {ng} gaps\n')


if __name__ == '__main__':
    main()
