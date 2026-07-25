#!/usr/bin/env python3
"""Test the BLE path: same protocol, written to the 18f0/2af1 characteristic.

The BLE route is the last unverified transport. It matters because Web Bluetooth
is BLE-only, so it is the only way a browser could ever reach this printer over
radio. Prior probes established that 2af1 accepts bytes and made the printer
feed a label -- but feeding blank paper is also what it does with bytes it
cannot parse, so that proved nothing.

Runs in two steps on purpose:
  1. a status query, which moves no paper, proving the link works both ways
  2. optionally a real print

Usage:
    python3 probes/ble-print.py                 # status query only
    python3 probes/ble-print.py stream.bin      # ...then print the stream
"""
import asyncio
import sys

from bleak import BleakClient, BleakScanner

sys.path.insert(0, __file__.rsplit('/', 2)[0])
from y50p import QUERIES, describe_status, read_replies, request  # noqa: E402

ADDR = 'YY:YY:YY:YY:YY:YY'          # LE personality; classic is 10:23:...
WRITE_CHAR = '00002af1-0000-1000-8000-00805f9b34fb'
NOTIFY_CHAR = '00002af0-0000-1000-8000-00805f9b34fb'


async def main():
    stream_path = sys.argv[1] if len(sys.argv) > 1 else None
    device = await BleakScanner.find_device_by_address(ADDR, timeout=20)
    if device is None:
        raise SystemExit(f'{ADDR} not found in LE scan')

    async with BleakClient(device, timeout=25) as client:
        chunk = max(20, client.mtu_size - 3)
        print(f'connected  mtu={client.mtu_size}  chunk={chunk}')

        replies = bytearray()
        await client.start_notify(NOTIFY_CHAR,
                                  lambda _, d: replies.extend(bytes(d)))

        async def send(data, label):
            for i in range(0, len(data), chunk):
                await client.write_gatt_char(WRITE_CHAR, data[i:i + chunk],
                                             response=False)
                await asyncio.sleep(0.01)
            print(f'  sent {len(data)} bytes ({label})')

        # --- step 1: read-only queries
        print('\n--- status over BLE')
        for name in ('model', 'firmware', 'serial', 'status'):
            replies.clear()
            await send(request(*QUERIES[name]), name)
            await asyncio.sleep(1.0)
            got = read_replies(bytes(replies))
            if not got:
                print(f'  {name:9} (no reply)  raw={bytes(replies).hex()}')
                continue
            for g, c, d, value in got:
                if (g, c) != QUERIES[name]:
                    continue
                if name == 'status':
                    b = value[0] if isinstance(value, bytes) and value else value
                    print(f'  {name:9} 0x{b:02x} ({describe_status(b)})')
                else:
                    print(f'  {name:9} {value}')

        # --- step 2: print
        if stream_path:
            data = open(stream_path, 'rb').read()
            print(f'\n--- printing {stream_path}')
            await send(data, 'label')
            await asyncio.sleep(3)

        await client.stop_notify(NOTIFY_CHAR)
    print('done')


asyncio.run(main())
