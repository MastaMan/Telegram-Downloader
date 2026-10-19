# Telegram Archive Desktop

A standalone Windows application for downloading ZIP and RAR attachments from Telegram groups and channels that your account can access. Built with Python, PySide6 and Telethon. **No 3ds Max or other host application is required.**

![Interface preview with synthetic sample data](docs/interface-preview.png)

## Start here

1. Install **Python 3.15, 64-bit**, from [python.org](https://www.python.org/downloads/windows/). Keep the Python launcher and Tcl/Tk options enabled. Enable **Add python.exe to PATH**. Python 3.11–3.15 is supported.
2. Extract the entire project into a writable folder, for example `C:\TelegramDownloader`. Do not run directly inside a ZIP or place it in `Program Files`.
3. Double-click **`Start.bat`**. This is the only launch file you need. First-time setup checks and installs pinned dependencies into a private `.venv`. It opens the application automatically when ready.
4. Open **API settings**, enter your API ID and API Hash, then click **Save**. Get them from [Telegram API development tools](https://my.telegram.org/apps).
5. In Telegram on your phone, open **Settings → Devices → Link Desktop Device** and scan the displayed QR code. If requested, enter your two-step verification password in the application.
6. Choose a group or channel, choose a download folder, click **Scan archives**, then **Start**. Up to **20 archives download simultaneously** by default. Use **Parallel downloads** in the sidebar to choose 1–20; the setting is remembered. Stop the queue before changing it.

The selected chat is remembered for each account and restored next time. The chat selector stays available during scanning, downloading, and pauses. Switching chats safely stops the current operation, retains its partial files, and opens the other chat's saved queue. **Downloads never start automatically on launch or chat selection.**

For a desktop shortcut, right-click `Start.bat` → **Show more options → Send to → Desktop (create shortcut)**. Keep the project folder in place. The shortcut still points to the same single launcher.

## What is included

- Compact dark interface with a larger queue, current-file and overall progress, speed, time remaining, and a collapsible Activity log.
- **Check updates** downloads the latest public GitHub main version and restarts through the dependency launcher. Settings, sessions, downloads, and the queue are preserved.
- Groups and channels, including archived dialogs; searchable chat selector and automatic restoration of the last selection.
- ZIP and RAR selection: `.zip` / `.rar` filenames, case-insensitive; nameless documents require a matching MIME type.
- Automatic background checks with installed WinRAR after each download, while other files download, plus **Check archives** for previously downloaded files. Three confirmed password/archive failures move the file to `wrong_archives` without overwriting existing files.
- Pause, resume, stop, network retries, rate-limit waits, and resumable `.part` files.
- Durable SQLite queue and incremental history-scan checkpoints, isolated by Telegram account and chat.
- QR sign-in with automatic code refresh and optional two-step verification password.
- API credentials **and the Telegram authorization session** encrypted with Windows DPAPI. No plaintext session file is written by the application.
- Optional import of the old `telegram_config.json` and `telegram_zip_session.session`.
- One running instance per Windows user; orderly worker shutdown when the window closes.
- Automated tests and a Windows GitHub Actions workflow for Python 3.11–3.15.

## Archive checks and upgrading

Install [WinRAR for Windows](https://www.win-rar.com/download.html) to enable archive checks. The app detects standard installations; otherwise use **WinRAR…** to select `WinRAR.exe`. The selected path is remembered. WinRAR is a separate licensed application and is not bundled or installed by the Python setup. Downloads work without it and remain **Unchecked**.

Checks follow the supplied `check_archives.bat` policy: test without a password, retry up to three times, and move only when all three results are codes 2, 3, or 11. Operational errors leave files in place. Files move to `wrong_archives` inside their existing chat download folder and are not downloaded again. Up to 20 downloads and one archive check can run at the same time. Each free download slot immediately picks up the next archive; it does not wait for a batch to finish. The main card shows active downloads, overall progress, combined speed, and estimated time for the queue. Each row shows that archive’s progress. Telegram and your connection determine actual throughput; 20 transfers do not guarantee a 20-fold speed increase. Reduce Parallel downloads if you encounter frequent rate limits. If checking takes longer, completed files wait in a saved check queue. **Start** also resumes pending checks after a stop or restart. Pause, Stop, chat switching, and closing the window apply to all downloads and the archive checker. The app finishes the remaining check queue after the last download. Use **Check archives** to recheck the current chat's previously downloaded files; hover over any status for details. This does not guess passwords or accept archive passwords.

After updating, click **Scan archives** once in each saved chat to discover historical RAR attachments. Existing downloaded ZIPs, partial files, credentials, and selected chats are retained. The application data folder remains `%LOCALAPPDATA%\TelegramZipDesktop` for compatibility. Launching still uses only **Start.bat**.

## Update the application

Click **Check updates** in the header while no operation is running. The app checks the latest commit on this repository's `main`, downloads it in the background, saves its state, installs the update after closing, and restarts automatically. It uses the public GitHub API without a token. **Stop** cancels preparation before installation.

ZIP installations receive a commit-pinned source archive; the app checks the package, backs up managed files, and restores them if replacement fails. Git installations require Git on PATH, a clean `main`, and fast-forward history; local source changes are never discarded. The restarted launcher installs changed Python dependencies if needed. See the user guide for recovery and limits.

Versions released before this button was added need one manual update: download the latest repository ZIP (or pull `main`) and replace the application sources. Keep your `.venv`, application data, and downloads. Future versions can use the button.

## Documentation

- [Detailed user guide](docs/USER_GUIDE.md)
- [Architecture and development](docs/DEVELOPMENT.md)
- [Validation results and limitations](docs/VALIDATION.md)
- [Third-party dependencies](THIRD_PARTY_NOTICES.md)

## Important boundaries

The app uses the permissions of your own Telegram account. It does not join groups, bypass permissions or content restrictions, scrape invite links, or download inaccessible material. Telegram must be reachable from your computer. A custom proxy UI is not included.

Archives are not extracted or executed. A completed download is **Unchecked** until WinRAR successfully tests it. **Verified** means the archive passed a password-free WinRAR test; it is not an antivirus or authenticity check. Known multipart `.partNN.rar` sets require manual validation with all volumes present.

This is a source distribution with a launcher, **not a signed Windows EXE installer**. Python is a prerequisite; Python itself is not installed automatically. Setup downloads the application libraries from PyPI. Actual Telegram login and downloading require your account and have not been exercised against a real account in the development environment. See the validation document for precise test coverage.
