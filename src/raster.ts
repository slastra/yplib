import type { RasterRow } from './protocol.js';

/**
 * 1-bit cutoff. Matches the hardware-verified Python renderer: a pixel at or
 * above this luma leaves the stock bare, below it the head fires.
 */
export const THRESHOLD = 128;

/** The subset of ImageData this module needs, so Node callers need no DOM. */
export interface PixelSource {
	data: Uint8ClampedArray;
	width: number;
	height: number;
}

/**
 * BT.601 luma composited over white paper. THE print-fidelity formula.
 *
 * Every 1-bit conversion anywhere in a toolchain must route through this one
 * function, or a preview and the paper silently disagree. It lives here rather
 * than in each renderer precisely so there is only ever one copy: alpha is
 * composited against white because unburned stock is what shows through, so a
 * half-transparent grey is lighter on paper than it looks on a dark screen.
 *
 * @param data RGBA bytes
 * @param i    byte offset of the pixel (4 bytes per pixel)
 */
export function lumaOverWhite(data: Uint8ClampedArray, i: number): number {
	const a = data[i + 3] / 255;
	return (0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2]) * a + 255 * (1 - a);
}

/**
 * Reduce RGBA pixels to the printer's rows: 1 where the head fires, 0 where it
 * does not. Row length is the source width, and `buildStream` will refuse the
 * result unless that equals the media width in dots, so crop before calling.
 *
 * Pure over plain arrays — pass an ImageData in the browser, or any
 * `{data, width, height}` from sharp, node-canvas, or a hand-built buffer.
 */
export function imageDataToRows(src: PixelSource, threshold = THRESHOLD): RasterRow[] {
	const { data, width, height } = src;
	const rows: RasterRow[] = [];
	for (let y = 0; y < height; y++) {
		const row = new Uint8Array(width);
		for (let x = 0; x < width; x++) {
			row[x] = lumaOverWhite(data, (y * width + x) * 4) < threshold ? 1 : 0;
		}
		rows.push(row);
	}
	return rows;
}
