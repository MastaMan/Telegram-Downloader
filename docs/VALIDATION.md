# Validation and release limits

## Compact interface and automatic updates — 19 October 2026

The update ran **102 tests per runtime: 101 passed, 1 skipped**, with exit code 0 on Python **3.15.0**, **3.14.8**, and **3.12.3** on macOS. The skip is the Windows-only DPAPI test. The compact interface was rendered and visually inspected; the default window shows approximately 11 queue rows with Activity collapsed, compared with roughly four previously.

Update tests cover ZIP preparation and installation, preservation of credentials/queue/download/venv fixture files, initial-version comparison, repeat checks, staged checksum changes, obsolete managed files, malformed ZIPs, Python syntax errors, unsafe/duplicate/symlink paths, disallowed redirects, download size limits, cancellation, and rollback after a simulated locked file. An interrupted-install journal is recovered on the next launcher check.

A real helper subprocess waits for a fixture parent to exit, installs source files, and starts a fixture bootstrap process. A real temporary Git checkout is fast-forwarded while retaining an untracked download. Mocked Git checks cover dirty-source refusal; process tests cover cancellation cleanup. Engine tests cover background preparation cancellation and the UI restart request. A subprocess regression test verifies Python 3.15 asyncio thread usage after importing the app and Qt.

The public GitHub commits endpoint was also contacted successfully without authentication. GitHub archive staging is tested with local ZIP fixtures; native Windows update handoff, locked-file behavior, WinRAR, live Telegram login, and dependency changes during a real Windows restart still require Windows acceptance testing. No claim is made that Windows CI has passed.

For Windows acceptance, install this version manually once, then use Check updates after a newer main commit is published. Confirm that sources update, the application restarts, saved account/chat/queue remain available, changed requirements trigger normal setup, and the next check reports up to date. Also check an offline attempt, cancellation during preparation, and a locally edited Git checkout; these must not overwrite sources.

## Concurrent downloads and archive checks — 19 October 2026

The updated source ran **78 tests per runtime: 77 passed, 1 skipped**, with exit code 0 on Python **3.15.0**, **3.14.8**, and **3.12.3** on macOS. The skip remains the Windows-only DPAPI test.

New tests hold a mock archive check open while later downloads finish, prove that each queued file is checked exactly once, and verify that the operation stays active until the check queue drains. They also cover shared pause/resume, chat switching that awaits both transfer and WinRAR process cleanup, cancellation after a fatal download error, restart recovery of pending checks without downloading again, and WinRAR disappearing before checks start. UI tests verify that background check updates preserve the current download's filename, speed, ETA, and progress.

The updated demo was rendered and visually inspected with one archive downloading and the previous archive being checked. Concurrency tests use simulated transfers and WinRAR results/processes; real Windows WinRAR behavior and live Telegram downloads remain unverified here.

For Windows acceptance, download several archives including one whose test is slow. Confirm that the next transfer advances while the previous file shows Checking, that Pause/Stop affect both operations, and that closing then reopening and pressing Start resumes pending checks. The app should remain in Checking archives after the final download until remaining checks finish.

## RAR support and archive checking — 19 October 2026

The update was tested on macOS with Python **3.15.0**, **3.14.8**, and **3.12.3**, using the pinned dependencies. **Each runtime ran 71 tests: 70 passed, 1 skipped, with exit code 0.** The skip is the Windows-only DPAPI test.

New checks cover ZIP/RAR filtering and MIME fallback, exact-byte RAR resume, ZIP content with a RAR filename, original database schema migration, and the one-time historical rescan without discarding completed ZIPs. Synthetic RAR transfer bytes exercise transport and resumption, not the RAR decoder.

Mocked WinRAR process results exercise immediate success, success after retry, three confirmed failures, mixed operational errors, timeouts, cancellation, and pause before result handling. The exact executable arguments are checked. A separate test starts and terminates a real Python child process to verify subprocess cleanup; this is not a real WinRAR test.

Filesystem and engine tests exercise automatic checking after download completion, cancellation that preserves the completed file, quarantine moves, collision preservation, missing/changed files, missing WinRAR, multipart refusal, and recovery after a move but before its final database update. Qt checks cover verification statuses, controls, and clean worker shutdown. The updated screenshot was rendered by the real GUI with synthetic data and visually inspected.

**WinRAR itself was not available in this macOS environment.** Its actual ZIP/RAR password behavior, process/UI behavior, installed-path discovery, and Windows move semantics still need the Windows acceptance check below. No real Telegram account was used. The Windows CI workflow exists but its result was not independently confirmed here.

The earlier ZIP structure-only check was replaced by explicit verification states: expected download bytes can be complete while content remains Unchecked. WinRAR handles format and integrity tests when configured. The historical test results below describe their respective earlier releases.

## Python 3.14 / 3.15 update — 19 October 2026

The final updated source was tested on macOS in separate environments with Python **3.15.0**, **3.14.8**, and **3.12.3**, using PySide6 Essentials / Shiboken6 **6.12.0** and all other pinned requirements.

**Each interpreter ran 49 tests: 48 passed, 1 skipped; each process exited with code 0.** The skipped check is real Windows DPAPI. These are local macOS results, not Windows CI or real-account Telegram results.

Added coverage includes supported-version and build checks, a side-effect-free launcher probe, broken environment handling, imports of the pinned dependencies in a subprocess, initialization of a real offline Telethon client on a running asyncio loop, and a separate demo process that renders a PNG and exits normally.

The update also explicitly closes the read-only legacy-session database and destroys Qt widgets before interpreter teardown. This resolved an unclosed SQLite connection warning and a Qt cleanup crash exposed during the compatibility test run. The final Python 3.15 demo image was visually inspected.

The newest supported stable release was verified against [Python 3.15.0](https://www.python.org/downloads/release/python-3150/); the Qt package's Python range and wheels were checked in [PySide6 Essentials 6.12.0 metadata](https://pypi.org/project/PySide6-Essentials/6.12.0/). Standard 64-bit CPython 3.11–3.15 is accepted. Free-threaded builds and prereleases are not supported.

## Original local validation — 19 October 2026

Environment: macOS, Python 3.12.3, Telethon 1.45.0, PySide6 Essentials 6.10.1, qrcode 8.2. The pinned requirements were installed into an isolated environment for testing.

**39 automated tests executed: 38 passed, 1 skipped.** The skipped test exercises actual Windows DPAPI and requires Windows. No real Telegram credentials were supplied.

The passing checks cover:

- ZIP selection and Windows-safe filenames, including reserved names and traversal characters.
- Account/chat isolation, duplicate scan prevention, atomic scan checkpoints, and crash recovery of an in-progress queue entry.
- Exact downloaded bytes, resumed-file SHA-256 equality, unaligned-tail recovery, incomplete transfers, rejected non-ZIP content, oversized partial files, and non-overwrite behavior.
- Network interruption, retained partial files, resumed downloads, pause/resume, cancellation while paused, and cancellation during a pending network read.
- Incremental message scans, scan cancellation checkpoints, groups/channels, restoration of the last selected chat, and missing-chat behavior.
- Chat switching while an operation is running, changed-attachment rejection, expired-file-reference refresh, QR expiration, and two-step verification retry using mocked Telegram clients.
- Read-only import of a standard Telethon SQLite authorization session, key validation, encryption-boundary use, and refusal to store secrets without Windows DPAPI.
- Qt rendering, visual progress states, API form validation, QR rendering, single-instance locking, and shutdown of a real background worker without a thread leak.

The interface was rendered with synthetic sample data, visually inspected, and adjusted to remove clipped controls. `docs/interface-preview.png` is a real render of the application in demo mode, not proof of a live Telegram download.

## Windows checks

The repository includes a GitHub Actions workflow for Windows x64 and Python 3.11–3.15. It runs the tests, including actual DPAPI encryption/decryption and tamper detection, checks the launcher's interpreter probe, and produces a Windows GUI screenshot. The presence of this workflow is **not** a claim that its run has already passed. Consult the repository's Actions page for current run results.

## Not verified with a real Windows desktop / Telegram account

- Double-click launch, first-time setup, Windows file-association behavior, and Explorer shortcut creation on a user's actual machine.
- Successful QR authorization against Telegram, real two-step passwords, and session revocation/reconnection.
- Live access to a particular private group/channel, Telegram content restrictions, actual download speed, and real-world rate limits.
- Recovery from a physical power loss or disk failure. Tests exercise controlled interruptions and persisted partial-file states.

## Suggested acceptance check on your Windows computer

1. Extract to a writable folder and double-click Start.bat. Verify setup completes and the app opens.
2. Enter API credentials, scan the QR, and confirm the displayed account ID.
3. Select a small private group/channel and a temporary download folder. Click Scan archives and verify both ZIP and RAR attachments appear, including older RAR files after upgrading.
4. Download known ZIP and RAR files, verify they open, then pause/resume a larger RAR.
5. Close the window during that larger transfer. Reopen: the previous chat should be selected automatically; Start should resume from the partial file.
6. Switch to a second chat during a transfer, then return. Verify both queues and the partial download are retained.
7. Start the launcher twice and verify there is only one application instance.
8. Confirm that Local AppData contains `credentials.dpapi` and no newly written plaintext Telethon `.session` or API-key JSON file.

9. Install WinRAR or select WinRAR.exe. Download known unencrypted ZIP/RAR fixtures, an encrypted fixture, a deliberately damaged fixture, and a ZIP renamed to .rar. Confirm successful files are Verified and repeated eligible failures move to wrong_archives. Never use irreplaceable files for this acceptance check.
10. Use Check archives on older downloaded queue items. Check missing WinRAR, a missing local file, and an operational access error: none should be moved. Confirm existing wrong_archives filenames are preserved.
11. Pause a long archive check, then resume; Stop or close during another check. Confirm the app's WinRAR process exits and the downloaded file remains available for a later check. Restart and verify quarantined files are not downloaded again.

No Windows executable or digital-signing certificate is included. The release consists of source, one Windows launcher, pinned dependency requirements, tests, and documentation.
