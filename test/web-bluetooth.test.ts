import { afterEach, describe, expect, test } from 'bun:test';
import { connect, hasBluetooth, isSecureContext, isSupported } from '../src/web-bluetooth.js';
import { frame, toHex } from '../src/protocol.js';

/**
 * A fake GATT stack. The transport is the one part of this library that cannot
 * be verified against a capture, so it gets exercised against a stand-in that
 * records exactly what reached the wire.
 */

/** A status reply carrying `value`: <group><cmd><dir=02><len:u16><03><vlen:u16><v> */
const statusReply = (value: number) =>
	frame(new Uint8Array([0x05, 0x0b, 0x02, 0x04, 0x00, 0x03, 0x01, 0x00, value]));

function fakeStack({ status = 0, silent = false }: { status?: number; silent?: boolean } = {}) {
	const writes: Uint8Array[] = [];
	const stamps: number[] = [];
	let notify: ((e: unknown) => void) | null = null;

	const emit = (bytes: Uint8Array) =>
		notify?.({ target: { value: new DataView(bytes.buffer.slice(0)) } });

	const writeChar = {
		async writeValueWithoutResponse(chunk: Uint8Array) {
			writes.push(new Uint8Array(chunk));
			stamps.push(Date.now());
			// answer a status query the way the printer does, asynchronously
			if (!silent && chunk.length >= 6 && chunk[4] === 0x05 && chunk[5] === 0x0b) {
				setTimeout(() => emit(statusReply(status)), 0);
			}
		}
	};
	const notifyChar = {
		async startNotifications() {},
		addEventListener(_: string, fn: (e: unknown) => void) {
			notify = fn;
		}
	};
	const device = {
		name: 'Y50P_8895_LE',
		addEventListener() {},
		gatt: {
			connect: async () => ({
				getPrimaryService: async () => ({
					getCharacteristic: async (uuid: number) => (uuid === 0x2af1 ? writeChar : notifyChar)
				})
			}),
			disconnect() {}
		}
	};

	const requested: unknown[] = [];
	(globalThis as Record<string, unknown>).navigator = {
		bluetooth: {
			requestDevice: async (opts: unknown) => {
				requested.push(opts);
				return device;
			}
		}
	};
	return { writes, stamps, requested, emit };
}

afterEach(() => {
	delete (globalThis as Record<string, unknown>).navigator;
	delete (globalThis as Record<string, unknown>).window;
});

describe('capability checks', () => {
	test('report the two failure modes separately', () => {
		fakeStack();
		(globalThis as Record<string, unknown>).window = { isSecureContext: false };
		// the fixes differ: wrong browser vs wrong origin
		expect(hasBluetooth()).toBe(true);
		expect(isSecureContext()).toBe(false);
		expect(isSupported()).toBe(false);
	});
});

describe('discovery', () => {
	test('filters by name and never by service UUID', async () => {
		// a service-UUID filter makes Chrome push SetDiscoveryFilter to BlueZ,
		// which segfaults bluetoothd 5.87 and takes the host's stack down
		const { requested } = fakeStack();
		await connect();
		const opts = requested[0] as { filters: { namePrefix: string }[]; optionalServices: number[] };
		expect(opts.filters).toEqual([{ namePrefix: 'Y50P' }]);
		expect(JSON.stringify(opts.filters)).not.toContain('services');
		// the service is still declared, just not used to filter
		expect(opts.optionalServices).toEqual([0x18f0]);
	});

	test('the name prefix is configurable for white-labelled siblings', async () => {
		const { requested } = fakeStack();
		await connect({ namePrefix: 'U8' });
		expect((requested[0] as { filters: unknown[] }).filters).toEqual([{ namePrefix: 'U8' }]);
	});

	test('exposes the device name', async () => {
		fakeStack();
		expect((await connect()).deviceName).toBe('Y50P_8895_LE');
	});
});

describe('send', () => {
	test('splits into 20-byte chunks that reassemble exactly', async () => {
		const { writes } = fakeStack();
		const link = await connect();
		const payload = new Uint8Array(50).map((_, i) => i);
		writes.length = 0;
		await link.send(payload);
		expect(writes.map((w) => w.length)).toEqual([20, 20, 10]);
		expect(toHex(new Uint8Array(writes.flatMap((w) => [...w])))).toBe(toHex(payload));
	});

	test('a stream shorter than one chunk goes out whole', async () => {
		const { writes } = fakeStack();
		const link = await connect();
		writes.length = 0;
		await link.send(new Uint8Array(5));
		expect(writes.map((w) => w.length)).toEqual([5]);
	});

	test('chunk size is configurable', async () => {
		const { writes } = fakeStack();
		const link = await connect({ chunkSize: 8 });
		writes.length = 0;
		await link.send(new Uint8Array(20));
		expect(writes.map((w) => w.length)).toEqual([8, 8, 4]);
	});

	test('paces writes, because the link has no backpressure', async () => {
		// writeValueWithoutResponse never reports a full buffer, so unpaced
		// writes outrun the printer and the job arrives corrupted
		const { writes, stamps } = fakeStack();
		const link = await connect({ paceMs: 20 });
		writes.length = 0;
		stamps.length = 0;
		await link.send(new Uint8Array(60));
		expect(writes).toHaveLength(3);
		expect(stamps[2] - stamps[0]).toBeGreaterThanOrEqual(35);
	});

	test('chunks are copies, not views onto the caller buffer', async () => {
		const { writes } = fakeStack();
		const link = await connect();
		const payload = new Uint8Array(30).fill(7);
		writes.length = 0;
		await link.send(payload);
		payload.fill(0); // mutate after sending
		expect([...writes[0]!]).toEqual(Array(20).fill(7));
	});
});

describe('readStatus', () => {
	test('resolves from the notification, not a timer', async () => {
		fakeStack({ status: 0 });
		const link = await connect();
		const t0 = Date.now();
		expect(await link.readStatus()).toBe(0);
		expect(Date.now() - t0).toBeLessThan(400); // well inside the 500ms timeout
	});

	test('decodes a fault status', async () => {
		fakeStack({ status: 0x06 });
		expect(await (await connect()).readStatus()).toBe(0x06);
	});

	test('a silent printer resolves null rather than hanging the caller', async () => {
		fakeStack({ silent: true });
		const link = await connect({ statusTimeoutMs: 30 });
		const t0 = Date.now();
		expect(await link.readStatus()).toBeNull();
		expect(Date.now() - t0).toBeGreaterThanOrEqual(25);
	});

	test('a later read still works after one timed out', async () => {
		// the waiter must be cleared on timeout, or every subsequent read is
		// resolved by the stale promise and the status never updates again
		const { emit } = fakeStack({ silent: true });
		const link = await connect({ statusTimeoutMs: 20 });
		expect(await link.readStatus()).toBeNull();
		const second = link.readStatus();
		setTimeout(() => emit(statusReply(0x04)), 5);
		expect(await second).toBe(0x04);
	});
});
