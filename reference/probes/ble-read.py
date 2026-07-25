#!/usr/bin/env python3
"""Read every readable GATT characteristic and listen on every notify one.

Strictly passive: no writes, so nothing here can move paper or change settings.
Looking for a vendor battery reading, since the Y50P exposes no standard
Battery Service (0x180F).
"""
import asyncio

from bleak import BleakClient, BleakScanner

ADDR = 'YY:YY:YY:YY:YY:YY'
LISTEN_SECONDS = 8


async def main():
    device = await BleakScanner.find_device_by_address(ADDR, timeout=15)
    if device is None:
        raise SystemExit(f'{ADDR} not found in LE scan')
    async with BleakClient(device, timeout=20) as client:
        print(f'connected: {client.is_connected}')

        print('\n--- readable characteristics')
        for service in client.services:
            for ch in service.characteristics:
                if 'read' not in ch.properties:
                    continue
                try:
                    v = await client.read_gatt_char(ch)
                    txt = ''.join(chr(b) if 32 <= b < 127 else '.' for b in v)
                    print(f'  {ch.uuid}  {len(v)}B  {v.hex()}  |{txt}|')
                except Exception as e:
                    print(f'  {ch.uuid}  read failed: {e}')

        print(f'\n--- listening on notify characteristics ({LISTEN_SECONDS}s)')
        got = []

        def make_cb(uuid):
            def cb(_, data):
                got.append((uuid, bytes(data)))
                txt = ''.join(chr(b) if 32 <= b < 127 else '.' for b in data)
                print(f'  NOTIFY {uuid}  {data.hex()}  |{txt}|')
            return cb

        subscribed = []
        for service in client.services:
            for ch in service.characteristics:
                if 'notify' not in ch.properties and \
                        'indicate' not in ch.properties:
                    continue
                try:
                    await client.start_notify(ch, make_cb(ch.uuid))
                    subscribed.append(ch)
                except Exception as e:
                    print(f'  {ch.uuid}  subscribe failed: {e}')
        print(f'  subscribed to {len(subscribed)}')
        await asyncio.sleep(LISTEN_SECONDS)
        for ch in subscribed:
            try:
                await client.stop_notify(ch)
            except Exception:
                pass
        print(f'  {len(got)} notifications received')


asyncio.run(main())
