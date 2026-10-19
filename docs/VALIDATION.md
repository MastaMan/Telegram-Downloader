# Validation and release limits

## Local validation — 19 October 2026

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

The repository includes a GitHub Actions workflow for Windows x64 and Python 3.11, 3.12, and 3.13. It runs the tests, including actual DPAPI encryption/decryption and tamper detection, and produces a Windows GUI screenshot. The presence of this workflow is **not** a claim that its run has already passed. Consult the repository's Actions page for current run results.

## Not verified with a real Windows desktop / Telegram account

- Double-click launch, first-time setup, Windows file-association behavior, and Explorer shortcut creation on a user's actual machine.
- Successful QR authorization against Telegram, real two-step passwords, and session revocation/reconnection.
- Live access to a particular private group/channel, Telegram content restrictions, actual download speed, and real-world rate limits.
- Recovery from a physical power loss or disk failure. Tests exercise controlled interruptions and persisted partial-file states.

## Suggested acceptance check on your Windows computer

1. Extract to a writable folder and double-click Start.bat. Verify setup completes and the app opens.
2. Enter API credentials, scan the QR, and confirm the displayed account ID.
3. Select a small private group/channel and a temporary download folder. Scan for ZIPs.
4. Download a known ZIP, verify it opens, then pause/resume a larger ZIP.
5. Close the window during that larger transfer. Reopen: the previous chat should be selected automatically; Start should resume from the partial file.
6. Switch to a second chat during a transfer, then return. Verify both queues and the partial download are retained.
7. Start the launcher twice and verify there is only one application instance.
8. Confirm that Local AppData contains `credentials.dpapi` and no newly written plaintext Telethon `.session` or API-key JSON file.

No Windows executable or digital-signing certificate is included. The release consists of source, one Windows launcher, pinned dependency requirements, tests, and documentation.
