import { describe, expect, test } from 'bun:test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { buildStream, decodeRaster, parseReplies, toHex } from '../src/protocol.js';

/**
 * Conformance against the real hardware captures.
 *
 * Unit vectors prove the pieces; this proves the whole. Each capture is a
 * recording of the vendor app driving the printer, and the test rebuilds the
 * entire session byte-for-byte from nothing but its own decoded raster. If a
 * CRC, a run length, a frame boundary or a preamble byte were wrong, the
 * rebuild would differ — so a pass means this implementation is
 * indistinguishable from the vendor's on the wire.
 */

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const load = (name: string) => new Uint8Array(readFileSync(join(root, 'captures', name)));

/** End offset of a well-formed frame starting at `i`, or 0 if there is none. */
function frameAt(buf: Uint8Array, i: number): number {
	if (buf[i] !== 0x1a || buf[i + 1] !== 0x01 || i + 4 > buf.length) return 0;
	const len = buf[i + 2] | (buf[i + 3] << 8);
	const end = i + 4 + len + 4;
	return end < buf.length && buf[end] === 0xa1 ? end + 1 : 0;
}

/**
 * Locate the unframed raster blob.
 *
 * It is spliced in raw between the 0x39 job frame and the trailer, so it has no
 * length field of its own. 0x1a is itself a legal run byte (27 white px), so
 * the end cannot be found by scanning for the frame magic alone: a candidate
 * only counts if a complete frame actually parses there.
 */
function rasterOf(buf: Uint8Array): Uint8Array {
	let start = -1;
	for (let i = 0; i < buf.length;) {
		const next = frameAt(buf, i);
		if (!next) {
			i++;
			continue;
		}
		if (buf[i + 4] === 0x05 && buf[i + 5] === 0x39) {
			start = next;
			break;
		}
		i = next;
	}
	if (start < 0) throw new Error('no 0x39 job frame found');
	let end = start;
	while (end < buf.length && !frameAt(buf, end)) end++;
	return buf.slice(start, end);
}

const CAPTURES = [
	'y50p-flashlabel-label.bin',
	'y50p-horizontal-line.bin',
	'y50p-vertical-line.bin'
] as const;

describe('captures decode', () => {
	for (const name of CAPTURES) {
		test(`${name} yields a full 240-row raster`, () => {
			const rows = decodeRaster(rasterOf(load(name)));
			expect(rows).toHaveLength(240);
			expect(rows[0]).toHaveLength(400);
		});

		test(`${name} frames parse with valid CRC`, () => {
			expect(parseReplies(load(name)).length).toBeGreaterThan(0);
		});
	}
});

describe('byte-for-byte rebuild', () => {
	// The horizontal-line snoop was stopped mid-trailer, so it holds only the
	// first trailer frame. The vertical-line capture starts mid-session with no
	// handshake, so it cannot be rebuilt from scratch — it is covered above by
	// decode plus the x=198 run vector pinned in selftest().
	const REBUILDABLE: [name: string, job: number, trailer?: string[]][] = [
		['y50p-flashlabel-label.bin', 0x13, undefined],
		['y50p-horizontal-line.bin', 0x08, ['0521010000']]
	];

	for (const [name, job, trailer] of REBUILDABLE) {
		test(`${name} rebuilds exactly`, () => {
			const buf = load(name);
			const rebuilt = buildStream(decodeRaster(rasterOf(buf)), 50, job, trailer);
			expect(toHex(rebuilt)).toBe(toHex(buf));
		});
	}
});
