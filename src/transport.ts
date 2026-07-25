import { describeStatus } from './protocol.js';

/**
 * The minimum a transport must provide. Anything that can push bytes at the
 * printer and read a status byte back can drive a print job: Web Bluetooth,
 * Web Serial, a classic SPP socket, or `/dev/usb/lpN`. The protocol itself is
 * transport-agnostic, verified on all three of USB, SPP and BLE.
 */
export interface Link {
	/** Write a complete byte stream, chunking and pacing as the link requires. */
	send(bytes: Uint8Array): Promise<void>;
	/** Current status byte, or null if the printer did not answer in time. */
	readStatus(): Promise<number | null>;
}

export interface PrintJobOptions {
	/** Called after each label lands, for progress reporting. */
	onProgress?: (done: number, total: number) => void;
	/** Abort between labels. A job stops cleanly, it does not tear mid-label. */
	signal?: AbortSignal;
	/** How long to wait for the printer to become ready between labels. */
	readyTimeoutMs?: number;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * Poll until the printer reports ready.
 *
 * `0x01` (printing) keeps waiting; cover-open, out-of-paper or silence past the
 * timeout throws, so a batch stops at the failed label instead of spooling
 * bytes into a printer that cannot take them.
 */
export async function waitReady(link: Link, timeoutMs = 15000): Promise<void> {
	const t0 = Date.now();
	for (;;) {
		const s = await link.readStatus();
		if (s === 0) return;
		if (s !== null && s !== 0x01) throw new Error(`printer not ready: ${describeStatus(s)}`);
		if (Date.now() - t0 > timeoutMs) {
			throw new Error(s === null ? 'printer not responding' : 'timed out waiting for printer');
		}
		await sleep(300);
	}
}

/**
 * Run a print job. Each entry builds one label's byte stream, lazily, so a
 * thousand-row batch does not rasterize a thousand labels up front.
 *
 * Label i+1 is built while label i is still transferring, which hides almost
 * all of the render cost behind the wire time. Returns the number printed,
 * which is less than `builds.length` if the signal aborted.
 */
export async function printJob(
	link: Link,
	builds: (() => Promise<Uint8Array>)[],
	{ onProgress, signal, readyTimeoutMs }: PrintJobOptions = {}
): Promise<number> {
	let done = 0;
	let next: Promise<Uint8Array> | undefined = builds[0]?.();
	for (let i = 0; i < builds.length; i++) {
		if (signal?.aborted) break;
		const stream = await next!;
		await waitReady(link, readyTimeoutMs);
		next = builds[i + 1]?.(); // render the next label during this transfer
		await link.send(stream);
		done = i + 1;
		onProgress?.(done, builds.length);
	}
	return done;
}
