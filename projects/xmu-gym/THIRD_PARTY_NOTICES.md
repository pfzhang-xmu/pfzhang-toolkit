# Third-party notices

XMU Gym is an independent project. The product name, page, and documentation
are original to this repository. The booking core under `core/booking/` is
derived from an MIT-licensed open-source project; its copyright and permission
notice is retained here and in [`licenses/MIT-xmu-sports.txt`](licenses/MIT-xmu-sports.txt)
as required by the MIT License. Nothing in this file changes the license of
this project's own code, which is MIT (see [`LICENSE`](LICENSE)).

## Booking core

- Portions of `core/booking/` are derived from `xmu-sports`
  (Copyright (c) 2026 MidiAug), licensed under the MIT License. The full MIT
  text is in `licenses/MIT-xmu-sports.txt`. The original project's own
  third-party notices are kept in `licenses/THIRD_PARTY_NOTICES-xmu-sports.md`
  for reference.

## Captcha model

- `core/booking/captcha_assets/common_old.onnx` and `ascii.json` come from
  [ddddocr](https://github.com/sml2h3/ddddocr) 1.6.1, MIT. The upstream
  license and model SHA256 are retained in
  `core/booking/captcha_assets/LICENSE` and `NOTICE.md` where present.

## WeChat local storage reader

- [ccl_chromium_reader](https://github.com/cclgroupltd/ccl_chromium_reader),
  MIT, pinned in `requirements.txt`.
- `ccl_simplesnappy` (pulled by `ccl_chromium_reader`), MIT.

## Runtime dependencies

- Requests (Apache-2.0), ONNX Runtime (MIT), NumPy (BSD-3-Clause),
  Pillow (HPND), qrcode (BSD-3-Clause). Their licenses are distributed with
  the packages installed by `pip`.

## Auth bridge assets

- `core/booking/assets/AUTH-BRIDGE-NOTICE.txt` and
  `core/booking/assets/LICENSE-WMPFDebugger.txt` are retained from the
  upstream booking core. Do not remove them when redistributing.

## Trademarks

Xiamen University, WeChat, and related names and marks belong to their
respective owners. This project is unofficial and is not endorsed by or
affiliated with them.
