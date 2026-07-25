#!/usr/bin/env python3
"""Y50P raster codec.

Encoding (recovered 2026-07-24 from differential captures):

    raster := row*
    row    := 0x18 <run>*        # runs continue until WIDTH pixels emitted
    run    := <colour:1><count-1:7>

So a run byte's bit 7 is the colour (0 = white, 1 = black) and its low 7 bits are
the run length minus one, giving runs of 1..128 pixels. Rows are exactly
WIDTH = 400 pixels wide (50 mm at 8 dots/mm, matching the 0x32 = 50 in the
`05 41 04 ...` setup frame). Rows are variable length -- an all-white row costs
4 bytes, so this is compression, not the raw per-row data earlier notes assumed.

Usage: python3 raster.py capture.bin [out.pbm]
"""
import sys

WIDTH = 400
ROW_MARK = 0x18


def decode(blob, width=WIDTH):
    """Decode an RLE raster blob into a list of rows (each a list of 0/1)."""
    rows = []
    i = 0
    n = len(blob)
    while i < n:
        if blob[i] != ROW_MARK:
            raise ValueError(f'expected row marker 0x18 at {i}, '
                             f'got 0x{blob[i]:02x}')
        i += 1
        row = []
        while len(row) < width:
            if i >= n:
                raise ValueError(f'truncated row {len(rows)} '
                                 f'({len(row)}/{width} px)')
            b = blob[i]
            i += 1
            colour = b >> 7
            row.extend([colour] * ((b & 0x7F) + 1))
        if len(row) != width:
            raise ValueError(f'row {len(rows)} overran: {len(row)} px')
        rows.append(row)
    return rows


def encode(rows, width=WIDTH):
    """Encode rows (lists of 0/1) back into an RLE raster blob."""
    out = bytearray()
    for row in rows:
        if len(row) != width:
            raise ValueError(f'row must be {width} px, got {len(row)}')
        out.append(ROW_MARK)
        i = 0
        while i < width:
            colour = row[i]
            run = 1
            while (i + run < width and row[i + run] == colour
                   and run < 128):
                run += 1
            out.append((colour << 7) | (run - 1))
            i += run
    return bytes(out)


def to_pbm(rows, path):
    with open(path, 'wb') as f:
        f.write(f'P1\n{len(rows[0])} {len(rows)}\n'.encode())
        for row in rows:
            f.write(''.join(map(str, row)).encode() + b'\n')


def main():
    from parse_frames import parse
    data = open(sys.argv[1], 'rb').read()
    blob = max((r[0] for k, _, *r in parse(data) if k == 'gap'), key=len)
    rows = decode(blob)
    ink = sum(sum(r) for r in rows)
    print(f'{len(rows)} rows x {WIDTH} px, {ink} black px '
          f'({100*ink/(len(rows)*WIDTH):.2f}%)')
    # round-trip check
    assert encode(rows) == blob, 'encode(decode(x)) != x'
    print('round-trip: OK')
    if len(sys.argv) > 2:
        to_pbm(rows, sys.argv[2])
        print(f'wrote {sys.argv[2]}')


if __name__ == '__main__':
    main()
