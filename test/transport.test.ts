import { describe, expect, test } from 'bun:test';
import { printJob, waitReady, type Link } from '../src/transport.js';

/**
 * A printer that answers instantly. `statuses` is consumed one per readStatus
 * call; once exhausted it reports ready forever.
 */
function fakeLink(statuses: (number | null)[] = []) {
	const sent: Uint8Array[] = [];
	const reads: number[] = [];
	const link: Link = {
		async send(bytes) {
			sent.push(bytes);
		},
		async readStatus() {
			reads.push(sent.length);
			return statuses.length ? (statuses.shift() as number | null) : 0;
		}
	};
	return { link, sent, reads };
}

const label = (n: number) => () => Promise.resolve(new Uint8Array([n]));

describe('waitReady', () => {
	test('returns as soon as the printer reports ready', async () => {
		const { link } = fakeLink([0]);
		expect(await waitReady(link)).toBeUndefined();
	});

	test('names the fault rather than hanging', async () => {
		const { link } = fakeLink([0x04]);
		expect(waitReady(link)).rejects.toThrow(/out of paper/);
	});

	test('cover open reports only the cover, not the paper it forces', async () => {
		// 0x02 forces 0x04 because the sensor rides up with the head, so
		// reporting both would send someone looking for a paper jam
		const { link } = fakeLink([0x06]);
		expect(waitReady(link)).rejects.toThrow(/cover open/);
	});

	test('a silent printer times out instead of looping forever', async () => {
		const { link } = fakeLink([null, null, null, null]);
		expect(waitReady(link, 1)).rejects.toThrow(/not responding/);
	});
});

describe('printJob', () => {
	test('prints every label and reports progress once each', async () => {
		const { link, sent } = fakeLink();
		const seen: [number, number][] = [];
		const done = await printJob(link, [label(1), label(2), label(3)], {
			onProgress: (d, t) => seen.push([d, t])
		});
		expect(done).toBe(3);
		expect(sent.map((b) => b[0])).toEqual([1, 2, 3]);
		expect(seen).toEqual([
			[1, 3],
			[2, 3],
			[3, 3]
		]);
	});

	test('waits for ready before every label, not just the first', async () => {
		const { link, reads } = fakeLink();
		await printJob(link, [label(1), label(2), label(3)]);
		// each readStatus happened before its label went out
		expect(reads).toEqual([0, 1, 2]);
	});

	test('builds lazily, so a large batch does not rasterize up front', async () => {
		const built: number[] = [];
		const make = (n: number) => () => {
			built.push(n);
			return Promise.resolve(new Uint8Array([n]));
		};
		const { link } = fakeLink();
		await printJob(link, [make(1), make(2), make(3)]);
		expect(built).toEqual([1, 2, 3]);
	});

	test('renders the next label while the current one transfers', async () => {
		// label 2 must start building before label 1 finishes sending, or the
		// render cost is serialised behind the wire time for the whole batch
		const order: string[] = [];
		const slowSend: Link = {
			async send() {
				order.push('send-start');
				await new Promise((r) => setTimeout(r, 10));
				order.push('send-end');
			},
			async readStatus() {
				return 0;
			}
		};
		const builds = [
			() => Promise.resolve(new Uint8Array([1])),
			() => {
				order.push('build-2');
				return Promise.resolve(new Uint8Array([2]));
			}
		];
		await printJob(slowSend, builds);
		expect(order.indexOf('build-2')).toBeLessThan(order.indexOf('send-end'));
	});

	test('an aborted job stops between labels and reports how many landed', async () => {
		const { link, sent } = fakeLink();
		const ac = new AbortController();
		const done = await printJob(link, [label(1), label(2), label(3)], {
			signal: ac.signal,
			onProgress: (d) => {
				if (d === 1) ac.abort();
			}
		});
		expect(done).toBe(1);
		expect(sent).toHaveLength(1); // never mid-label
	});

	test('a printer fault aborts the batch instead of spooling into it', async () => {
		const { link, sent } = fakeLink([0, 0x04]);
		expect(printJob(link, [label(1), label(2)])).rejects.toThrow(/out of paper/);
		await Bun.sleep(5);
		expect(sent.length).toBeLessThan(2);
	});

	test('an empty job is a no-op', async () => {
		const { link, sent } = fakeLink();
		expect(await printJob(link, [])).toBe(0);
		expect(sent).toHaveLength(0);
	});
});
