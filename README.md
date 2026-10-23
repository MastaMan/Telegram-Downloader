# Telegram File Hunter

Formerly Telegram Archive Desktop. A standalone Windows application for downloading ZIP and RAR attachments from Telegram groups and channels that your account can access. Built with Python, PySide6 and Telethon. **No 3ds Max or other host application is required.**

![Interface preview with synthetic sample data](docs/interface-preview.png)

## Start here

1. Extract the entire project into a writable folder, for example `C:\TelegramDownloader`. Do not run directly inside a ZIP or place it in `Program Files`.
2. Double-click **`Start.bat`**. This is the only launch file you need. It first closes any earlier running copy of the app and the old downloader scripts, so only a clean current version runs. It finds a suitable Python 3.14 or downloads an official private runtime automatically, then installs the dependencies and opens the app. No manual Python installation or administrator rights are needed.
3. Open **API settings**, enter your API ID and API Hash, then click **Save**. Get them from [Telegram API development tools](https://my.telegram.org/apps).
4. In Telegram on your phone, open **Settings → Devices → Link Desktop Device** and scan the displayed QR code. If requested, enter your two-step verification password in the application.
5. Choose a group or channel, choose a download folder, click **Scan archives**, then **Start**. Or click **Pin** and then the chat in the pinned list to scan and download in one step. Up to **20 archives download simultaneously** by default. Use **Parallel downloads** in the sidebar to choose 1–20; the setting is remembered. Stop the queue before changing it.

The selected chat is remembered for each account and restored next time. The chat selector stays available during scanning, downloading, and pauses. Switching chats safely stops the current operation, retains its partial files, and opens the other chat's saved queue. **Downloads never start automatically on launch or chat selection.** Only clicking a pinned chat in the hunting list starts scanning and downloading it.

For a desktop shortcut, right-click `Start.bat` → **Show more options → Send to → Desktop (create shortcut)**. Keep the project folder in place. The shortcut still points to the same single launcher.

## Download performance

The launcher installs **cryptg 0.6.0** automatically on Python 3.11–3.14 and checks that Telethon uses it. This accelerates encryption/decryption; actual download speed still depends on Telegram, the connection, and disk activity. The Activity log reports the active encryption backend.

The normal Windows launcher automatically uses Python 3.14 with cryptg in `.venv-py314`, including when upgrading from a Python 3.15 environment. The old `.venv` is retained. If Python 3.14 is missing, the official full runtime is downloaded to `%LOCALAPPDATA%\TelegramZipDesktop\runtime` and checked against a pinned SHA-256 before use. System Python, PATH, file associations, credentials, and downloads are unchanged. Source development on Python 3.11–3.15 remains supported.

Intermediate download progress is batched into one database transaction about once per second for all transfers. Combined statistics refresh about four times per second, instead of rescanning the queue for every file callback. Completion and stop/error state changes commit the latest progress immediately; pause and orderly shutdown also flush pending checkpoints. Resume still uses the actual `.part` file length, so a stale display checkpoint cannot cause the whole file to restart.

## What is included

- Resizable window that reopens at its last position and size (1150×640 the first time), down to 500×340, with settings accessible in a separate window at compact sizes.
- Compact dark interface: foldable settings cards with one-line summaries, colour-coded buttons (green start/hunt, amber pause/finish, red stop/sign out, teal tools), real check marks, dense queue rows, downloaded/total bytes and inline progress bars for each archive, overall and active-file progress, speed, time remaining, and a separate Activity log window.
- A single horizontal row shows a separate progress bar and percentage for every active download, including while browsing other queue pages.
- The queue opens in **Unfinished** mode. Switch to **Downloaded** or **All** and browse the entire saved history in pages of 200 files, with direct page-number navigation.
- **Parallel downloads** changes live from 1 to 20. Raising it starts more files; lowering it lets active files finish before new ones start. The active count and selected limit are shown together.
- **Speed limit** dropdown: Unlimited by default, or 10 / 5 / 2 / 1 / 0.5 / 0.25 MB/s shared across all downloads. Change it live; the choice is remembered.
- **Check updates** downloads the latest public GitHub main version and restarts through the dependency launcher. Settings, sessions, downloads, and the queue are preserved.
- Groups and channels, including archived dialogs; searchable chat selector and automatic restoration of the last selection.
- **Pinned chats** in a scrollable list: one click scans the chat for new messages and downloads the archives allowed by the Hunting rules. **Hunt all** does this for every pinned chat in turn.
- Scans request only messages with documents and continue automatically after Telegram FloodWait requests.
- **Hunting rules** for all chats: a filename filter (regex or plain text; by default `^[0-9a-z]+\.[0-9a-z]+\.(zip|rar)$` for names such as `7407604.67a7dac35b142.rar`) and skipping archives already downloaded in any chat, by name or Telegram file. Skipped archives remain visible in their own queue view.
- All chats save into one folder by default; **Folder per chat** keeps a subfolder per chat. Archives without downloaded data follow the current setting.
- **Import old files…** renames archives from the earlier scripts (`group_<id>` folders, `name__msg<id>.zip`) to their original names, moves them into the download folder without overwriting, and skips them in future downloads.
- ZIP and RAR selection: `.zip` / `.rar` filenames, case-insensitive; nameless documents require a matching MIME type.
- Original filenames are preserved, including extension case and long names. Only collisions add a removable prefix such as `__dup_2__`; Windows-invalid characters still need replacement.
- Completed archives saved with old numbered names are restored automatically for the selected chat, including files in `wrong_archives`, using verified Telegram metadata and a recoverable rename journal.
- Automatic background checks with installed WinRAR after each download, while other files download, plus **Check archives** for previously downloaded files. WinRAR's test decompresses every file and compares its CRC, so the queue shows **Intact**, **Damaged**, or **Password**. Three confirmed failures move a damaged archive to `wrong_archives\damaged` and a password-protected one to `wrong_archives\password`, without overwriting existing files.
- Pause, resume, stop, network retries, rate-limit waits, and resumable `.part` files.
- **Finish current** completes active downloads and pending archive checks, then stops without starting another archive. **Start** continues the remaining queue.
- Durable SQLite queue and incremental history-scan checkpoints, isolated by Telegram account and chat. The queue database can live in another local or synced folder (such as Dropbox), and **Backup** saves checked snapshots next to it.
- QR sign-in with automatic code refresh and optional two-step verification password.
- API credentials **and the Telegram authorization session** encrypted with Windows DPAPI. No plaintext session file is written by the application.
- Optional import of the old `telegram_config.json` and `telegram_zip_session.session`.
- One running instance per Windows user; orderly worker shutdown when the window closes.
- Automated tests and a manually started Windows GitHub Actions workflow for Python 3.11–3.15. Pushes do not launch it or generate failure-notification emails from this workflow.

## Archive checks and upgrading

Install [WinRAR for Windows](https://www.win-rar.com/download.html) to enable archive checks. The app detects standard installations; otherwise use **WinRAR…** to select `WinRAR.exe`. The selected path is remembered. WinRAR is a separate licensed application and is not bundled or installed by the Python setup. Downloads work without it and remain **Unchecked**.

Checks follow the supplied `check_archives.bat` policy: an integrity test without a password, retried up to three times, moving a file only when all three results are codes 2, 3, or 11. Three code 11 results mean a password-protected archive (`wrong_archives\password`); any other qualifying combination means a damaged archive (`wrong_archives\damaged`). Operational errors leave files in place. Moved files stay inside their download folder and are not downloaded again. Up to 20 downloads and one archive check can run at the same time. Each free download slot immediately picks up the next archive; it does not wait for a batch to finish. The main card shows active downloads, overall progress, combined speed, and estimated time for the queue. Each row shows that archive’s progress. Telegram and your connection determine actual throughput; 20 transfers do not guarantee a 20-fold speed increase. Reduce Parallel downloads if you encounter frequent rate limits. If checking takes longer, completed files wait in a saved check queue. **Start** also resumes pending checks after a stop or restart. Pause, Stop, chat switching, and closing the window apply to all downloads and the archive checker. The app finishes the remaining check queue after the last download. Use **Check archives** to recheck the current chat's previously downloaded files; hover over any status for details. This does not guess passwords or accept archive passwords.

After updating, click **Scan archives** once in each saved chat to discover historical RAR attachments. Existing downloaded ZIPs, partial files, credentials, and selected chats are retained. The application data folder remains `%LOCALAPPDATA%\TelegramZipDesktop` for compatibility. Launching still uses only **Start.bat**.

## Update the application

Click **Check updates** in the header while no operation is running. The app checks the latest commit on this repository's `main`, downloads it in the background, saves its state, installs the update after closing, and restarts automatically. It uses the public GitHub API without a token. **Stop** cancels preparation before installation.

ZIP installations receive a commit-pinned source archive; the app checks the package, backs up managed files, and restores them if replacement fails. Git installations require Git on PATH, a clean `main`, and fast-forward history; local source changes are never discarded. The restarted launcher installs changed Python dependencies if needed. See the user guide for recovery and limits.

Versions released before this button was added need one manual update: download the latest repository ZIP (or pull `main`) and replace the application sources. Keep your `.venv`, application data, and downloads. Future versions can use the button.

## Documentation

- [Detailed user guide](docs/USER_GUIDE.md)
- [Architecture and development](docs/DEVELOPMENT.md)
- [Release procedure and publication approval](docs/RELEASING.md)
- [Validation results and limitations](docs/VALIDATION.md)
- [Third-party dependencies](THIRD_PARTY_NOTICES.md)

## Important boundaries

The app uses the permissions of your own Telegram account. It does not join groups, bypass permissions or content restrictions, scrape invite links, or download inaccessible material. Telegram must be reachable from your computer. A custom proxy UI is not included.

Archives are not extracted or executed. A completed download is **Unchecked** until WinRAR successfully tests it. **Intact** means every file in the archive passed WinRAR's CRC test without a password; it is not an antivirus or authenticity check. Known multipart `.partNN.rar` sets require manual validation with all volumes present.

This is a source distribution with a launcher, **not a signed Windows EXE installer**. The launcher downloads an official private Python runtime when required and application libraries from PyPI. First-time setup requires internet access and Windows PowerShell; subsequent healthy launches reuse the prepared environment. Actual Telegram login and downloading require your account and have not been exercised against a real account in the development environment. See the validation document for precise test coverage.
