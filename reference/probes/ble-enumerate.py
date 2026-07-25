#!/usr/bin/env python3
"""Enumerate GATT services/characteristics on the Y50P LE personality."""
import asyncio
from bleak import BleakClient, BleakScanner

ADDR = 'YY:YY:YY:YY:YY:YY'

async def main():
    device = await BleakScanner.find_device_by_address(ADDR, timeout=15)
    if device is None:
        raise SystemExit(f'{ADDR} not found in LE scan - is it advertising?')
    print(f'found: {device.name}')
    async with BleakClient(device, timeout=20) as client:
        print(f'connected: {client.is_connected}, mtu: {client.mtu_size}')
        for service in client.services:
            print(f'\n[service] {service.uuid}  ({service.description})')
            for ch in service.characteristics:
                props = ','.join(ch.properties)
                print(f'  [char] {ch.uuid}  props={props}')
                for d in ch.descriptors:
                    print(f'    [desc] {d.uuid}')

asyncio.run(main())
