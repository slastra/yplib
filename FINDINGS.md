# YPL — protocol findings

How the YPL wire format was reverse-engineered, from HCI snoops of the
manufacturer's Android app (`com.flashlabel.flashlabelpro`) against a **KNAON
Y50P** thermal label printer, and verified by rebuilding whole print sessions
byte for byte.

This is the derivation behind [`yplib`](./README.md). It is deliberately long:
the point is that every constant here is traceable to a capture rather than to
a guess.

## Status: SOLVED ✅

**All three transports verified on hardware**: USB (`/dev/usb/lp1`), classic
Bluetooth SPP, and BLE. The protocol is identical on every one — the same bytes,
including bidirectional status, work unchanged across all three.

- ✅ **Transport**: classic Bluetooth SPP, RFCOMM **channel 1**, raw bytes.
- ✅ **Frame checksum**: standard CRC-32 with a non-standard init (below).
- ✅ **Raster encoding**: 1-bit RLE, 400 px rows (below).
- ✅ **Arbitrary label generation**: `y50p.py` builds labels from text or an
  image and prints them. Bench-verified on hardware 2026-07-24.

`y50p.py selftest` rebuilds both full-session captures **byte-for-byte** from
their decoded rasters, and verifies the CRC of every control frame in all three.

Media is **50 × 30 mm** stock = 400 × 240 dots at 8 dots/mm.
`y50p.py testpattern` prints a full-bleed calibration target (edge-anchored
border, corner blocks, centre crosshair, 1 mm ticks) that fills the label
cleanly — verified on hardware.

## KEY: this is NOT TSPL

Unlike the CTP800 (raw TSPL over SPP), the Y50P speaks a **proprietary framed
binary protocol**. Sending TSPL (`SIZE/CLS/BITMAP/PRINT`) makes it FEED a blank
label but print nothing — the commands parse as noise. Do not reuse the
TSPL generators here; the pipeline is different below the socket.

The two are different in kind, not just in spelling:

|            | TSPL (CTP800)                           | Y50P                                        |
| ---------- | --------------------------------------- | ------------------------------------------- |
| Encoding   | ASCII text, CRLF-terminated lines       | binary frames, magic + length + CRC-32      |
| Integrity  | none                                    | CRC-32 per frame                            |
| Direction  | write-only, fire and forget             | request/reply/event RPC, printer talks back |
| Layout     | printer-side (`TEXT`, `BARCODE`, fonts) | host-side; printer only takes a bitmap      |
| Image data | `BITMAP` with packed rows               | RLE runs, 1 row per `0x18` record           |
| Errors     | invalid commands ignored silently       | typed replies, status poll, async events    |

TSPL is a _page-description language_ — you tell the printer to draw a barcode
and it has the font and symbology built in. The Y50P protocol is a _binary RPC_
with a framebuffer: all rendering happens on the host, and the wire carries
nothing but property sets and a compressed bitmap.

## USB — the protocol is transport-agnostic (confirmed 2026-07-24)

The Y50P is **also a standard USB printer-class device**, and the _identical_
byte stream prints over it. This was missed initially because the USB port was
assumed to be charge-only.

- `5958:0150`, interface class **07** (printer), subclass 01, protocol **02**
  (bidirectional). A CTP800D on the same bench is `5958:0130` — same vendor.
- On Linux the kernel `usblp` driver binds it as `/dev/usb/lpN`. **No driver,
  no CUPS, no pairing.** Just open the node and write the same frames.
- Status reads work too, because the interface is bidirectional — `y50p status`
  over USB returns the same model/firmware/serial/state as over Bluetooth.
- Bench-verified: the calibration label and a text label both printed over USB.

`y50p.py` auto-detects, preferring USB and falling back to Bluetooth.

⚠️ **Identify the node by USB ID, never by probing.** Several thermal printers
share this bus. Writing a probe frame to each `/dev/usb/lp*` to see which
answers will hit the CTP800D, and stray bytes leave
its TSPL parser owed BITMAP data so it silently consumes the _next_ label as
image filler. `find_usb()` matches `5958:0150` from sysfs and writes nothing.

This also makes a browser path realistic — see the packaging note at the end.

## Bluetooth identity

- Classic: `Y50P_8895` @ `XX:XX:XX:XX:XX:XX`, SPP (UUID 1101), RFCOMM ch 1.
- BLE: `Y50P_8895_LE` @ `YY:YY:YY:YY:YY:YY` — dual-mode. GATT has a printer
  service `18f0` with write char `2af1` (write-without-response) + notify `2af0`,
  plus a Microchip transparent-UART service `49535343-...`. FlashLabel used the
  CLASSIC side, not BLE (confirmed in the HCI snoop: 0 GATT writes, 66 SPP frames).
- **BLE printing is CONFIRMED** (bench-verified 2026-07-24). Writing the same
  frames to `2af1` and reading replies on `2af0` gives a fully bidirectional
  link — model, firmware, serial and status all read back — and a complete label
  printed correctly. **No pairing required**; GATT connects unauthenticated.
- Negotiated MTU was only 23, so payloads go out in 20-byte chunks (125 writes
  for a 2.5 KB label). Completes in a couple of seconds, but BLE is the slowest
  of the three links. Chrome may negotiate a larger MTU.
- Note the earlier "the `2af1` char accepts bytes and fed a label" observation
  proved nothing: feeding blank paper is exactly what this printer does with
  bytes it cannot parse. Only a legible label settles it.

## Frame format

Stream = sequence of control frames with a raw raster blob spliced in the middle.

```
1a 01 <len:u16 LE> <payload:len bytes> <crc32:u32 LE> a1
```

Magic start `0x1a`, version `0x01`, magic end `0xa1`.

⚠️ **The version byte is hardcoded to `0x01` here, and a v2 exists.** The prior
art defines both `version1: 0x01` and `version2: 0x02`, so this implementation
handles **YPL v1 only**. Every capture taken from the Y50P is v1, and nothing
here has ever seen or produced a v2 frame — so what differs in v2 is unknown.
Worth checking before assuming this code drives another model in the family.

On the name: **"YPL" is the prior art's term**, not one we can evidence from the
vendor. It appears nowhere in `libdnInkPrinter.so`'s strings and nowhere in any
capture. The SDK's own vocabulary is `dnInkPrinter` / `j0data` / `dn*`. The name
follows the usual convention (ZPL, EPL, TSPL), so it may well come from a vendor
document, but treat it as a useful label rather than an established standard.

### Checksum — SOLVED

Reflected **CRC-32**, poly `0xEDB88320` (the ordinary one), computed over the
**payload only**, with:

- `init   = 0xCA896ADE`
- `xorout = 0xFFFFFFFF`
- stored **little-endian**

```python
crc = zlib.crc32(payload, 0xCA896ADE ^ 0xFFFFFFFF) ^ 0xFFFFFFFF ^ 0xFFFFFFFF
```

Verified against all 18 distinct (payload → checksum) pairs in the captures.

**How it fell out** (`solve_checksum.py`, `solve_init.py`): the checksums are
GF(2)-affine, so `chk(a) ^ chk(b)` depends only on `a ^ b`. Two payloads
differing only in the last byte by `0x01` XOR to `96 30 07 77`, which read
little-endian is `0x77073096` — entry 1 of the standard reflected CRC-32 table.
That pins the polynomial. The remaining per-length residual is the signature of
a non-zero init, recovered by solving `Z(n,I) ^ X = r_n` over GF(2).

The odd init is presumably the register state after some constant prefix the
firmware hashes first; no 1–3 byte prefix produces it, and the exact prefix
doesn't matter in practice.

Earlier brute force missed this because it only tried _standard_ init/xorout
combinations, and `dnCheckLast` — the presumed checksum function — turned out to
be an **8-direction bounds check** (compares indices against struct fields at
offsets 0x00–0x1c, returns 0/1), not a checksum at all.

### Payload structure — SOLVED

Every payload, in **both** directions, is the same TLV record:

```
<group:1> <cmd:1> <dir:1> <len:u16 LE> <data:len>
```

`dir` is what makes the whole thing legible:

| dir    | meaning                            |
| ------ | ---------------------------------- |
| `0x01` | request / invoke                   |
| `0x02` | reply (printer → host)             |
| `0x03` | unsolicited event (printer → host) |
| `0x04` | set a value                        |

A **reply**'s `data` is itself typed:

```
<dtype:1> <vlen:u16 LE> <value:vlen>
```

with `dtype` `0x01` = bytes/ASCII and `0x03` = little-endian integer.

This corrects an earlier misreading: `05410402003200` is **not** a `(2, 50)`
pair. It is cmd `0x41`, dir `0x04` (set), length 2, value **50** — the media
width in millimetres, which is exactly the 400 px row.

| group/cmd | dir   | meaning                                                          |
| --------- | ----- | ---------------------------------------------------------------- |
| `01 04`   | req   | model — replies `"Y50P"`                                         |
| `01 07`   | req   | firmware — replies `"2.1.5"`                                     |
| `01 02`   | req   | serial — replies `"Y50P2511xxxx"`                                |
| `01 b7`   | req   | hardware info — static int `2` (not battery)                     |
| `05 0b`   | req   | **printer status**                                               |
| `05 11`   | set   | **density**                                                      |
| `05 19`   | req   | **start print**                                                  |
| `05 1a`   | req   | **end print**                                                    |
| `05 1b`   | req   | **zlib print** — compressed raster path, never seen in a capture |
| `05 20`   | set   | **paper type** — 0 label, 1 sheet/black-mark, 2 continuous       |
| `05 21`   | req   | **paper locate**                                                 |
| `05 36`   | req   | **first-task paper withdrawal**                                  |
| `05 37`   | req   | **end-task formfeed**                                            |
| `05 38`   | set   | **print width** — 50 mm                                          |
| `05 39`   | set   | **compression rate**                                             |
| `05 40`   | set   | **x reference** — 0                                              |
| `05 41`   | set   | **canvas width** — 50 mm, matching the 400 px row                |
| `05 0f`   | event | unsolicited state notification                                   |

Command names come from **Souukou/OpenBluetoothPrinter** (see _Prior art_),
which names the protocol **YPL** and calls the `dir` byte `io`, with exactly the
four values recovered here independently.

### Status byte (`05 0b`) — MAPPED ON HARDWARE

Measured 2026-07-24 by driving each physical state and reading the poll:

| status | door     | paper   |
| ------ | -------- | ------- |
| `0x00` | closed   | loaded  |
| `0x04` | closed   | **out** |
| `0x06` | **open** | out     |
| `0x06` | **open** | loaded  |

Full bit map — `0x02` and `0x04` measured here, the rest from the prior art
(which agrees exactly on both of ours):

| bit    | meaning       | provoked here? |
| ------ | ------------- | -------------- |
| `0x01` | printing      | no             |
| `0x02` | cover open    | **yes**        |
| `0x04` | out of paper  | **yes**        |
| `0x08` | under voltage | no             |
| `0x10` | overheat      | no             |

**The two bits are not independent, and inferring the map is not safe.** From
`0x06` (open + out) and `0x04` (closed + out) it looks like `0x02` alone should
mean "cover open with paper loaded" — it does not. That state also reads `0x06`.
The paper sensor sits in the head assembly and reads empty whenever the cover is
up, so `0x04` rides along with every cover-open report and **`0x02` never
appears on its own**.

Practical reading: `0x06` = cover open (paper state unknown), `0x04` = genuinely
out of paper, `0x00` = ready. `describe_status()` in `y50p.py` encodes this.

Event payloads (`05 0f`, dir `0x03`) do **not** use the reply's
`<dtype><vlen><value>` encoding — observed data `06 01 01 00` and `00 01` fit no
obvious structure yet. Left raw.

**Two corrections from the prior art:**

- `0x38` vs `0x41` were flagged here as indistinguishable, since both carry 50 in
  every capture. They are **print width** and **canvas width** — related but
  distinct, which is why they agree on this media.
- `0x39` was recorded here as a **job counter**, reasoning that its values
  (`0x13`, `0x08`, `0x09`) looked sequential and that the printer accepted any
  value. **That was wrong**: it is the **compression rate**. The values track
  each capture's raster size against its uncompressed size, which the
  "counter" story never accounted for.

`0x1b` (zlib print) is a **second, compressed raster path** never seen in any
capture here — FlashLabel used the plain RLE route throughout.

Note also that **height is never transmitted**. Nothing in the stream says 240
rows; the printer takes rows until the raster ends. So label length is implied
by how much raster you send, not declared.

⚠️ **`05 19` moves paper** — it retracted the loaded label during probing, and
is the only probe frame that also emitted a `05 0f` event. Treat `0x19`, `0x36`
and the `0x21`/`0x37`/`0x1a` trio as side-effecting; only the `01 xx` info
queries and `05 0b` are safe to send at rest.

**The full trailer matters.** `0521` alone closes the raster but does not feed —
the paper stops the instant the last printed row clears the head, leaving the
label half-presented. `0537` + `051a` complete the feed. This was missed at
first because the horizontal-line snoop was stopped mid-trailer and only
contains `0521`; the FlashLabel capture has the complete sequence.

Printer REPLIES over SPP with framed responses containing ASCII `Y50PC`,
firmware `2.1.5w`, and serial `Y50P2511xxxx...`.

## What class of printer is this?

Not a brand family — an **SDK family**. The hardware is white-labelled across
unrelated consumer brands, but they all wrap the same Android SDK:

- `libdnInkPrinter.so`, JNI class `com.j0data.sdk.dnInkPrinterSDK`
- protocol named **YPL** by the prior art
- USB vendor `0x5958` — **not registered** in `usb.ids`; note it is ASCII `"YX"`

Known members so far:

| model       | brand                  | BLE profile        | evidence                       |
| ----------- | ---------------------- | ------------------ | ------------------------------ |
| **Y50P**    | KNAON                  | `18F0` / 2AF1+2AF0 | this repo, hardware-verified   |
| **U8**      | FlashToy (`knaon.com`) | `FF00` + FF03 flow | prior art, hardware-tested     |
| R4xx family | —                      | —                  | prior art, reference work only |
| R8 family   | —                      | —                  | prior art, partial research    |

⚠️ **The BLE profile is not shared across the family.** Same wire format,
different transport: the Y50P answers on service `18F0` (write `2AF1`, notify
`2AF0`), while the U8 uses `FF00` with a separate `FF03` flow-control
characteristic and a packet credit window. A client hardcoding either one will
not find the other.

Nor is the geometry shared. The Y50P is a 50 mm label printer; the U8 is
wide-format, 203.2 dpi, 50–216 mm — wide enough for US Letter. Anything in a
client that assumes 400 dots is Y50P-specific, not YPL-specific.

⚠️ **The vendor ID is not the discriminator, and neither is the brand.** Direct
counter-evidence from this same bench: the **CTP800D is `5958:0130`** — same
unregistered vendor ID, same USB printer class, sitting on the same hub — and it
speaks **TSPL**, not YPL. One vendor ships both protocols.

The BLE service `18f0` with `2af1`/`2af0` is likewise a generic Chinese BLE
printer service used by many unrelated devices. It does not imply YPL.

**The manufacturer is Xiamen Print Future Technology Co., Ltd.** (`futureprt.com`),
which publishes both driver apps on Google Play: **FlashLabel Pro** (drives the
Y50P) and **FlashToy** (drives the U8). Their FCC grantee code is **`2A6FW`**, so
every device they certify carries an ID of the form `2A6FW-<model>`, which makes
the FCC database a public index of the catalogue. Models certified under it
include `Y50P`, `U8`, `A80`, `C80`, `M50`, `S1`, `Y12P`, `Y41`, `Y813BT` and
`L11`; their own site additionally lists `Y8`, `C80S`, `C80Y`, `Y812` and `D1`,
spanning label makers, 80 mm units, A4 mobile printers and tattoo stencil
printers.

**The driver app is a better filter than the manufacturer.** The vendor lists a
companion app per product, and that is a much tighter signal than the FCC
grantee code: a device sold as driven by **FlashLabel** or **FlashToy** is
running the `com.j0data.sdk` SDK these captures came from. On that basis the
pocket **P80** (`futureprt.com/products_details/151.html`, "FLash Label APP") is
a strong YPL candidate without anyone having opened it.

⚠️ **Sharing a manufacturer does not mean sharing a protocol.** `2A6FW-Y41` is
certified by this same company, yet the KNAON **Y41BT** was independently found
to speak **TSPL** (see `cbiffle/raster-tspl-rs` issue 2, where a USB capture
shows plain `SIZE`/`GAP`/`BITMAP` commands). One ODM, two protocol families.
Treat the catalogue as a list of _candidates_, never as a compatibility list —
the wire format below is the only thing that settles it.

**A reliable test.** Any of these settles it in seconds:

1. Does the vendor's Android app ship `libdnInkPrinter.so` / `com.j0data.sdk`?
2. Does a capture consist of frames starting `1a 01` and ending `a1`?
3. Do those frames' checksums validate as CRC-32 with init `0xCA896ADE`?

`parse_frames.py` answers 2 and 3 directly — it reports `crc=ok` per frame, so
running it over a capture from an unknown printer identifies the protocol
immediately.

The prior art is explicit about not over-generalising here, and it is right:
_"Treat closely named models as different until evidence proves that they share
discovery, commands, raster encoding, lifecycle, and media behavior"_ and
_"Never promote one model's result to an entire family without matching
evidence."_

## Prior art

**[Souukou/OpenBluetoothPrinter](https://github.com/Souukou/OpenBluetoothPrinter)**
(MIT, TypeScript) targets the **FlashToy U8** — a different printer from the
same vendor speaking the same protocol, which they name **YPL**. Found after
this work was complete, by searching GitHub for our CRC constant.

**Independently confirms**, having been derived separately:

- CRC-32 init `0xCA896ADE`, poly `0xEDB88320`, xorout `0xFFFFFFFF` — identical
- `0x1a` start / `0xa1` end framing
- The `dir` byte, which they call `io`: `01` request, `02` response,
  `03` report, `04` setting — the same four values
- Status bits `0x02` cover open and `0x04` out of paper, matching what was
  measured here on hardware
- Per-row RLE with runs capped at 128 (they call it a "Dymo row" encoding)

Two independent derivations agreeing on a non-standard CRC init is about as
strong as protocol evidence gets.

**They have that we did not:** every command name in the table above, three
further status bits, and the `0x1b` zlib-compressed raster path.

**We have that they do not:** the Y50P specifically, raw USB printer-class
support via `/dev/usb/lpN` (they use Web Serial), and byte-for-byte rebuild
verification against captures.

**Neither has battery level.** Their repo mentions it only as a safety
aspiration in `SECURITY.md`, so it remains genuinely unsolved rather than merely
unfound here.

## Raster encoding — SOLVED

Not TIFF, and not raw per-row either (the earlier note here was wrong — rows are
variable length, which is what gives it away). It is a plain 1-bit RLE:

```
raster := row*
row    := 0x18 <run>*        # runs continue until 400 px emitted
run    := <colour:1><count-1:7>
```

- Bit 7 of a run byte is the colour: `0` = white, `1` = black.
- Bits 0–6 are the run length **minus one**, so runs span 1..128 px.
- Rows are exactly **400 px** wide (50 mm at 8 dots/mm, matching the `0x32` in
  the `0x41` setup frame). All three captures are **240 rows** (30 mm).
- `0x18` is a row-start marker, not a run.

Worked example — an all-white row is `18 7f 7f 7f 0f`:
`128 + 128 + 128 + 16 = 400` px, all white. The vertical-line row
`18 7f 45 83 7f 45` is `128w + 70w + 4b + 128w + 70w = 400`, i.e. a 4-dot black
line at x=198, dead centre. ✓

Decoding `captures/y50p-flashlabel-label.bin` renders a legible label reading
**"WOMENS"** over a black band with a reversed-out **"8"** — full confirmation.

## Packaging note: Chromebook / browser

Since the printer is USB printer-class and the protocol is transport-agnostic,
a browser path is realistic — and **both WebUSB and Web Bluetooth work**, since
all three transports are now verified on hardware:

- Web Bluetooth is **BLE-only** — Chrome can never open a classic RFCOMM/SPP
  socket from a page, so the SPP path is unavailable to a web app. But the BLE
  path is confirmed to print, so this is a real option: `requestDevice` filtered
  on service `18f0`, then chunked `writeValueWithoutResponse` to `2af1`, with
  `2af0` notifications for status. Expect ~20-byte chunks unless Chrome
  negotiates a larger MTU.
- WebUSB is the faster route where a cable is acceptable.

**Chrome on Linux needs a flag for Web Bluetooth** (measured on Chrome 150,
BlueZ 5.87, 2026-07-24). In a secure context (localhost):

| Chrome launch                                 | `navigator.bluetooth` | `getAvailability()` |
| --------------------------------------------- | --------------------- | ------------------- |
| default                                       | **absent**            | —                   |
| `--enable-experimental-web-platform-features` | present               | **true**            |

So developing the Web Bluetooth client on this Linux box works, but a flag is
required. Two options:

- **`chrome://flags/#enable-web-bluetooth`** — the dedicated Linux flag
  ("Enables the Web Bluetooth API on platforms without official support").
  Preferred for a browser you actually use. Needs a relaunch.
- `--enable-experimental-web-platform-features` — broader, turns on every
  experimental feature; fine for a throwaway profile launched from a script.

On **ChromeOS it is on by default** — no flag — which is the actual deployment
target, so this only affects local development. Headless Chrome always reports
`false` regardless of flags and is not a valid way to test this.

- WebUSB can claim printer-class interfaces (this is how ESC/POS receipt-printer
  web demos work). Printer class 07 is not on the WebUSB protected-class
  blocklist.
- Caveat on Linux: `usblp` binds the interface first, so a WebUSB/libusb client
  must detach the kernel driver. ChromeOS handles this itself.
- The vendor's own **FlashLabel Chrome extension** is USB-only ("connect to a
  thermal printer via usb to print") and hooks Ctrl+P, which is the signature of
  a `chrome.printerProvider` extension — it registers as a print destination and
  forwards the job over `chrome.usb`. That is the established pattern for
  ChromeOS, and it is evidence the vendor did not consider BLE printing viable
  either.
- Whole pipeline is browser-native: canvas → threshold `ImageData` to 1-bit →
  RLE → CRC-32 frames → chunked writes. No server, no driver, no install.

## Web Bluetooth demo (`web/`)

A zero-install browser client: renders a label on canvas, encodes it, and prints
over BLE. No server, no driver, no extension.

**Verified working on Chrome/Linux 2026-07-25** — connected, read model,
firmware, serial and status, and printed three labels back to back at
**1.3 s each** (2927 bytes in 147 chunked writes). See the discovery caveat
below.

```bash
cd web && python3 -m http.server 8788
# then open http://localhost:8788/  (localhost is a secure context)
```

On Linux, Chrome needs **`chrome://flags/#enable-web-bluetooth`** ("Enables the
Web Bluetooth API on platforms without official support – Linux"), then a
**relaunch**. ChromeOS has Web Bluetooth on by default.

`--enable-experimental-web-platform-features` also works and is handy for a
throwaway profile, but it switches on every experimental web feature; the
dedicated flag above is the right one for a browser you actually use.

`file://` will not work — Web Bluetooth requires a secure context, and ES
modules require http(s).

- `web/protocol.mjs` — the protocol, DOM-free so both the page and Node can use it.
- `web/index.html` — UI, canvas rendering, and the BLE transport.
- `web/selftest.mjs` — `node web/selftest.mjs`. Rebuilds both full captures
  **byte-for-byte** from their decoded rasters, the same guarantee
  `y50p.py selftest` gives. The page runs the unit-vector half on load and
  reports it in the log, so a bad port is visible before anything reaches paper.

Writes go out in 20-byte chunks (safe for the 23-byte default ATT MTU) with 8 ms
pacing, mirroring the Python driver — `writeValueWithoutResponse` has no
backpressure, so unpaced writes can outrun the printer's buffer.

### ⚠️ Chrome + BlueZ 5.87: FILTERED discovery crashes bluetoothd

**Root cause isolated 2026-07-25.** It is not Web Bluetooth on Linux that is
broken — it is specifically Chrome's BlueZ **`SetDiscoveryFilter` path with a
service UUID**. Scanning with `acceptAllDevices` works perfectly.

Verified by A/B on one machine, cross-referencing the page log against
`journalctl`:

```
02:03:23  scanning (filtered on 0x18f0)…
02:03:24  bluetoothd SEGFAULT
02:03:25  "User cancelled the requestDevice() chooser"  <- not cancelled; daemon died

02:06:31  scanning (all devices)…
02:06:50  connected                                     <- no crash
02:06:55  three labels printed, 1.3 s each
```

`getDevices()` was unavailable on **both** attempts (the permissions-backend
flag was off), so the only variable was the discovery mode.

**Workaround: filter by name, not by service.** All three modes measured on the
same machine:

| discovery mode                      | result                                                |
| ----------------------------------- | ----------------------------------------------------- |
| `filters: [{ services: [0x18f0] }]` | **11 segfaults**, every crash of the day              |
| `acceptAllDevices`                  | clean — but a chooser full of every nearby BLE device |
| `filters: [{ namePrefix: 'Y50P' }]` | **clean, 49 min uptime** — and a tidy chooser         |

```js
// crashes bluetoothd 5.87 under Chrome on Linux
navigator.bluetooth.requestDevice({ filters: [{ services: [0x18f0] }] });

// works, and still shows only the printer
navigator.bluetooth.requestDevice({
	filters: [{ namePrefix: 'Y50P' }],
	optionalServices: [0x18f0]
});
```

Name filtering is strictly better than `acceptAllDevices`: same safety, better
chooser. It works because only a **service** filter makes Chrome push a
`SetDiscoveryFilter` UUID list down to BlueZ; `namePrefix` is matched inside
Chrome, so the faulting path is never entered.

`web/index.html` defaults to the name filter and offers all three modes.

Note that Chrome 150.0.7871.186 does **not** fix this — it was still crashing
after the update, and BlueZ 5.87-2 is current with no update pending.

The symptom, before the cause was known: `bluetoothd` segfaults and systemd
restarts it, so in `bluetoothctl` it looks like the adapter vanishing and
coming back:

```
Agent unregistered
[DEL] Controller AA:BB:CC:DD:EE:FF host [default]
Agent registered
[NEW] Controller AA:BB:CC:DD:EE:FF BlueZ 5.87 [default]
```

followed by a flood of UUID/class-of-device churn as BlueZ rebuilds state.
`journalctl -u bluetooth` shows the truth:
`bluetooth.service: Main process exited, code=dumped, status=11/SEGV`.

**This is not a bug in this project's code.** A web page cannot segfault a root
daemon; the fault is in Chrome's BlueZ D-Bus client against BlueZ 5.87. Note the
flag's own wording: _"Enables the Web Bluetooth API on platforms **without
official support** – Linux."_

The trigger is **discovery**, not the flag. Six crashes landed within minutes of
the first `requestDevice()`, and none in the preceding week — earlier runs that
merely feature-detected `navigator.bluetooth` with the flag on were clean.

No lasting harm: systemd restarts bluetoothd and the controller returns.

A **misleading secondary symptom**: Chrome then logs

```
Failed to start discovery: org.bluez.Error.InProgress: Operation already in progress
```

which reads like a permissions or contention problem and sends you hunting for a
missing group. It is neither. Chrome caches adapter state that does not survive
the daemon dying underneath it, so after the restart it still believes it holds a
discovery session and the retry collides. The giveaway is the timestamp — it
matches `systemctl show bluetooth -p ActiveEnterTimestamp` to the second.

For the record, **no group is required**: BlueZ ships
`<policy context="default"><allow send_destination="org.bluez"/></policy>`, so
any user may drive it. A real permissions failure returns `NotAuthorized`.

Optionally also enable
`chrome://flags/#enable-web-bluetooth-new-permissions-backend`, which exposes
`navigator.bluetooth.getDevices()` so an already-permitted device reconnects
with no scan at all. Not required — unfiltered scanning is enough — but it
removes discovery from the picture entirely. `web/index.html` uses it when
available.

Pairing is **not** needed. The printer connects and prints unpaired
(`Paired: no` throughout the verified run).

If Chrome ever proves unworkable, `probes/ble-print.py` (bleak) and USB
(`y50p.py`) are both unaffected — the Python/BlueZ path never crashed once
across dozens of connects.

## Files

The TypeScript library:

- `src/protocol.ts` — the wire format: framing, CRC, RLE raster, stream
  building, reply parsing. Pure, no dependencies.
- `src/raster.ts` — `lumaOverWhite` and pixels-to-rows. Pure.
- `src/transport.ts` — the `Link` interface and job orchestration.
- `src/web-bluetooth.ts` — the BLE transport: chunking, pacing, discovery.
- `test/captures.test.ts` — rebuilds the captures below byte-for-byte.

The Python reference, under `reference/`:

- `y50p.py` — the driver: framing, CRC, rendering, USB + SPP transports.
  `selftest`, `status`, `text`, `image`, `testpattern`, `print`, `monitor`.
- `raster.py` — raster codec (`decode`/`encode`), PBM dump for previews.
- `parse_frames.py` — splits a capture into control frames + raster blobs.
  This is the instrument that identifies an unknown printer as YPL.
- `solve_checksum.py` / `solve_init.py` — how the CRC was recovered; kept as
  the audit trail.
- `replay.py` — the original replay-only driver, superseded, kept for history.
- `probes/` — the single-purpose BLE/SPP/USB probes used during the work.
- `sdk-exports.txt` — `nm` output from the vendor's `libdnInkPrinter.so`. The
  binary itself is NOT redistributed here; only this symbol list is.

And `captures/` — three TX streams: a real FlashLabel label, a horizontal
line, and a vertical line. These are host-to-printer only, so they contain no
replies and therefore no serial or model strings.

## Battery level — NOT FOUND (investigated 2026-07-24)

Wanted, not located. What was ruled out, so this isn't re-tried blind:

- **No standard BLE Battery Service.** Full GATT enumeration shows no `0x180F`
  / `0x2A19`. Every service is a generic module passthrough: Microchip
  transparent UART (`49535343-…`), a Tencent airsync service (`fee7`), and
  vendor `ff00`/`ff10`/`ff80`/`fff0` pairs.
- **`01 b7` is not the battery.** It was the prime suspect — the only one of the
  four connect-time queries not otherwise explained, and the only one returning
  an integer (2) rather than a string. It reads a constant 2 both on battery and
  on USB, so it is a static hardware/protocol revision.
- **BLE `ff03` is not the battery.** It pushes `01 07` and `02 f4 00` (u16 244)
  on subscribe only — no periodic feed, and both unchanged across a
  plug/unplug. Looks like BLE module info.
- **`fec8`** carries the Tencent airsync blob, ending in the BLE MAC
  `d0 23 81 07 88 95`. Not printer telemetry.

The remaining honest options are a **fresh HCI snoop** of FlashLabel Pro while
it displays the battery indicator (zero risk, definitive — how everything else
here was solved), or a sweep of unused `01 xx` opcodes. The sweep was **not**
run: all four known group-01 commands are pure reads, but an unknown opcode
could in principle trigger a reset or reboot, and that is not worth it
uninvited on someone's hardware.

`probes/ble-read.py` and `probes/ble-listen.py` are the passive BLE tools used
here — neither writes anything. `probes/ble-print.py` is the working BLE
client (status query, then print) and doubles as the reference for a Web
Bluetooth port.

BLE is deliberately **not** wired into `y50p.py` as a transport: bleak is async
while the driver is synchronous, and wrapping it per-call would reconnect on
every operation. USB and SPP cover every practical need, and `ble-print.py`
already demonstrates the path for anyone porting it to the browser.

## Open questions (not blocking)

1. **Label geometry is hardcoded** to 400×240. The `0x41`/`0x38` frames clearly
   carry millimetre dimensions, so other media sizes should just work by
   changing those params and the row width — untested, needs different stock.
2. The exact constant prefix behind `init = 0xCA896ADE`.
3. Meanings of the individual setup commands (`0x20`, `0x11`, `0x19`, `0x36`).
4. `05 0f` event payload structure (`06 01 01 00`, `00 01`).
5. Whether any status bits above `0x04` exist — head over-temperature, low
   battery and jam states have not been provoked.
6. Battery level (see above).
7. A browser client. Both WebUSB and Web Bluetooth are viable (all three
   transports verified); nothing is blocking this but the work.
