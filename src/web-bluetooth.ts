import { parseReplies, request } from './protocol.js';
import type { Link } from './transport.js';

const SERVICE = 0x18f0;
const WRITE_UUID = 0x2af1;
const NOTIFY_UUID = 0x2af0;

export interface ConnectOptions {
	/**
	 * Device name prefix to match. The hardware is white-labelled, so this is
	 * the knob to turn for a sibling model: 'Y50P', 'U8', and so on.
	 */
	namePrefix?: string;
	/** Bytes per GATT write. 20 fits the 23-byte default ATT MTU. */
	chunkSize?: number;
	/** Delay between writes, in ms. See the warning on `send` below. */
	paceMs?: number;
	/** How long to wait for a status reply before giving up on it. */
	statusTimeoutMs?: number;
	/** Fires if the printer drops the GATT link. */
	onDisconnect?: () => void;
}

/** A connected printer. Extends {@link Link}, so it drives `printJob` directly. */
export interface BluetoothLink extends Link {
	readonly deviceName: string;
	disconnect(): void;
}

/** Whether this browser exposes Web Bluetooth at all (Chromium only). */
export function hasBluetooth(): boolean {
	return typeof navigator !== 'undefined' && 'bluetooth' in navigator;
}

/** Web Bluetooth is unavailable on insecure origins, localhost excepted. */
export function isSecureContext(): boolean {
	return typeof window !== 'undefined' && window.isSecureContext;
}

/**
 * Both conditions the browser must meet. Check the two separately when you
 * want to tell the user *which* one failed, since the fixes differ: a missing
 * API means the wrong browser, an insecure context means the wrong origin.
 */
export function isSupported(): boolean {
	return hasBluetooth() && isSecureContext();
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * Connect to a printer over Web Bluetooth.
 *
 * Discovery matches on device NAME, never on service UUID. This is not a
 * preference: a service-UUID filter makes Chrome push a `SetDiscoveryFilter`
 * UUID list to BlueZ, which segfaults `bluetoothd` 5.87 on desktop Linux and
 * takes the user's whole Bluetooth stack down with it. `getDevices()` is tried
 * first so an already-permitted printer reconnects with no chooser at all.
 *
 * Must be called from a user gesture, per the Web Bluetooth spec.
 */
export async function connect(options: ConnectOptions = {}): Promise<BluetoothLink> {
	const {
		namePrefix = 'Y50P',
		chunkSize = 20,
		paceMs = 8,
		statusTimeoutMs = 500,
		onDisconnect
	} = options;

	const device = await pickDevice(namePrefix);
	if (!device.gatt) throw new Error('device has no GATT server');

	let inbox: number[] = [];
	let statusWaiter: ((status: number | null) => void) | null = null;

	device.addEventListener('gattserverdisconnected', () => onDisconnect?.());

	const service = await (await device.gatt.connect()).getPrimaryService(SERVICE);
	const writeChar = await service.getCharacteristic(WRITE_UUID);
	const notifyChar = await service.getCharacteristic(NOTIFY_UUID);
	await notifyChar.startNotifications();

	notifyChar.addEventListener('characteristicvaluechanged', (e) => {
		const value = (e.target as BluetoothRemoteGATTCharacteristic).value;
		if (!value) return;
		inbox.push(...new Uint8Array(value.buffer));
		if (!statusWaiter) return;
		// resolve the moment the status frame lands, rather than on a timer
		const hit = parseReplies(Uint8Array.from(inbox)).find(
			(r) => r.group === 0x05 && r.cmd === 0x0b
		);
		if (!hit) return;
		const resolve = statusWaiter;
		statusWaiter = null;
		resolve(typeof hit.value === 'number' ? hit.value : (hit.value[0] ?? 0));
	});

	const link: BluetoothLink = {
		deviceName: device.name ?? namePrefix,

		/**
		 * `writeValueWithoutResponse` has no backpressure, so unpaced writes
		 * outrun the printer's buffer and the job arrives corrupted. The chunk
		 * is copied rather than a subarray view: the hardware-verified driver
		 * sends a copy, and a shared buffer is not worth the risk on a link
		 * that cannot tell you it dropped something.
		 */
		async send(bytes: Uint8Array) {
			for (let i = 0; i < bytes.length; i += chunkSize) {
				await writeChar.writeValueWithoutResponse(
					bytes.slice(i, i + chunkSize) as Uint8Array<ArrayBuffer>
				);
				await sleep(paceMs);
			}
		},

		async readStatus() {
			inbox = [];
			const reply = new Promise<number | null>((resolve) => {
				statusWaiter = resolve;
				// a silent printer resolves null rather than hanging the caller
				setTimeout(() => {
					if (statusWaiter === resolve) {
						statusWaiter = null;
						resolve(null);
					}
				}, statusTimeoutMs);
			});
			await link.send(request(0x05, 0x0b));
			return reply;
		},

		disconnect() {
			device.gatt?.disconnect();
		}
	};

	return link;
}

async function pickDevice(namePrefix: string): Promise<BluetoothDevice> {
	if (navigator.bluetooth.getDevices) {
		try {
			const known = await navigator.bluetooth.getDevices();
			const hit = known.find((d) => (d.name ?? '').startsWith(namePrefix));
			if (hit) return hit;
		} catch {
			// permissions backend unavailable — fall through to a chooser
		}
	}
	return navigator.bluetooth.requestDevice({
		filters: [{ namePrefix }],
		optionalServices: [SERVICE]
	});
}
