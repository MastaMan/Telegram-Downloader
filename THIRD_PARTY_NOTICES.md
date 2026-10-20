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
