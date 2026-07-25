#!/usr/bin/env python3
"""Solve the Y50P 4-byte frame checksum.

Observation that cracks it: the checksum is GF(2)-affine (verified below), and
the XOR of two checksums whose payloads differ only in the final byte by 0x01
is 0x77073096 little-endian -- entry 1 of the standard reflected CRC-32 table
(poly 0xEDB88320). So the algorithm is ordinary CRC-32; only the init/xorout and
the covered byte range were unknown.
"""
import zlib

# (payload_hex, checksum_hex) pairs harvested from captures/ by parse_frames.py
SAMPLES = [
    ('0104010000', 'bbe290f1'),
    ('01b7010000', 'cf0357fe'),
    ('0107010000', '554d25e3'),
    ('0102010000', '67bdfbd4'),
    ('050b010000', '2d54735c'),
    ('0519010000', '39cb63a6'),
    ('0536010000', '5174325e'),
    ('0521010000', '771bfc93'),
    ('0537010000', '34138ee6'),
    ('051a010000', 'd764d6b4'),
    ('052001010000', '8eb45220'),
    ('051101010008', '8aadc8b2'),
    ('053904010013', '91f0c2fe'),
    ('053904010008', '7d39a774'),
    ('053904010009', 'eb09a003'),
    ('05410402003200', '0ebbaf2c'),
    ('05380402003200', '07e78200'),
    ('05400402000000', 'da3c830a'),
]

PAIRS = [(bytes.fromhex(p), bytes.fromhex(c)) for p, c in SAMPLES]


def check_linearity():
    """CRC-like functions are affine: chk(a)^chk(b) depends only on a^b."""
    print('--- linearity check (same-length payloads)')
    by_len = {}
    for p, c in PAIRS:
        by_len.setdefault(len(p), []).append((p, c))
    ok = True
    for n, group in sorted(by_len.items()):
        if len(group) < 3:
            continue
        (p0, c0), (p1, c1), (p2, c2) = group[:3]
        d01 = int.from_bytes(c0, 'little') ^ int.from_bytes(c1, 'little')
        d02 = int.from_bytes(c0, 'little') ^ int.from_bytes(c2, 'little')
        d12 = int.from_bytes(c1, 'little') ^ int.from_bytes(c2, 'little')
        good = (d01 ^ d02) == d12
        ok &= good
        print(f'  len={n}: d01^d02 == d12 ? {good}')
    print(f'  => affine: {ok}\n')
    return ok


def crc_raw(data, init=0):
    """Reflected CRC-32 (poly 0xEDB88320) with no final xorout."""
    return zlib.crc32(data, init ^ 0xFFFFFFFF) ^ 0xFFFFFFFF


def framed(payload):
    """Full on-wire header + payload."""
    return b'\x1a\x01' + len(payload).to_bytes(2, 'little') + payload


CANDIDATES = {
    'crc32(payload)':            lambda p: zlib.crc32(p),
    'crc32(frame hdr+payload)':  lambda p: zlib.crc32(framed(p)),
    'crc32(payload) ^ ffffffff': lambda p: zlib.crc32(p) ^ 0xFFFFFFFF,
    'crc_raw(payload) init=0':   lambda p: crc_raw(p),
    'crc_raw(hdr+payload) i=0':  lambda p: crc_raw(framed(p)),
    'crc_raw(payload) i=~0':     lambda p: crc_raw(p, 0xFFFFFFFF),
    'crc_raw(hdr+payload) i=~0': lambda p: crc_raw(framed(p), 0xFFFFFFFF),
}


def try_candidates():
    print('--- candidate search (checksum read little-endian)')
    for name, fn in CANDIDATES.items():
        for order in ('little', 'big'):
            residuals = {}
            for p, c in PAIRS:
                want = int.from_bytes(c, order)
                residuals.setdefault(len(p), set()).add(fn(p) ^ want)
            per_len = {n: rs.pop() for n, rs in residuals.items()
                       if len(rs) == 1}
            if len(per_len) != len(residuals):
                continue  # not even constant within a length group
            allsame = len(set(per_len.values())) == 1
            tag = 'EXACT MATCH' if allsame and not any(per_len.values()) else \
                  ('constant xorout' if allsame else 'per-length residual')
            print(f'  {name:28} [{order:6}] {tag}: '
                  + ' '.join(f'{n}:{v:08x}' for n, v in sorted(per_len.items())))


if __name__ == '__main__':
    check_linearity()
    try_candidates()
