#!/usr/bin/env python3
"""Recover the CRC-32 init and xorout for the Y50P frame checksum.

solve_checksum.py established the polynomial is the standard reflected CRC-32
(0xEDB88320) and that residual = crc_raw(payload, init=0) ^ chk is constant
within each payload-length group but varies across lengths. That is exactly the
signature of a non-zero init value, since

    chk(m) = crc_raw(m, 0) ^ Z(n, I) ^ X

where n = len(m), X = xorout, and Z(n, I) is the register after clocking n zero
bytes from init I. Z is GF(2)-linear in I, so subtracting equations for two
lengths eliminates X and leaves a linear system we can solve for I directly.
"""
import zlib

from solve_checksum import PAIRS


def crc_raw(data, init=0):
    return zlib.crc32(data, init ^ 0xFFFFFFFF) ^ 0xFFFFFFFF


def Z(n, init):
    """Register value after clocking n zero bytes starting from `init`."""
    return crc_raw(b'\x00' * n, init)


def residuals():
    out = {}
    for p, c in PAIRS:
        r = crc_raw(p) ^ int.from_bytes(c, 'little')
        out.setdefault(len(p), set()).add(r)
    assert all(len(v) == 1 for v in out.values()), 'residual not constant'
    return {n: v.pop() for n, v in out.items()}


def solve_gf2(rows, nbits=32):
    """Solve A·x = b over GF(2). rows = [(a_bitmask, b_bit), ...]."""
    piv = {}
    for a, b in rows:
        for i in range(nbits):
            if not (a >> i) & 1:
                continue
            if i in piv:
                pa, pb = piv[i]
                a ^= pa
                b ^= pb
            else:
                piv[i] = (a, b)
                break
        else:
            if b:
                return None  # inconsistent
    x = 0
    for i in sorted(piv, reverse=True):
        a, b = piv[i]
        # back-substitute bits above i that are already fixed
        v = b ^ (bin(a & x & ~(1 << i)).count('1') & 1)
        if v:
            x |= 1 << i
    return x


def main():
    r = residuals()
    print('--- residuals by payload length')
    for n, v in sorted(r.items()):
        print(f'  n={n}: {v:08x}')

    # Basis for the linear map I -> Z(n1,I) ^ Z(n2,I)
    lens = sorted(r)
    rows = []
    for n1, n2 in zip(lens, lens[1:]):
        target = r[n1] ^ r[n2]
        basis = [Z(n1, 1 << k) ^ Z(n2, 1 << k) for k in range(32)]
        for bit in range(32):
            a = 0
            for k in range(32):
                if (basis[k] >> bit) & 1:
                    a |= 1 << k
            rows.append((a, (target >> bit) & 1))

    I = solve_gf2(rows)
    if I is None:
        print('\nNo consistent init -- model is wrong.')
        return
    X = Z(lens[0], I) ^ r[lens[0]]
    print(f'\n  init   = 0x{I:08x}')
    print(f'  xorout = 0x{X:08x}')

    print('\n--- verify against every captured sample')
    ok = True
    for p, c in PAIRS:
        got = crc_raw(p, I) ^ X
        want = int.from_bytes(c, 'little')
        good = got == want
        ok &= good
        if not good:
            print(f'  MISMATCH {p.hex()}: got {got:08x} want {want:08x}')
    print(f'  all {len(PAIRS)} samples match: {ok}')


if __name__ == '__main__':
    main()
