# Telegram ZIP Desktop

A standalone Windows application for downloading ZIP attachments from Telegram groups and channels that your account can access. Built with Python, PySide6 and Telethon. **No 3ds Max or other host application is required.**

![Interface preview with synthetic sample data](docs/interface-preview.png)

## Start here

1. Install **Python 3.12, 64-bit**, from [python.org](https://www.python.org/downloads/windows/). Keep the Python launcher and Tcl/Tk options enabled. Enable **Add python.exe to PATH**. Python 3.11–3.13 is supported.
2. Extract the entire project into a writable folder, for example `C:\TelegramDownloader`. Do not run directly inside a ZIP or place it in `Program Files`.
3. Double-click **`Start.bat`**. This is the only launch file you need. First-time setup checks and installs pinned dependencies into a private `.venv`. It opens the application automatically when ready.
4. Open **API settings**, enter your API ID and API Hash, then click **Save**. Get them from [Telegram API development tools](https://my.telegram.org/apps).
5. In Telegram on your phone, open **Settings → Devices → Link Desktop Device** and scan the displayed QR code. If requested, enter your two-step verification password in the application.
6. Choose a group or channel, choose a download folder, click **Scan ZIPs**, then **Start**.

The selected chat is remembered for each account and restored next time. The chat selector stays available during scanning, downloading, and pauses. Switching chats safely stops the current operation, retains its partial files, and opens the other chat's saved queue. **Downloads never start automatically on launch or chat selection.**

For a desktop shortcut, right-click `Start.bat` → **Show more options → Send to → Desktop (create shortcut)**. Keep the project folder in place. The shortcut still points to the same single launcher.

## What is included

- Dark desktop interface with current-file and overall progress, transfer speed, estimated file time remaining, a persistent queue, and an activity panel.
- Groups and channels, including archived dialogs; searchable chat selector and automatic restoration of the last selection.
- ZIP-only selection: `.zip` filenames, case-insensitive; nameless documents require a ZIP MIME type. The completed download must also be recognized as a ZIP archive.
- Pause, resume, stop, network retries, rate-limit waits, and resumable `.part` files.
- Durable SQLite queue and incremental history-scan checkpoints, isolated by Telegram account and chat.
- QR sign-in with automatic code refresh and optional two-step verification password.
- API credentials **and the Telegram authorization session** encrypted with Windows DPAPI. No plaintext session file is written by the application.
- Optional import of the old `telegram_config.json` and `telegram_zip_session.session`.
- One running instance per Windows user; orderly worker shutdown when the window closes.
- Automated tests and a Windows GitHub Actions workflow for Python 3.11, 3.12 and 3.13.

## Documentation

- [Detailed user guide](docs/USER_GUIDE.md)
- [Architecture and development](docs/DEVELOPMENT.md)
- [Validation results and limitations](docs/VALIDATION.md)
- [Third-party dependencies](THIRD_PARTY_NOTICES.md)

## Important boundaries

The app uses the permissions of your own Telegram account. It does not join groups, bypass permissions or content restrictions, scrape invite links, or download inaccessible material. Telegram must be reachable from your computer. A custom proxy UI is not included.

ZIP archives are not extracted or executed. ZIP recognition checks the archive structure, not every entry's CRC, authenticity, or safety. Very large archives may contain unsafe content just like files downloaded manually.

This is a source distribution with a launcher, **not a signed Windows EXE installer**. Python is a prerequisite; Python itself is not installed automatically. Setup downloads the application libraries from PyPI. Actual Telegram login and downloading require your account and have not been exercised against a real account in the development environment. See the validation document for precise test coverage.
