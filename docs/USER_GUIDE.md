# User guide

## 1. System requirements

- Windows 10 or 11, x64, with 64-bit Python 3.11–3.13. Python 3.12 is recommended.
- Python's standard Tcl/Tk component, used for the first-time setup window.
- An internet connection, access to Telegram, and enough space for the dependencies and downloaded archives. Allow roughly 1 GB for setup, in addition to your downloads.
- A Telegram account already authorized in the official mobile app and already able to open the required group or channel.
- Your own API ID and API Hash from `https://my.telegram.org/apps`.

The application is independent of 3ds Max. It runs as a normal desktop program.

## 2. Install and launch

Extract **all files** into a writable directory, such as `C:\TelegramDownloader`. Avoid very long paths. Double-click **Start.bat** every time you want to open the app.

The launcher first looks for Python 3.12, then 3.13, then 3.11 using the Windows Python launcher. It falls back to `python` on PATH. An unsupported or 32-bit interpreter produces an explanatory error. If Python is missing, the official Windows download page opens; install Python and run Start.bat again.

On first launch, a setup window creates `.venv`, checks dependencies, and installs the required pinned versions. No administrator rights are required. Later launches check the environment and normally go straight to the application. A console window may briefly appear while Start.bat prepares the launch.

Do not move the project while it is running. If you move it later and the environment no longer works, close the app, remove only the project's `.venv` directory, and run Start.bat to recreate it. Your credentials and queue live separately under Local AppData.

You may create one Windows desktop shortcut to Start.bat using the **Send to → Desktop (create shortcut)** menu. You do not need separate download and QR-login launchers.

## 3. Enter API credentials

Open **API settings**. Enter your API ID (a positive number) and API Hash (32 hexadecimal characters). The hash input is masked. The **Open Telegram API settings** button opens Telegram's official API page.

After Save, the credentials are protected using the current Windows user's DPAPI. They are not stored in a plaintext JSON configuration. The settings dialog deliberately does not display a previously saved hash. To change credentials, enter both fields again or import the old configuration.

## 4. Sign in with the QR code

The QR window appears when the saved session is missing or no longer authorized. On your phone:

1. Open Telegram.
2. Open **Settings → Devices → Link Desktop Device**. Wording can differ slightly between Telegram versions.
3. Scan the code shown on your computer and confirm the device.

The code refreshes automatically if it expires. Keep it private. The application renders it in memory and does not save a QR image to disk.

If two-step verification is enabled, enter your Telegram password in the password dialog. Incorrect passwords can be retried. The password is never added to the saved credentials. Canceling either sign-in dialog stops authorization.

Once connected, the account name and numeric account ID appear on the left. Normally later launches reuse the encrypted session and reconnect without another QR scan. **Sign out** revokes this app's Telegram session and removes its saved authorization. It keeps the API credentials, queue, selected-chat preferences, and downloaded files.

## 5. Pick a group or channel once

Use **Groups & channels**. You can type part of a name to search, or use the drop-down list. Hover over a list entry to see its numeric chat ID when names are identical.

The list includes groups, broadcast channels, private chats of those types, and archived dialogs visible to the signed-in account. Ordinary one-to-one conversations are not included.

The last selected chat is saved per Telegram account. On the next launch, it is automatically selected after the account reconnects and the chat list loads. If that chat has become inaccessible, the app explains this and leaves the selection empty; it never silently starts using a different chat.

The selector stays available while downloads are running or paused. Selecting another chat cancels the current operation cleanly, keeps its `.part` file and queue, and displays the chosen chat. Return to the original chat and press Start to resume its work. **Refresh chats** is available when no operation is active; stop the current operation first if you need to refresh the list.

If a private group is missing, check that it opens in Telegram under the same account ID shown in this app. Then click Refresh chats. The app does not accept invite links or join groups automatically.

## 6. Choose where archives are saved

Click **Choose folder…** and select a local folder with enough free space. The location is remembered.

New files are organized as:

```text
<chosen folder>\account_<account ID>\chat_<chat ID>\
    <message ID>_<document ID>_<safe original filename>.zip
```

The identifiers prevent collisions when two attachments have the same name. Windows-invalid filename characters are replaced, reserved names are handled, and long names are shortened. Keep the chosen folder path short enough for Windows path limits; `C:\TelegramFiles` is a good example.

**Open folder** opens the currently selected base folder in Explorer. Hover over a queue row to see that file's full path, or its error if it has failed.

Changing the folder applies to **newly discovered files**. Existing queue entries retain their original paths, so incomplete downloads can still resume safely. Do not move `.part` files while downloading.

## 7. Scan and download

**Scan ZIPs** reads message history from oldest to newest and saves matching ZIP documents to the queue. The first scan can take time in a large group. The footer shows the number of messages checked; the queue fills as files are found.

Only filenames ending in `.zip` are selected, case-insensitively. If a document has no filename, its MIME type must indicate ZIP. Files such as `.rar`, `.7z`, `.zip.exe`, and multipart `.z01` attachments are excluded. This app does not assemble split archives.

The scan checkpoint is saved after each processed message. Stop, close, or restart the app, then click Scan ZIPs to continue. Later scans look for newer messages, so they do not need to re-read the full history. They do **not** revisit older edited messages. A file whose document ID or size changed after discovery is reported as failed instead of appending different content to an existing partial file.

Click **Start** after scanning to process the saved queue, one file at a time. Start also retries failed items once per queue pass. Completed items are skipped.

The interface shows:

| Display | Meaning |
| --- | --- |
| Current download bar | Bytes saved for the current archive |
| Transfer speed | Recent download rate; resets when paused or stalled |
| File time left | Estimate for the current file, based on recent speed |
| Files completed | Fully completed files divided by all discovered files |
| Green queue bar | Received bytes across the queue; a failed partial file can still contribute bytes |
| Completed | Expected byte count received, ZIP recognized, and final filename created |
| Failed | Hover over the row and check Activity for the reason |

The queue processes every saved entry. For responsiveness, the table displays the first 1,000 entries; totals include the full queue.

## 8. Pause, resume, stop, and close

**Pause** suspends a download or scan. A request already in progress may finish, but its data is held until you resume. No new file chunks are requested while the transfer is waiting at the pause gate. **Resume** continues the same operation.

**Stop** cancels the active scan/download, including a pending network read or rate-limit wait. It keeps finished archives, partial files, and the scan checkpoint. Use Start to resume queued downloads; use Scan ZIPs to continue scanning.

Closing the main window disables controls, flushes the current partial file, saves queue/session state, disconnects Telethon, and waits for the worker to finish. The window closes after cleanup. Do not kill the process while this message is shown unless it is genuinely stuck.

After an ordinary restart, select nothing: the previous chat is restored. Press Start to resume. The actual `.part` file length determines the restart offset. An incomplete trailing block of up to 512 KB may be downloaded again to maintain alignment.

After an abrupt power loss or forced termination, the app recovers queued states and partial files. It cannot guarantee that hardware committed every last write. Completed files are not automatically re-downloaded if you later delete or move them; keep the final files in their recorded locations.

## 9. Import the earlier scripts' settings

The old `telegram_zip_downloader.py` and `telegram_qr_login.py` are replaced by this one application. Their exact source attachments were not available when this version was created, so this is a new implementation that preserves their described workflow.

1. Close both old scripts so they are not using the same Telegram authorization.
2. Open API settings → **Import old config…** and select `telegram_config.json`. The importer accepts `api_id` / `api_hash` or `API_ID` / `API_HASH` fields.
3. Optionally choose **Import old session…** and select the old standard Telethon SQLite `.session` file, for example `telegram_zip_session.session`.
4. Click Save. The app reads the old authorization key without modifying the source file, converts it to a memory session, and saves only the DPAPI-encrypted representation. Unsupported or unauthenticated sessions are rejected.

The original JSON and `.session` files remain unchanged and may contain sensitive plaintext authorization data. Keep them private. The app does not delete them automatically. Their old download queues or untracked partial files are not imported. If importing fails, enter the API credentials and sign in using a fresh QR code.

## 10. Storage and privacy

Application data is under `%LOCALAPPDATA%\TelegramZipDesktop`:

| File | Contents |
| --- | --- |
| `credentials.dpapi` | Encrypted API ID, API Hash, and Telegram authorization session |
| `settings.json` | Download folder and last selected chat per account; no API secret |
| `queue.sqlite3` and its WAL/SHM files | Message/document identifiers, archive names, sizes, paths, scan checkpoints, and statuses |
| `app.lock` | Prevents another copy from opening the same queue |

DPAPI normally binds saved secrets to the same Windows user on the same computer. Copying the encrypted file to another computer is not a migration method: sign in again there. DPAPI does not protect against malicious code already running as your Windows user. Downloaded archives and queue metadata are **not** encrypted by the app.

The project directory also contains `.venv`, `.setup.lock`, `setup.log`, and `startup.log` after launch. Setup logs contain package-install output. The on-screen Activity panel is kept in memory. The application avoids logging credentials, raw RPC requests, passwords, or QR URLs.

To remove the application, sign out first if possible, close it, then delete its project folder and its Local AppData folder. Downloaded archives are in your chosen download folder and must be removed separately if desired. You can also revoke this device from Telegram's Devices screen.

## 11. Troubleshooting

| Symptom | Action |
| --- | --- |
| Setup fails | Read `setup.log`; check internet access, proxy/firewall policies, supported Python version, and write permission. Run Start.bat again. |
| Window never appears | Check `startup.log`. A second instance may already be open. Repair Python or recreate only `.venv` if necessary. |
| Saved credentials cannot be read | Use the same Windows user/computer. To recover from a corrupt vault, close the app, back up `credentials.dpapi`, move it out of the data folder, and sign in again. |
| API credentials rejected | Re-enter the pair from your Telegram API application; a bot token is not an API Hash. |
| QR login expires | The app refreshes it automatically. Verify phone connectivity and the Devices screen. |
| Channel missing | Confirm account ID and access in the official app, then Refresh chats. |
| No files found | Check that attachments really have `.zip` names, or a ZIP MIME type when nameless. Scan only processes messages newer than its saved checkpoint. |
| Telegram rate limit | Activity shows the required wait. Leave it running, or Stop and try later. |
| Slow downloads | Speed depends on Telegram, your connection and account limits. One sequential download is intentional. |
| Disk full or permission error | Free space or restore permission for the recorded path, then Start. Existing queue entries do not change paths when the base folder changes. |
| Not a readable ZIP | The `.part` file is kept and the item is failed. Check the source attachment. ZIP structure recognition is not a CRC test or an antivirus scan. |
| Existing destination differs | Move the conflicting file aside before retrying. The application does not overwrite it. |
| Attachment changed or was deleted | The old partial is preserved; that queue item cannot be safely resumed. A fresh repost with a new message ID can be discovered by Scan ZIPs. |

## 12. Known limits

Windows is required for real account storage. Other operating systems can preview the GUI and run most tests using demo mode. A custom Telegram proxy configuration screen, continuous monitoring for new messages, bandwidth limiting, parallel file downloads, full ZIP CRC verification, old-history rescanning, and automatic recovery of deleted completed files are not included. First-run dependency installation and real Telegram account flows should be verified on your Windows machine before unattended use.
