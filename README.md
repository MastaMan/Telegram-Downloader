# Telegram Archive Desktop

A standalone Windows application for downloading ZIP and RAR attachments from Telegram groups and channels that your account can access. Built with Python, PySide6 and Telethon. **No 3ds Max or other host application is required.**

![Interface preview with synthetic sample data](docs/interface-preview.png)

## Start here

1. Install **Python 3.15, 64-bit**, from [python.org](https://www.python.org/downloads/windows/). Keep the Python launcher and Tcl/Tk options enabled. Enable **Add python.exe to PATH**. Python 3.11–3.15 is supported.
2. Extract the entire project into a writable folder, for example `C:\TelegramDownloader`. Do not run directly inside a ZIP or place it in `Program Files`.
3. Double-click **`Start.bat`**. This is the only launch file you need. First-time setup checks and installs pinned dependencies into a private `.venv`. It opens the application automatically when ready.
4. Open **API settings**, enter your API ID and API Hash, then click **Save**. Get them from [Telegram API development tools](https://my.telegram.org/apps).
5. In Telegram on your phone, open **Settings → Devices → Link Desktop Device** and scan the displayed QR code. If requested, enter your two-step verification password in the application.
6. Choose a group or channel, choose a download folder, click **Scan archives**, then **Start**.

The selected chat is remembered for each account and restored next time. The chat selector stays available during scanning, downloading, and pauses. Switching chats safely stops the current operation, retains its partial files, and opens the other chat's saved queue. **Downloads never start automatically on launch or chat selection.**

For a desktop shortcut, right-click `Start.bat` → **Show more options → Send to → Desktop (create shortcut)**. Keep the project folder in place. The shortcut still points to the same single launcher.

## What is included

- Dark desktop interface with current-file and overall progress, transfer speed, estimated file time remaining, a persistent queue, and an activity panel.
- Groups and channels, including archived dialogs; searchable chat selector and automatic restoration of the last selection.
- ZIP and RAR selection: `.zip` / `.rar` filenames, case-insensitive; nameless documents require a matching MIME type.
- Automatic checks with installed WinRAR after each download, plus **Check archives** for previously downloaded files. Three confirmed password/archive failures move the file to `wrong_archives` without overwriting existing files.
- Pause, resume, stop, network retries, rate-limit waits, and resumable `.part` files.
- Durable SQLite queue and incremental history-scan checkpoints, isolated by Telegram account and chat.
- QR sign-in with automatic code refresh and optional two-step verification password.
- API credentials **and the Telegram authorization session** encrypted with Windows DPAPI. No plaintext session file is written by the application.
- Optional import of the old `telegram_config.json` and `telegram_zip_session.session`.
- One running instance per Windows user; orderly worker shutdown when the window closes.
- Automated tests and a Windows GitHub Actions workflow for Python 3.11–3.15.

## Archive checks and upgrading

Install [WinRAR for Windows](https://www.win-rar.com/download.html) to enable archive checks. The app detects standard installations; otherwise use **WinRAR…** to select `WinRAR.exe`. The selected path is remembered. WinRAR is a separate licensed application and is not bundled or installed by the Python setup. Downloads work without it and remain **Unchecked**.

Checks follow the supplied `check_archives.bat` policy: test without a password, retry up to three times, and move only when all three results are codes 2, 3, or 11. Operational errors leave files in place. Files move to `wrong_archives` inside their existing chat download folder and are not downloaded again. Use **Check archives** to test the current chat's previously downloaded files; hover over any status for details. This does not guess passwords or accept archive passwords.

After updating, click **Scan archives** once in each saved chat to discover historical RAR attachments. Existing downloaded ZIPs, partial files, credentials, and selected chats are retained. The application data folder remains `%LOCALAPPDATA%\TelegramZipDesktop` for compatibility. Launching still uses only **Start.bat**.

## Documentation

- [Detailed user guide](docs/USER_GUIDE.md)
- [Architecture and development](docs/DEVELOPMENT.md)
- [Validation results and limitations](docs/VALIDATION.md)
- [Third-party dependencies](THIRD_PARTY_NOTICES.md)

## Important boundaries

The app uses the permissions of your own Telegram account. It does not join groups, bypass permissions or content restrictions, scrape invite links, or download inaccessible material. Telegram must be reachable from your computer. A custom proxy UI is not included.

Archives are not extracted or executed. A completed download is **Unchecked** until WinRAR successfully tests it. **Verified** means the archive passed a password-free WinRAR test; it is not an antivirus or authenticity check. Known multipart `.partNN.rar` sets require manual validation with all volumes present.

This is a source distribution with a launcher, **not a signed Windows EXE installer**. Python is a prerequisite; Python itself is not installed automatically. Setup downloads the application libraries from PyPI. Actual Telegram login and downloading require your account and have not been exercised against a real account in the development environment. See the validation document for precise test coverage.
