# Acknowledgements

## Souukou / OpenBluetoothPrinter

The protocol in this repository was derived independently, from HCI snoops of
the manufacturer's Android app, and verified by rebuilding whole print sessions
byte for byte. [Souukou/OpenBluetoothPrinter][obp] was found **afterwards**, by
searching GitHub for the CRC constant. It targets a sibling printer, the
FlashToy U8, and it named this wire format **YPL** publicly first, which is why
this library carries that name rather than inventing a second one.

Working separately, they arrived at the same framing, the same status bits, and
the same non-standard CRC init. Two derivations agreeing on a value like that is
about as strong as protocol evidence gets.

Material taken from their work and used here, with thanks:

- **Every command name** in the command table in `FINDINGS.md`. The captures
  here proved what the commands _do_; the names are theirs.
- **Status bits `0x01` (printing), `0x08` (under voltage) and `0x10`
  (overheat)**, in `STATUS_FLAGS`. Only `0x02` (cover open) and `0x04` (out of
  paper) were provoked and measured on hardware here.
- The name **YPL**, and the reading of the `dir` byte, which they call `io`.

Their project is MIT licensed. Its notice follows in full, as that licence
requires.

[obp]: https://github.com/Souukou/OpenBluetoothPrinter

```
MIT License

Copyright (c) 2026 Yuhang

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Vendor SDK

`reference/sdk-exports.txt` is `nm`-style symbol output from the vendor's
`libdnInkPrinter.so`, taken during interoperability analysis. **That binary is
not redistributed here** and is excluded by `.gitignore`. Symbol names describe
an interface rather than expressing anything creative, and are reproduced only
to document how the SDK family was identified.
