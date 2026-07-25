/**
 * yplib — the YPL thermal label printer protocol.
 *
 * This entry point is pure: no DOM, no Node APIs, no dependencies. It runs
 * anywhere JavaScript does. The Web Bluetooth transport lives behind
 * `yplib/web-bluetooth` so importing the protocol never drags browser types in.
 */

export {
	WIDTH,
	HEIGHT,
	ROW_MARK,
	crc32,
	hexToBytes,
	toHex,
	frame,
	request,
	preamble,
	PREAMBLE,
	TRAILER,
	encodeRaster,
	decodeRaster,
	buildStream,
	parseReplies,
	STATUS_FLAGS,
	describeStatus,
	selftest,
	type RasterRow,
	type Reply
} from './protocol.js';

export { THRESHOLD, lumaOverWhite, imageDataToRows, type PixelSource } from './raster.js';

export { waitReady, printJob, type Link, type PrintJobOptions } from './transport.js';
