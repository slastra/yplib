#!/usr/bin/env python3
"""Y50P label printer driver -- generates arbitrary labels.

Protocol (fully recovered, see FINDINGS.md):

  frame  := 1a 01 <len:u16 LE> <payload:len> <crc32:u32 LE> a1
  crc32  := reflected CRC-32, poly 0xEDB88320, init 0xCA896ADE,
            xorout 0xFFFFFFFF, computed over the payload only
  raster := (0x18 <run>*)*   with run = <colour:1><count-1:7>, rows of 400 px

The job is a fixed frame preamble, the raw raster blob spliced in, then a
terminator frame.

The protocol is transport-agnostic: identical bytes work over classic Bluetooth
SPP (RFCOMM channel 1) and over USB. The Y50P enumerates as a standard USB
printer-class device (class 07, subclass 01, protocol 02 = bidirectional), so on
Linux it appears as /dev/usb/lpN with no driver, and status reads work there
too. USB is preferred when present: it is faster and needs no pairing.

Usage:
    python3 y50p.py selftest                 # verify against captures/
    python3 y50p.py status                   # device info + ready/paper/cover
    python3 y50p.py text "HELLO" [out.bin]   # render text, write stream
    python3 y50p.py image pic.png [out.bin]
    python3 y50p.py print <stream.bin>       # send to the printer
"""
import glob
import os
import select
import socket
import sys
import time
import zlib

from raster import WIDTH, decode, encode

HEIGHT = 240          # rows per label (30 mm at 8 dots/mm)
CRC_INIT = 0xCA896ADE
ADDR = 'XX:XX:XX:XX:XX:XX'
RFCOMM_CHANNEL = 1
USB_GLOB = '/dev/usb/lp*'
USB_VID, USB_PID = '5958', '0150'   # Y50P; the CTP800D is 5958:0130


# --- framing ---------------------------------------------------------------

def crc(payload):
    return zlib.crc32(payload, CRC_INIT ^ 0xFFFFFFFF) ^ 0xFFFFFFFF ^ 0xFFFFFFFF


def frame(payload_hex):
    p = bytes.fromhex(payload_hex)
    return (b'\x1a\x01' + len(p).to_bytes(2, 'little') + p
            + crc(p).to_bytes(4, 'little') + b'\xa1')


# Frame sequence lifted from captures/y50p-horizontal-line.bin. The 01xx frames
# are the post-connect handshake; the 05xx block is per-job setup. 0x41/0x38
# carry (2, 50) -- 50 mm media width, i.e. the 400 px row. 0x39 is a job
# counter that increments per label; the printer accepts any value.
PREAMBLE = [
    '0104010000', '0104010000', '01b7010000', '0107010000', '0102010000',
    '050b010000', '050b010000', '050b010000', '050b010000', '050b010000',
    '050b010000',
    '052001010000', '051101010008', '0519010000', '0536010000', '050b010000',
    '05410402003200',   # media width  = 50 mm
    '05380402003200',   # print length = 50 mm
    '05400402000000',   # offset = 0
]
JOB_FRAME = '053904010%03x'   # 0x39 is the COMPRESSION RATE, not a job
                              # counter — see FINDINGS.md, 'Status byte'

# End-of-job. 0x21 closes the raster; 0x37 and 0x1a are what actually feed the
# label out to the tear bar -- omitting them stops the paper the instant the
# last printed row clears the head. The trailing keep-alives are what the app
# sends while it waits for the feed to finish.
TRAILER = ['0521010000', '0537010000', '051a010000'] + ['050b010000'] * 10


def build_stream(rows, job=0x08, trailer=None):
    parts = [frame(p) for p in PREAMBLE]
    parts.append(frame(JOB_FRAME % job))
    parts.append(encode(rows))
    parts += [frame(p) for p in (TRAILER if trailer is None else trailer)]
    return b''.join(parts)


# --- payload structure -----------------------------------------------------
#
# Every payload, in both directions, is:
#
#     <group:1> <cmd:1> <dir:1> <len:u16 LE> <data:len>
#
# and a reply's data is itself typed:
#
#     <dtype:1> <vlen:u16 LE> <value:vlen>
#
# This is what the `05410402003200` setup frames really are: cmd 0x41, dir 0x04
# (set), a 2-byte value of 50 -- the media width in mm. Earlier notes read those
# bytes as a "(2, 50)" pair, which was wrong.

DIR_REQUEST, DIR_REPLY, DIR_EVENT, DIR_SET = 0x01, 0x02, 0x03, 0x04
DIR_NAMES = {DIR_REQUEST: 'req', DIR_REPLY: 'reply',
             DIR_EVENT: 'event', DIR_SET: 'set'}
DTYPE_BYTES, DTYPE_INT = 0x01, 0x03

# Read-only info queries, safe to send at any time. 0x19/0x36 and the
# 0x21/0x37/0x1a trio are deliberately excluded -- they move paper.
QUERIES = {
    'model':    (0x01, 0x04),
    'firmware': (0x01, 0x07),
    'serial':   (0x01, 0x02),
    'hwinfo':   (0x01, 0xB7),
    'status':   (0x05, 0x0B),
}


# Status byte from the 05 0b poll.
#
# 0x02 and 0x04 were mapped here by driving each physical state:
#
#   door closed, paper loaded -> 0x00
#   door closed, paper out    -> 0x04
#   door OPEN,   paper out    -> 0x06
#   door OPEN,   paper loaded -> 0x06   <-- not 0x02
#
# so the bits are not independent: the paper sensor sits in the head assembly
# and reads empty whenever the cover is up, meaning 0x04 rides along with every
# cover-open report and 0x02 never appears alone.
#
# The remaining three bits are from Souukou/OpenBluetoothPrinter, independent
# work on the sibling FlashToy U8 (see FINDINGS.md). They agree exactly on 0x02
# and 0x04. Not provoked on hardware here.
STATUS_PRINTING = 0x01
STATUS_COVER_OPEN = 0x02
STATUS_NO_PAPER = 0x04
STATUS_UNDER_VOLTAGE = 0x08
STATUS_OVERHEAT = 0x10

STATUS_FLAGS = (
    (STATUS_PRINTING, 'printing'),
    (STATUS_COVER_OPEN, 'cover open'),
    (STATUS_NO_PAPER, 'out of paper'),
    (STATUS_UNDER_VOLTAGE, 'under voltage'),
    (STATUS_OVERHEAT, 'overheat'),
)


def describe_status(value):
    """Render a status byte as human-readable state."""
    if value == 0:
        return 'ready'
    flags = [name for bit, name in STATUS_FLAGS if value & bit]
    # Cover-open forces the paper bit, so reporting both is misleading.
    if value & STATUS_COVER_OPEN and 'out of paper' in flags:
        flags.remove('out of paper')
    rest = value & ~sum(bit for bit, _ in STATUS_FLAGS)
    if rest:
        flags.append(f'unknown bits 0x{rest:02x}')
    return ', '.join(flags) or f'unknown (0x{value:02x})'


def is_ready(value):
    return value == 0


def request(group, cmd, data=b'', direction=DIR_REQUEST):
    p = bytes([group, cmd, direction]) + len(data).to_bytes(2, 'little') + data
    return (b'\x1a\x01' + len(p).to_bytes(2, 'little') + p
            + crc(p).to_bytes(4, 'little') + b'\xa1')


def decode_payload(payload):
    """Split a payload into (group, cmd, dir, value). value is bytes|int|None."""
    if len(payload) < 5:
        return None
    group, cmd, direction = payload[0], payload[1], payload[2]
    ln = int.from_bytes(payload[3:5], 'little')
    data = payload[5:5 + ln]
    value = data
    # Only replies carry the <dtype><vlen><value> encoding. Event payloads
    # (e.g. 05 0f -> 06 01 01 00) do not parse that way and are left raw.
    if direction == DIR_REPLY and len(data) >= 3:
        dtype = data[0]
        vlen = int.from_bytes(data[1:3], 'little')
        raw = data[3:3 + vlen]
        if dtype == DTYPE_INT:
            value = int.from_bytes(raw, 'little')
        elif dtype == DTYPE_BYTES:
            value = raw
    return group, cmd, direction, value


def read_replies(raw):
    """Parse a reply buffer into decoded payloads, skipping bad frames."""
    from parse_frames import parse
    out = []
    for kind, _, *rest in parse(raw):
        if kind != 'frame':
            continue
        payload, chk = rest
        if crc(payload) != int.from_bytes(chk, 'little'):
            continue
        dec = decode_payload(payload)
        if dec:
            out.append(dec)
    return out


# --- transports ------------------------------------------------------------
#
# The same bytes go over either link, so everything above this point is
# transport-agnostic and everything below only has to move bytes.

class UsbTransport:
    """A USB printer-class device node, e.g. /dev/usb/lp1."""

    def __init__(self, device):
        self.name = device
        self.fd = os.open(device, os.O_RDWR | os.O_NONBLOCK)

    def write(self, data):
        return os.write(self.fd, data)

    def read(self, timeout):
        buf = b''
        while select.select([self.fd], [], [], timeout)[0]:
            try:
                chunk = os.read(self.fd, 4096)
            except BlockingIOError:
                break
            if not chunk:
                break
            buf += chunk
            if buf.endswith(b'\xa1'):
                break
        return buf

    def close(self):
        os.close(self.fd)


class BluetoothTransport:
    """Classic Bluetooth SPP on RFCOMM channel 1."""

    def __init__(self, addr=ADDR):
        self.name = addr
        self.sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
                                  socket.BTPROTO_RFCOMM)
        self.sock.settimeout(15)
        self.sock.connect((addr, RFCOMM_CHANNEL))

    def write(self, data):
        self.sock.sendall(data)
        return len(data)

    def read(self, timeout):
        self.sock.settimeout(timeout)
        buf = b''
        while True:
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
        return buf

    def close(self):
        self.sock.close()


def usb_ids(node):
    """(idVendor, idProduct) for a /dev/usb/lpN node, or None.

    Walks sysfs: /sys/class/usbmisc/lpN -> .../<device>/<interface>/usbmisc/lpN
    so two levels up from the link target is the interface and one more is the
    USB device holding the IDs.
    """
    link = f'/sys/class/usbmisc/{os.path.basename(node)}'
    if not os.path.exists(link):
        return None
    dev = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.realpath(link))))
    try:
        with open(f'{dev}/idVendor') as f:
            vid = f.read().strip()
        with open(f'{dev}/idProduct') as f:
            pid = f.read().strip()
    except OSError:
        return None
    return vid, pid


def find_usb(devices=None):
    """Return the /dev/usb/lpN that is a Y50P, by USB ID.

    Identified from sysfs rather than by probing. Other thermal printers share
    this bus -- the CTP800D on the next node runs TSPL, and stray bytes desync
    its parser so that it consumes the following label as image data. Never
    write to a device to find out what it is.
    """
    for dev in sorted(devices or glob.glob(USB_GLOB)):
        if usb_ids(dev) == (USB_VID, USB_PID):
            return dev
    return None


def open_transport(target=None):
    """Open `target`, or auto-detect: USB if a Y50P is on the bus, else BT."""
    if target is None:
        target = find_usb() or ADDR
    return (UsbTransport(target) if target.startswith('/')
            else BluetoothTransport(target))


def query(transport, group, cmd, wait=0.8):
    transport.write(request(group, cmd))
    time.sleep(0.05)
    return read_replies(transport.read(wait))


def get_status(target=None):
    """Open the printer, run the safe read-only queries, return a dict."""
    t = open_transport(target)
    info, events = {'link': t.name}, []
    try:
        for name, (group, cmd) in QUERIES.items():
            for g, c, d, value in query(t, group, cmd):
                if d == DIR_EVENT:
                    events.append((f'{g:02x}{c:02x}', value))
                elif (g, c) == (group, cmd):
                    if name == 'status':
                        raw = value[0] if isinstance(value, bytes) and value \
                            else value
                        info['status'] = raw
                        info['state'] = describe_status(raw)
                        continue
                    if isinstance(value, bytes):
                        value = (value.decode('ascii')
                                 if value and all(32 <= b < 127 for b in value)
                                 else '0x' + value.hex())
                    info[name] = value
    finally:
        t.close()
    info['events'] = events
    return info


# --- rendering -------------------------------------------------------------

def img_to_rows(img):
    """PIL image -> list of 0/1 rows, fitted to WIDTH x HEIGHT."""
    from PIL import Image
    img = img.convert('L')
    img.thumbnail((WIDTH, HEIGHT), Image.LANCZOS)
    canvas = Image.new('L', (WIDTH, HEIGHT), 255)
    canvas.paste(img, ((WIDTH - img.width) // 2, (HEIGHT - img.height) // 2))
    px = canvas.load()
    return [[1 if px[x, y] < 128 else 0 for x in range(WIDTH)]
            for y in range(HEIGHT)]


def testpattern_rows():
    """Full-bleed calibration target for 50 x 30 mm stock.

    Everything is drawn relative to the extreme edges of the 400x240 bitmap, so
    whatever is missing from the printed label tells you directly which edge is
    being clipped and by how much.
    """
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new('L', (WIDTH, HEIGHT), 255)
    d = ImageDraw.Draw(img)
    W, H = WIDTH - 1, HEIGHT - 1

    d.rectangle([0, 0, W, H], outline=0, width=2)          # extreme border
    d.line([0, 0, W, H], fill=0, width=1)                  # diagonals
    d.line([0, H, W, 0], fill=0, width=1)
    for x, y in ((0, 0), (W, 0), (0, H), (W, H)):          # solid corner blocks
        d.rectangle([min(x, x - 24), min(y, y - 24),
                     max(x, x + 24), max(y, y + 24)], fill=0)
    d.rectangle([WIDTH // 2 - 40, HEIGHT // 2 - 22,        # centre knockout
                 WIDTH // 2 + 40, HEIGHT // 2 + 22], fill=255, outline=0)
    d.line([WIDTH // 2, 0, WIDTH // 2, HEIGHT // 2 - 22], fill=0, width=2)
    d.line([WIDTH // 2, HEIGHT // 2 + 22, WIDTH // 2, H], fill=0, width=2)
    d.line([0, HEIGHT // 2, WIDTH // 2 - 40, HEIGHT // 2], fill=0, width=2)
    d.line([WIDTH // 2 + 40, HEIGHT // 2, W, HEIGHT // 2], fill=0, width=2)

    for mm in range(1, 50):                                # 8 px = 1 mm ticks
        x = mm * 8
        long = mm % 10 == 0
        d.line([x, 2, x, 2 + (18 if long else 8)], fill=0, width=2 if long else 1)
        d.line([x, H - 2, x, H - 2 - (18 if long else 8)],
               fill=0, width=2 if long else 1)
    for mm in range(1, 30):
        y = mm * 8
        long = mm % 10 == 0
        d.line([2, y, 2 + (18 if long else 8), y], fill=0, width=2 if long else 1)
        d.line([W - 2, y, W - 2 - (18 if long else 8), y],
               fill=0, width=2 if long else 1)

    font = None
    for path in ('/usr/share/fonts/TTF/DejaVuSans-Bold.ttf',
                 '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'):
        try:
            font = ImageFont.truetype(path, 19)
            break
        except OSError:
            continue
    if font is not None:
        d.text((WIDTH // 2, HEIGHT // 2), '50x30', font=font, fill=0,
               anchor='mm')
        d.text((WIDTH // 2, 34), 'TOP', font=font, fill=0, anchor='mm')
        d.text((WIDTH // 2, H - 34), 'BOTTOM', font=font, fill=0, anchor='mm')

    px = img.load()
    return [[1 if px[x, y] < 128 else 0 for x in range(WIDTH)]
            for y in range(HEIGHT)]


def text_to_rows(text, size=90):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new('L', (WIDTH, HEIGHT), 255)
    d = ImageDraw.Draw(img)
    font = None
    for path in ('/usr/share/fonts/TTF/DejaVuSans-Bold.ttf',
                 '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'):
        try:
            font = ImageFont.truetype(path, size)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    # shrink until it fits
    while size > 8:
        box = d.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= WIDTH - 20 and box[3] - box[1] <= HEIGHT - 20:
            break
        size -= 4
        font = font.font_variant(size=size)
    box = d.textbbox((0, 0), text, font=font)
    d.text(((WIDTH - (box[2] - box[0])) / 2 - box[0],
            (HEIGHT - (box[3] - box[1])) / 2 - box[1]), text, font=font, fill=0)
    px = img.load()
    return [[1 if px[x, y] < 128 else 0 for x in range(WIDTH)]
            for y in range(HEIGHT)]


# --- sending ---------------------------------------------------------------

def send(stream, target=None):
    """Send a built stream. Auto-detects USB, falling back to Bluetooth.

    Writes are paced on frame boundaries the way the app does; the raster blob
    is unframed so it goes out as one chunk.
    """
    t = open_transport(target)
    try:
        i = 0
        while i < len(stream):
            nxt = stream.find(b'\x1a', i + 1)
            chunk = stream[i:nxt] if nxt != -1 else stream[i:]
            t.write(chunk)
            i = nxt if nxt != -1 else len(stream)
            time.sleep(0.01)
        time.sleep(2)
    finally:
        t.close()
    return len(stream)


def monitor(addr=None, period=0.7):
    """Poll status and print every change, to map codes against real states.

    Open the cover, pull the paper out, etc. while this runs -- each transition
    is logged with the raw bytes so the codes can be named afterwards.
    """
    t = open_transport(addr)
    print(f'connected via {t.name} -- change the printer state; Ctrl-C to stop')
    last = None
    t0 = time.time()
    try:
        while True:
            seen = query(t, *QUERIES['status'], wait=period)
            state = tuple((f'{g:02x}{c:02x}', DIR_NAMES.get(d, d),
                           value.hex() if isinstance(value, bytes) else value)
                          for g, c, d, value in seen)
            if state != last:
                print(f'[{time.time() - t0:7.1f}s] ' +
                      ('  '.join(f'{k}/{d}={v}' for k, d, v in state)
                       or '(no reply)'))
                last = state
    except KeyboardInterrupt:
        print('\nstopped')
    finally:
        t.close()


# --- selftest --------------------------------------------------------------

def selftest():
    from parse_frames import parse
    ok = True
    # The horizontal-line snoop was stopped mid-trailer, so it only contains the
    # first trailer frame; the FlashLabel capture holds the complete job.
    cases = (
        ('captures/y50p-flashlabel-label.bin', 0x13, None),
        ('captures/y50p-horizontal-line.bin', 0x08, ['0521010000']),
        ('captures/y50p-vertical-line.bin', 0x09, None),
    )
    for path, job, trailer in cases:
        data = open(path, 'rb').read()
        # every control frame's CRC must verify
        bad = [off for k, off, *r in parse(data)
               if k == 'frame' and crc(r[0]) != int.from_bytes(r[1], 'little')]
        blob = max((r[0] for k, _, *r in parse(data) if k == 'gap'), key=len)
        rt = encode(decode(blob)) == blob
        print(f'{path}: crc bad frames={len(bad)} raster round-trip={rt}')
        ok &= not bad and rt
        # The vertical-line capture starts mid-session (no handshake), so only
        # the two full-session captures can be rebuilt from scratch.
        if 'vertical' not in path:
            match = build_stream(decode(blob), job=job, trailer=trailer) == data
            print(f'  full stream rebuild byte-identical: {match}')
            ok &= match
    print('SELFTEST', 'PASS' if ok else 'FAIL')
    return ok


def run_link_command(cmd):
    """The subcommands that need to reach the printer."""
    if cmd == 'status':
        for k, v in get_status().items():
            print(f'{k:10} {f"0x{v:02x}" if k == "status" else v}')
    elif cmd == 'monitor':
        monitor()
    elif cmd == 'print':
        print(f'sent {send(open(sys.argv[2], "rb").read())} bytes')


def explain_link_error(err):
    """Turn a bare socket errno into something actionable."""
    usb = 'present' if find_usb() else 'not connected'
    return (f'cannot reach the printer: {err}\n'
            f'  USB: {usb}\n'
            f'  If a browser or another app holds the BLE link, the classic SPP\n'
            f'  radio will refuse connections. Disconnect it, or plug in USB.')


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'selftest'
    if cmd in ('status', 'monitor', 'print'):
        try:
            return run_link_command(cmd)
        except OSError as e:
            sys.exit(explain_link_error(e))
    if cmd == 'selftest':
        sys.exit(0 if selftest() else 1)
    elif cmd in ('status', 'monitor', 'print'):
        run_link_command(cmd)
    else:
        if cmd == 'text':
            rows = text_to_rows(sys.argv[2])
        elif cmd == 'image':
            from PIL import Image
            rows = img_to_rows(Image.open(sys.argv[2]))
        elif cmd == 'testpattern':
            rows = testpattern_rows()
        else:
            sys.exit(f'unknown command {cmd}')
        out = sys.argv[2 if cmd == 'testpattern' else 3] \
            if len(sys.argv) > (2 if cmd == 'testpattern' else 3) else 'label.bin'
        stream = build_stream(rows)
        open(out, 'wb').write(stream)
        print(f'wrote {out} ({len(stream)} bytes)')


if __name__ == '__main__':
    main()
