# Third-party dependencies

The application installs the versions pinned in `requirements.txt`. Upstream copyright notices and license terms remain applicable. Dependencies are installed from PyPI and are not vendored in the source archive.

| Package | Upstream | License summary |
| --- | --- | --- |
| cryptg | https://pypi.org/project/cryptg/ | CC0 wrapper; native dependencies retain their own license terms |
| Telethon | https://github.com/LonamiWebs/Telethon | MIT |
| PySide6 Essentials / Shiboken6 | https://www.qt.io/qt-for-python | LGPLv3 / GPL / commercial options; Qt components may have separate terms |
| qrcode | https://github.com/lincolnloop/python-qrcode | BSD |
| pyaes | https://github.com/ricmoo/pyaes | MIT |
| rsa | https://github.com/sybrenstuvel/python-rsa | Apache-2.0 |
| pyasn1 | https://github.com/pyasn1/pyasn1 | BSD |

Review the installed package license files and the Qt license documentation before redistributing a bundled executable. This repository currently ships application source and a dependency-installing launcher, not a bundled binary distribution. The project is not affiliated with Telegram or The Qt Company.

Optional archive validation uses the user's installed [WinRAR](https://www.win-rar.com/). WinRAR is proprietary, separately licensed software; it is not included or installed by this project. Its license and trial terms apply independently. The application invokes WinRAR to test ZIP/RAR files and is not affiliated with RARLAB or win.rar GmbH.

When no suitable Python is installed, the Windows launcher downloads an official full CPython 3.14.8 runtime ZIP from python.org. The runtime stays outside the repository source distribution and retains its included LICENSE.txt and dependency notices. Python is distributed under the PSF License Agreement and associated third-party notices: https://docs.python.org/3/license.html.

## Lucide dropdown icon

The [Lucide chevron-down](https://github.com/lucide-icons/lucide/blob/main/icons/chevron-down.svg) geometry is rendered directly with Qt in `tgzip/ui.py`, with application-specific size and colors. No icon package or external SVG file is required at runtime. This icon is derived from Feather; the applicable upstream notices from [Lucide's license](https://github.com/lucide-icons/lucide/blob/main/LICENSE) follow.

### ISC License

Copyright (c) 2026 Lucide Icons and Contributors

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

### The MIT License (MIT) — Feather-derived chevron-down

Copyright (c) 2013-present Cole Bemis

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
