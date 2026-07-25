#!/usr/bin/env python3
"""Listen on the vendor notify characteristics with timestamps.

ble-read.py saw ff03 push `01 07` and `02 f4 00` on subscribe, which looks like
<id:1><value> telemetry. This listens long enough to tell whether those are
one-shot values or a periodic feed, and to correlate them with a physical change
(unplugging USB) made while it runs. Passive: no writes.

Usage: python3 probes/ble-listen.py [seconds]
"""
import asyncio
import sys
import time

from bleak import BleakClient, BleakScanner

ADDR = 'YY:YY:YY:YY:YY:YY'
WATCH = ['0000ff03', '0000ff01', '0000ff11', '0000ff12', '0000ff81',
         '0000fff1', '49535343-1e4d', '49535343-aca3']


async def main():
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    device = await BleakScanner.find_device_by_address(ADDR, timeout=15)
    if device is None:
        raise SystemExit(f'{ADDR} not found in LE scan')
    async with BleakClient(device, timeout=20) as client:
        t0 = time.time()
        print(f'connected; listening {seconds}s')

        def make_cb(uuid):
            def cb(_, data):
                b = bytes(data)
                extra = ''
                if len(b) == 3:
                    extra = f'  id={b[0]:02x} u16={int.from_bytes(b[1:], "little")}'
                elif len(b) == 2:
                    extra = f'  id={b[0]:02x} u8={b[1]}'
                print(f'[{time.time() - t0:6.1f}s] {uuid[:8]} {b.hex()}{extra}')
            return cb

        subs = []
        for service in client.services:
            for ch in service.characteristics:
                if not any(ch.uuid.startswith(w) for w in WATCH):
                    continue
                try:
                    await client.start_notify(ch, make_cb(ch.uuid))
                    subs.append(ch)
                except Exception as e:
                    print(f'  {ch.uuid} subscribe failed: {e}')
        print(f'subscribed to {len(subs)}; make the physical change now')
        await asyncio.sleep(seconds)
        for ch in subs:
            try:
                await client.stop_notify(ch)
            except Exception:
                pass
    print('done')


asyncio.run(main())
