# yplib

[![npm](https://img.shields.io/npm/v/@slastra/yplib)](https://www.npmjs.com/package/@slastra/yplib)
[![license](https://img.shields.io/npm/l/@slastra/yplib)](./LICENSE)

**The YPL thermal label printer protocol, in TypeScript.** Framing, CRC, 1-bit
raster encoding, and a Web Bluetooth transport. No dependencies, no DOM in the
core, and every constant verified against hardware captures.

```bash
npm install @slastra/yplib
```

> Published under a scope because npm's typosquatting filter rejects the bare
> name `yplib` as too close to `otplib`, `zlib` and `tslib`.

YPL is the wire format spoken by a family of cheap white-labelled thermal label
printers sold as **KNAON**, **FlashToy** and others. It is **not TSPL**, despite
what the vendor tooling around these devices might suggest. See
[FINDINGS.md](./FINDINGS.md) for how it was derived.

## Quick start

Print a label from a canvas in the browser:

```ts
import { buildStream, imageDataToRows } from '@slastra/yplib';
import { connect } from '@slastra/yplib/web-bluetooth';

const ctx = canvas.getContext('2d')!; // 400 × 240 for 50 × 30 mm stock
const rows = imageDataToRows(ctx.getImageData(0, 0, canvas.width, canvas.height));

const printer = await connect(); // must be called from a user gesture
await printer.send(buildStream(rows, 50));
```

Batch printing, with progress and cancellation:

```ts
import { printJob } from '@slastra/yplib';

const ac = new AbortController();
const printed = await printJob(
	printer,
	rowsPerLabel.map((rows) => () => Promise.resolve(buildStream(rows, 50))),
	{ signal: ac.signal, onProgress: (done, total) => console.log(`${done}/${total}`) }
);
```

Labels are built lazily and pipelined: label _n+1_ renders while label _n_ is
still transferring. The job checks printer status between labels, so a jam or an
open cover stops the run instead of spooling into a printer that cannot take it.

## What it does not do

**Rendering is yours.** This library turns pixels into wire bytes; it does not
decide how your image becomes 1-bit. `lumaOverWhite` and `imageDataToRows` are
provided because getting the luma formula wrong makes previews disagree with
paper, but thresholding policy, dithering and layout stay in your hands.

For a full label designer built on this, see
[printrow](https://github.com/slastra/printrow).

## API

### Core — `@slastra/yplib`

Pure. Runs in browsers, Node, Bun and Deno alike, with no dependencies.

|                                                     |                                                            |
| --------------------------------------------------- | ---------------------------------------------------------- |
| `frame(payload)`                                    | Wrap a payload as `1a 01 <len> <payload> <crc32> a1`       |
| `crc32(bytes)`                                      | Reflected CRC-32, poly `0xEDB88320`, **init `0xCA896ADE`** |
| `encodeRaster(rows)` / `decodeRaster(blob)`         | 1-bit RLE codec                                            |
| `buildStream(rows, widthMm, job?, trailer?)`        | A complete print job                                       |
| `parseReplies(buf)`                                 | Resync-tolerant inbound frame parser                       |
| `describeStatus(v)` / `STATUS_FLAGS`                | Human-readable printer status                              |
| `imageDataToRows(src, threshold?)`                  | RGBA pixels → printer rows                                 |
| `lumaOverWhite(data, i)`                            | BT.601 luma composited over white stock                    |
| `printJob(link, builds, opts?)` / `waitReady(link)` | Job orchestration                                          |
| `selftest()`                                        | Runs the capture-derived vectors; `[]` means pass          |

### Transport — `@slastra/yplib/web-bluetooth`

`connect(options?)` returns a `Link` with `send`, `readStatus`, `disconnect` and
`deviceName`. Options: `namePrefix` (default `'Y50P'`), `chunkSize` (20),
`paceMs` (8), `statusTimeoutMs` (500), `onDisconnect`.

`isSupported()` checks both requirements at once; `hasBluetooth()` and
`isSecureContext()` check them separately, because the fixes differ — a missing
API means the wrong browser, an insecure context means the wrong origin.

Implement the two-method `Link` interface yourself to drive the same protocol
over Web Serial, a classic SPP socket, or `/dev/usb/lpN`. The protocol is
transport-agnostic; it was verified over all three.

## Two things that will bite you

**Raster rows carry no length field.** A row that is not exactly the media width
in dots shifts every following row marker, and the firmware can hang. This is
not theoretical — it crashed a printer twice during development. `buildStream`
refuses to encode a wrong-width raster rather than let you find out on hardware.

**Never filter discovery by service UUID.** A service-UUID filter makes Chrome
push a `SetDiscoveryFilter` UUID list to BlueZ, which **segfaults `bluetoothd`
5.87** on desktop Linux and takes the user's entire Bluetooth stack down.
`connect()` filters by device name for this reason.

## Hardware

Verified on a **KNAON Y50P**, 50 × 30 mm stock at 8 dots/mm (400 × 240), over
USB, classic Bluetooth SPP and BLE. Other media heights are safe, because the
protocol never transmits height: the printer takes rows until the raster ends.
Widths other than 50 mm follow the captured frame format but are untested on
real stock.

The hardware is white-labelled, so the badge on the case is not the giveaway,
and neither is the USB vendor ID (`0x5958` is unregistered and shared with
printers that speak TSPL instead). What settles it is the wire format:

> Frames that start `1a 01`, end `a1`, and checksum as CRC-32 with init
> `0xCA896ADE` are this protocol, whatever the label says.

`reference/parse_frames.py` answers that question directly against a capture.

## Development

```bash
bun install
bun test        # unit vectors, capture conformance, transport
bun run check   # tsc --noEmit
bun run build   # dist/
```

The test suite rebuilds real hardware captures **byte for byte** from their own
decoded rasters. A pass means this implementation is indistinguishable from the
vendor's app on the wire.

## Credits

The protocol is named YPL after
[Souukou/OpenBluetoothPrinter](https://github.com/Souukou/OpenBluetoothPrinter),
whose work on the sibling FlashToy U8 supplied the command names and three of
the status bits used here. See [ACKNOWLEDGEMENTS.md](./ACKNOWLEDGEMENTS.md).

## Licence

MIT
