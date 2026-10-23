# Validation and release limits

## Patch release 1.0.7: Hunt all, faster scans, and a movable queue database — 23 October 2026

**Hunt all** scans and downloads every accessible pinned chat in pin order within one operation, reports progress in the footer, continues after a failing chat, and stops after the active chat on Finish current. Scans now request document messages only (`InputMessagesFilterDocument`) and wait out FloodWait/FloodPremiumWait, resuming from the stored checkpoint instead of failing. The queue database can be copied to or opened from another local or synced folder (Use existing / Replace, keeping replaced files and their WAL), network folders are refused, and an unavailable folder blocks queue operations instead of falling back to a stale queue. **Backup** saves integrity-checked snapshots of the queue and settings beside the queue database, keeping ten; update backups now copy the queue actually in use.

Python **3.14.8** on macOS: **224 tests**, 216 passed and eight skipped (Windows DPAPI and seven launcher tests without a portable PowerShell). New coverage: Hunt all order, inaccessible and failing chats, and the no-pinned-chat error; scan restart after a FloodWait with the document filter requested; copying the queue, reopening it after restart, returning to the data folder; Use/Replace choices and the retained replaced files; refusal for network folders and running operations; blocked operations and recovery for an unavailable folder; manual backups including staged progress, their retention, and that update snapshots are not pruned; and the new sidebar controls. Compilation, the environment probe and whitespace checks passed. Offscreen previews were inspected at 1150×640 (top and scrolled), 500×340, and the Settings window at 480×600; the documentation previews were refreshed to v1.0.7.

Live Telegram searches and FloodWait timing, Dropbox synchronization, Windows network-drive detection, and real Windows rendering were not exercised. The document filter's speed gain depends on each chat's share of document messages.

## Patch release 1.0.6: pinned-chat hunting and global download rules — 22 October 2026

Chats can be pinned to a scrollable list; one click scans the chat for new messages and downloads in the same operation. Global Hunting rules apply to all chats: a filename filter (enabled by default; Python regex `^[0-9a-z]+\.[0-9a-z]+\.(zip|rar)$` or case-insensitive text) and skipping archives already downloaded in any chat by name or Telegram document. Excluded archives move to a new `skipped` state with a reason and their own queue view, and return to the queue when rules are relaxed. All chats now share the download folder by default; **Folder per chat** restores subfolders, and only archives without downloaded data follow a changed layout. The sidebar scrolls; the pinned list stays visible at startup.

Python **3.14.8** on macOS: **213 tests**, 205 passed and eight skipped (Windows DPAPI and seven launcher tests without a portable PowerShell). New coverage includes the default pattern, text mode and invalid regex handling; skipping by name, renamed Telegram document, and duplicates within one pass while quarantined copies stay downloadable; restoring skipped files after relaxing rules; live rule changes during a download; pinned-chat hunting from scan to download in a shared folder; pin persistence; relocation of unstarted files while partial files stay; and the sidebar controls, Skipped queue view and validation feedback. Existing download, archive-check and layout tests run with the new rules disabled where they test unrelated behavior; layout tests now expect a scrolling sidebar and reach lower controls by scrolling. Compilation, the environment probe and whitespace checks passed. Offscreen previews were inspected at 1150×640 (top and scrolled), 500×340, and the Settings window at 480×600 and 760×600; the documentation previews were refreshed to v1.0.6.

On a disposable copy of the owner's marked queue, the default rules classified the 47,649 remaining jobs in 0.2 seconds: 958 do not match the name filter and 46,691 remain to download. The original database was not modified. Live Telegram hunting, real Windows rendering, and moving into a shared folder on the owner's drive were not exercised.

## Patch release 1.0.5: Telegram waits no longer fail downloads — 22 October 2026

Telegram `FLOOD_PREMIUM_WAIT` responses (download throttling for non-Premium accounts) were not recognized because Telethon's `FloodPremiumWaitError` is not a `FloodWaitError` subclass; affected files were marked as failed immediately. Ordinary short `FLOOD_WAIT` responses also consumed the four per-file attempts, so parallel transfers could fail after a few one- to three-second waits. In a supplied queue copy, all 38 failed jobs had one of these two causes. Both waits are now honored for the requested duration without consuming connection retries; Activity labels the non-Premium limit separately, and Stop still cancels the wait. Network errors keep the existing limit of three retries. Previously failed jobs are retried by the next Start.

Python **3.14.8** on macOS: **203 tests**, 195 passed and eight skipped (Windows DPAPI and seven launcher tests without a portable PowerShell). New coverage alternates premium and ordinary waits beyond the retry budget, checks that waits do not extend the connection-retry limit, and checks the error text; all three fail against the 1.0.4 engine. The environment probe and compilation passed, the documentation preview was refreshed to v1.0.5, and launcher files are unchanged. Live Telegram throttling and real Windows behavior were not exercised.

## Patch release 1.0.4: queue backups before updates — 20 October 2026

The update flow now creates a standalone SQLite snapshot plus settings after an update package is ready and before requesting restart. It retains ten completed snapshots in Local AppData, verifies database integrity, flushes pending progress, and blocks installation if backup creation fails. Checks that find no update do not create a snapshot.

Python **3.14.8** on macOS: **201 tests**, 200 passed and one skipped (Windows DPAPI), with portable PowerShell enabled. Coverage includes committed WAL records, saved chat settings, pending progress flush, twelve backups reduced to ten without deleting unrelated files, disk-write failure, missing/corrupt source databases, cancellation during backup, staged-update cleanup, and backup completion before restart handoff. The final 1.0.4 candidate also passed the environment probe, compilation, whitespace checks, version-display checks, and the updater allowlist check for the new module. The documentation preview was refreshed to v1.0.4.

The backup implementation was also exercised on a disposable copy of the owner's supplied database: all **186,916 records** were preserved and the backup passed `integrity_check`. The original attachment's SHA-256 was unchanged. No user database, backup, or private settings are part of the repository. Real Windows update installation remains unverified.

## Patch release 1.0.3: sidebar visible at startup — 20 October 2026

The final 1.0.3 candidate completed a suite of **192 tests** on macOS with Python **3.14.8**: 191 passed and one skipped (Windows DPAPI). Portable PowerShell was enabled for launcher tests. The environment probe, bytecode compilation, and whitespace checks passed. Header and window-title version checks passed, and offscreen previews were inspected at 1150×640 and 500×340. Real Windows and live Telegram validation were unavailable.

The main window now starts at **1150×640**. The sidebar is immediately visible, with enough height for all four cards in the standard demo layout, while the queue retains approximately the width of the previous compact layout. The minimum size and compact-mode breakpoints are unchanged.

All **26 UI tests** passed on macOS with Python **3.14.8**. The startup check asserts the new dimensions, visible sidebar, hidden compact Settings button, and no vertical sidebar overflow. Existing tests cover shrinking to 500×340 and moving settings between the sidebar and its dialog. The 1150×640 offscreen preview was inspected and saved in the documentation. Real Windows rendering remains unverified.

## Patch release 1.0.2: responsive Settings — 20 October 2026

The final 1.0.2 candidate passed the Python 3.14 environment probe, bytecode compilation, whitespace checks, and the full 192-test suite below. Header and window-title version checks passed; main-window previews at 900×600 and 500×340 were inspected and the documentation preview was refreshed. The Windows workflow remains manual-only.

Removed the fixed-width content inside the Settings dialog. Cards now fill its width, reflow into two columns in wide windows, and retain aligned top edges and consistent spacing. Limit fields use a wrapping form. The dialog starts at 480×600, supports 320×340, and keeps Close outside the vertical scrolling area. The docked sidebar remains 240 pixels wide.

Python **3.14.8** on macOS: **192 tests**, 191 passed and one skipped (Windows DPAPI), with portable PowerShell enabled for launcher tests. New GUI coverage resizes repeatedly between narrow and wide layouts, checks card alignment and control bounds, ensures lower controls are reachable by scrolling, and verifies that chat/parallel/speed values survive resizing and moving the panel back to the sidebar. Offscreen previews were inspected at 480×600, 760×480, and 320×340, including the lower scrolled section. Real Windows rendering and live Telegram behavior were not tested for this change.

## Patch release 1.0.1 — 20 October 2026

This release includes compact 900×600 startup, consistent dark scrollbars, selected-chat archive folder access from **Open**, and the documented default patch-version policy. The header and window title both display **v1.0.1**.

Final candidate checks on macOS:

- Python **3.14.8**: **191 tests**, 190 passed and one skipped (Windows DPAPI).
- Python **3.15.0**: **191 tests**, 189 passed and two skipped (Windows DPAPI and the unavailable cryptg wheel).
- Launcher execution tests used portable PowerShell **7.6.7**. The Python 3.14 environment probe, bytecode compilation, and whitespace checks passed.
- Offscreen previews were inspected at **900×600** and **500×340**. Startup dimensions and displayed version were asserted; the documentation preview was refreshed for 1.0.1.
- Folder-opening tests cover the selected chat, switching chats, the base-folder fallback without a selection, and a missing chat folder. File-manager opening is mocked; no existing archives are moved.

These are local/offscreen and simulated-network checks. Real Windows rendering, Windows DPAPI, live Telegram downloads, WinRAR execution, and a complete installed Windows update were not verified. The Windows workflow remains manual-only.

## Compact layout on startup — 20 October 2026

The default window now opens at **900×600**, with Settings available from the header and the sidebar hidden immediately. The sidebar returns above 900 pixels wide when the window is at least 540 pixels high. The 500×340 minimum is unchanged.

All **25 UI tests** passed on macOS with Python **3.14.8**, including the startup dimensions, compact Settings access, eight complete queue rows at the new default height, and the existing resize/scroll/auxiliary-window checks. The 900×600 Qt preview was inspected and saved as the documentation preview. Real Windows rendering remains unverified.

## Consistent dark scrollbars — 20 October 2026

Both orientations now share nine-pixel dark tracks, rounded handles, hover/pressed feedback, and explicit page/corner styling. Arrow buttons are suppressed. The style inherits into the queue, active-transfer strip, Settings, Activity, and dropdowns.

All **25 UI tests** passed on macOS with Python **3.14.8**. Offscreen previews were inspected for the 500×340 window with both queue scroll axes and twenty active transfers, a shortened Settings dialog, a long Activity log, and a chat dropdown with 102 entries and a nonzero scroll range. Pointer simulation confirmed the handle uses the intended hover (`#59769b`) and pressed (`#78adff`) colors. No real Windows rendering was available.

## Smaller, responsive window — 20 October 2026

The default window is now 960×640 and the minimum is 500×340 (half the previous minimum width and height). Compact mode moves the existing sidebar controls into Settings on demand, provides horizontal scrolling for crowded active-transfer tiles and narrow queue columns, and abbreviates pagination buttons. Very short layouts hide secondary metrics and filename labels while preserving percentages and tooltips. Activity opens separately. Both auxiliary windows close with the main window.

The full suite ran **189 tests** on macOS with Python **3.14.8** and portable PowerShell: **188 passed**, Windows DPAPI skipped. After the final tile-width adjustment, all **23 UI tests** passed again. Release validation on Python **3.15.0** ran all **189 tests**: **187 passed**, Windows DPAPI and unavailable cryptg skipped. The Python 3.14 interpreter probe and syntax checks also passed. Tests exercise 500×340 in downloading/paused/idle states, access to every main action and pager, a visible queue row, scrolling to the twentieth file's 100%, opening/closing Settings and Activity, restoring the sidebar on enlargement, and closing the main window with both dialogs open. Readable percentages are also checked at 900, 960, and 1000 pixels wide.

The 960×640 default, 500×340 compact/paused layout with twenty transfers, and Settings dialog were visually inspected using real Qt offscreen rendering. The default documentation preview was refreshed. No Windows desktop or live Telegram account was used; Windows acceptance should check display scaling, resize breakpoints, scrollbar interaction, and focus/close behavior of the auxiliary windows.

## Simpler progress layout — 20 October 2026

Removed the redundant selected-file bar, its heading, and Follow active control. The overall bar and active-transfer tiles remain, with six-pixel gaps between tiles. Percentage text adapts to narrow tiles. The queue receives the space freed by the removed controls.

The final release candidate ran **188 tests per runtime** on macOS with portable PowerShell. Python **3.14.8** passed **187**, with Windows DPAPI skipped; Python **3.15.0** passed **186**, with Windows DPAPI and unavailable cryptg skipped. Both suites exited successfully. The Python 3.14 interpreter probe and syntax checks also passed. Obsolete pin/follow tests were replaced with checks that queue selection preserves active-file and overall progress, and that files outside the displayed page still update their tile. The tests verify that only the overall and active-file progress widgets remain, all twenty percentages fit, and neighboring tiles have the wider gaps. The default 1220×850 preview and the 1000×680 layout with twenty transfers were inspected visually; resizing back and forth preserved readable percentages. No real Windows or Telegram session was used.

## Live parallel download limit — 20 October 2026

The candidate ran **190 tests per runtime** on macOS with portable PowerShell enabled. Python **3.14.8** passed **189**, with Windows DPAPI skipped; Python **3.15.0** passed **188**, with Windows DPAPI and unavailable cryptg skipped. Both suites exited successfully.

A twelve-file controlled transfer test grows the pool from two to five, reduces it to two without cancellations, and verifies that slots refill only below the target. Rapid limit changes settle at the latest value; every archive is claimed once. Another fixture changes the limit while paused, confirms no extra downloads start until Resume, and proves increasing the limit after Finish current does not bypass the stop request. A fatal disk-error test checks that no further file is claimed and all active transfers are cleaned up. The real-file partial-transfer test now grows from one to three and lowers the limit before Pause/Stop, then reopens SQLite and resumes all saved partials.

Settings reload preserves the new limit. Offscreen UI checks cover live dropdown commands, active-count/limit labels, preserved per-file and aggregate progress, pause, and Finish current. Normal (1220×850) and minimum (1000×680) layouts were visually inspected, including more active files than the new limit and a paused operation with Activity expanded. The default preview was refreshed.

These are local/offscreen checks and simulated network transfers, not a Windows or live Telegram run. On Windows, verify raising/lowering the limit during real transfers, Pause/Resume, Finish current, immediate Stop/close and restart, and automatic WinRAR checks after downloads complete.

## Finish current downloads — 20 October 2026

The candidate ran **186 tests per runtime** on macOS with portable PowerShell enabled. Python **3.14.8** passed **185**, with Windows DPAPI skipped; Python **3.15.0** passed **184**, with Windows DPAPI and unavailable cryptg skipped. Both suites exited successfully. The Python 3.14 interpreter probe and syntax checks also passed.

A seven-file simulated queue proves that Finish current lets the three claimed files finish, waits for their archive checks, leaves four files untouched, and allows the next Start to finish the remaining queue. Repeating the command is harmless. A paused transfer fixture verifies that the command does not resume work, Resume finishes the two active files, and workers do not claim replacements. The existing real-file partial-transfer test now requests Finish current while paused before immediate Stop: all streams close, saved partials survive reopening SQLite, and downloads resume at their offsets.

Offscreen Qt tests verify the button command, disabled state after requesting it, download/pause/check status transitions, reset on idle, and its unavailability during a paused scan. The interface was visually inspected at 1220×850 and 1000×680, including the finishing state and a paused operation with Activity expanded. The documentation preview was refreshed.

These are local/offscreen tests with simulated Telegram streams and archive checks. Real Windows, live Telegram, and WinRAR were unavailable. On Windows, check Finish current during several parallel downloads, while paused, and while checks are pending; verify that Start continues the saved remainder and immediate Stop/close still preserve partial files.

## Active progress row and paginated queue filters — 20 October 2026

The final candidate ran **183 tests per runtime** on macOS with portable PowerShell enabled. Python **3.14.8** passed **182**, with Windows DPAPI skipped; Python **3.15.0** passed **181**, with Windows DPAPI and unavailable cryptg skipped. Both processes exited successfully. The interpreter probe and Python syntax checks also passed.

A real SQLite fixture with **100,005 archives** verifies that five unfinished files remain reachable beyond 100,000 completed entries, active downloads/checks sort first, downloaded history spans 500 pages, and the final All page is reachable. Another fixture traverses every page without omissions or duplicates, then completes the last-page files and checks clamping. Tests preserve account/chat isolation, pending progress overlays, completed/quarantined history, and totals.

Engine checks change filters/pages while an operation is paused and verify that it is neither canceled nor resumed. GUI checks cover all three filters, the Unfinished default, removal of completed rows, direct page entry with Enter, stale-response rejection, last-page clamping while the number field retains focus, and active progress independent of the displayed page. Twenty tiles fit at the minimum window width with complete percentages; pause retains values, live progress updates the matching tile, and chat switching clears the strip.

The real Qt demo was visually inspected at 1220×850 and 1000×680, including 2, 10, and 20 active transfers and the expanded Activity log while paused. The default preview was updated. The added progress row and page controls leave room for 16 full queue rows at the default size and 10 at the minimum size with Activity collapsed.

These are local/offscreen and simulated transfer results, not a Windows or live Telegram run. Windows acceptance should verify all percentages at the user's display scaling, page-number entry, switching filters during transfers, browsing a later page while the upper bars advance, and Pause/Stop/restart with an existing queue.

## Header version, dropdown arrows, and Settings — 20 October 2026

The final candidate ran **177 tests** on macOS with Python **3.14.8** and portable PowerShell enabled: **176 passed, 1 skipped** (Windows DPAPI), exit code 0. Interpreter and Python syntax checks passed. The header version, Lucide dropdown chevrons, and renamed Settings block were visually inspected at 1220×850 and 1000×680; the documentation screenshot was refreshed.

An offscreen interaction check clicked each of the three arrow hit areas, confirmed the list opened and closed with Escape, and checked that chat search remains editable. Disabled download-count controls retain a muted arrow. No Windows desktop or live Telegram account was used; native Windows rendering and interaction remain an acceptance check.

## Denser interface and informative queue — 20 October 2026

The full suite ran **177 tests** on macOS with Python **3.14.8**: **176 passed, 1 skipped** (Windows DPAPI), exit code 0. Portable PowerShell launcher tests were enabled. These results describe the local candidate; they do not establish publication.

Real Qt renders were inspected at 1220×850 and 1000×680, including the expanded Activity log at the smaller size. Compared with the previous committed interface at the same dimensions, the queue viewport now accommodates **20 instead of 9** full rows at the default size and **13 instead of 5** at the minimum size, with Activity collapsed. These are measured viewport capacities; demo data contains 12 archives. Actual Windows font rendering may differ.

GUI checks cover visible controls at the minimum size across downloading, paused, and idle states, Activity expansion, long account/folder/status text without forced window growth, live transferred-byte and row-bar updates, and restored/stopped queue progress. Filename restoration keeps its own temporary progress until the next idle queue snapshot restores the aggregate display. Existing selection/follow behavior, background verification, QR rendering, and worker shutdown tests remain passing. The documentation preview was refreshed.

No actual Windows desktop, live Telegram session, or real WinRAR was used. Windows acceptance should check control labels and queue readability at the user's display scaling, selecting/pinning files, opening Activity, Pause/Resume, and the relocated sidebar archive-check controls.

## Separate per-file progress bar — 20 October 2026

The full suite ran **175 tests per runtime** on macOS. Python **3.12.3** and **3.14.8** passed 174 with Windows DPAPI skipped; Python **3.15.0** passed 173 with Windows DPAPI and unavailable cryptg skipped. Portable PowerShell tests were enabled.

Qt tests distinguish the file's 62% progress from the queue's 38%, preserve a stable automatic target while other files report progress, follow the next active file after completion, and pin a selected row without affecting aggregate progress. They cover reordered queue snapshots, Pause, completed selections, returning to Follow active, clicking the same highlighted row again, active downloads outside the visible queue, completion after the active pool empties, and chat reset. A worker test verifies active-file events contain independent copies of file metadata and byte counts.

The real Qt interface was rendered and visually inspected at 1220×850 and 1000×680. The file's name, byte counts, percentage, progress bar, and Follow active control fit alongside the existing aggregate display. The default-size queue still shows at least nine full rows. `interface-preview.png` was refreshed. No real Telegram account or Windows desktop was used; Windows acceptance should check row selection, automatic following, pause/resume, and switching chats during parallel downloads.

## Shared download speed limit — 20 October 2026

The full suite ran **170 tests per runtime** on macOS. Python **3.12.3** and **3.14.8** passed 169 with Windows DPAPI skipped; Python **3.15.0** passed 168 with Windows DPAPI and unavailable cryptg skipped. Portable PowerShell tests were enabled. The real Qt interface was rendered at 1220×850 and 1000×680; the Speed limit dropdown fits beneath Parallel downloads without reducing the queue area.

New tests verify the seven presets and Unlimited default, persistence across engine restart, changes during active work and pause, and one limiter shared by all twenty workers. A timed aggregate-budget test requires twenty 4 KiB requests at 0.25 MB/s to spend the shared 0.3125-second budget rather than receiving twenty independent allowances. Other async checks cover live increase/decrease, switching to Unlimited, cancellation of the first waiter, pause/resume without accumulated credit, and reading no network chunks before allowance is granted. A canceled real-file transfer preserves its partial and later finishes with identical bytes. UI tests check all dropdown labels, live availability, selection commands, and restoring settings without signal loops.

No real Telegram throughput or Windows networking was measured. Windows acceptance: choose each limit while several large archives download, compare sustained combined throughput over at least a minute, change limits live, pause/resume, stop/restart, and verify the saved selection. Allow for 512 KiB chunk bursts, requests already in flight, network overhead, and Telegram's own limits; this is archive-request pacing rather than an operating-system traffic shaper.

## Automatically restore completed legacy filenames — 20 October 2026

The full suite ran **157 tests per runtime** on macOS. Python **3.12.3** and **3.14.8** passed 156 with Windows DPAPI skipped; Python **3.15.0** passed 155 with Windows DPAPI and unavailable cryptg skipped. Portable PowerShell tests were enabled. The new restoration view was rendered with real Qt offscreen and visually inspected: archive count, percentage, progress bar, Pause, and Stop are visible and consistent.

Real temporary-file tests cover original names beyond the old truncation limit, quarantine paths, disk/queue collisions, 105 archives fetched in two metadata batches, unchanged completion/check status, and passing restored paths to the archive checker without downloading again. Missing, changed, untracked, newly named, symbolic-link, deleted-message, and edited-document cases are preserved. A file replaced with same-size content and identical mtime while metadata is fetched is rejected by its changed filesystem identity.

Interruption tests reopen SQLite before the move, after the move but before the final metadata commit, and between POSIX hard-link creation and source removal. A database trigger verifies that updating the queue and removing the rename journal roll back together. Recovery rejects an unrelated same-size destination and keeps both files. Locked sources can be skipped and retried. Async tests cover automatic saved-chat refresh/selection, pause during metadata retrieval, and cancellation with later retry.

No real Windows filesystem, Telegram account, or WinRAR executable was used for this feature. Windows acceptance: update with an existing completed queue, log in to the same account, and confirm the remembered chat restores names automatically. Include a previously shortened name and a file in wrong_archives; confirm completion/check status stays intact. Test duplicates, locked files, Pause/Stop, close/reopen, and Check archives after restoration. Other chats are handled when selected. Untracked files and attachments that can no longer be verified against Telegram are intentionally left unchanged.

## Preserve original archive filenames — 20 October 2026

The full suite ran **137 tests per runtime** on macOS: Python **3.12.3** and **3.14.8** passed 136 with Windows DPAPI skipped; Python **3.15.0** passed 135 with Windows DPAPI and unavailable cryptg skipped. Portable PowerShell launcher tests were enabled. All three processes exited successfully.

New coverage checks exact preservation of case, Unicode, spaces, and names longer than the previous 85-character limit; collisions with queue reservations, files, directories, and partial downloads; reservation persistence across database reopen; and account-folder isolation. A real-file transfer simulation downloads twenty identically named archives concurrently, verifies every file's distinct bytes, and repeats the scan to check stable assignments. A same-size unrelated file introduced after scanning receives protection rather than being mistaken for a completed download.

Legacy migration tests persist an old partial/new final path pair, reopen the database, and finish from the existing byte offset under the full original Telegram name. Already completed legacy files remain unchanged. Other checks cover incomplete-file conflicts, durable-completion recovery, explicit failure for an over-limit name instead of silent truncation, and the same removable duplicate prefix inside wrong_archives.

Windows filesystem behavior and live Telegram downloads remain unverified here. On Windows, update with a partially downloaded old queue, finish it, and compare the final name with Telegram. Test duplicate ZIP/RAR names, uppercase extensions, a pre-existing unrelated file, Pause/Stop/restart, and quarantine collisions. Verify Total Commander previews prefix removal correctly before applying it to completed files in folders without resulting name conflicts.

## Automatic Python preparation and manual CI — 20 October 2026

The Windows entry point now selects standard Python 3.14 or downloads the official full CPython 3.14.8 x64 runtime ZIP into per-user application storage. The published SHA-256 was read from the [official Windows release manifest](https://www.python.org/ftp/python/3.14.8/windows-3.14.8.json). A real download was verified against `4873947a8afc037846b180312b83c744a4146a851cfd316a75c3125a4d8299da`; its 2,203 entries include python.exe, pythonw.exe, venv, ensurepip, and tkinter. The Windows binaries were inspected, not executed on macOS.

A portable PowerShell 7.6.7 runtime executed seven offline launcher tests: real parsing, hashing, extraction, publication, hash rejection, failed download cleanup, previous-runtime preservation, installed-runtime reuse, exclusive setup locking, and the actual bootstrap-to-PowerShell call with quoted paths/arguments. These tests replace Windows executable probes and network responses with fixtures. Additional Python tests cover automatic handoff from 3.15, preventing setup while the app is running, preservation of the old environment and queue fixtures, and reuse of a healthy environment.

The final full suite ran **126 tests per runtime**, on Python **3.12.3**, **3.14.8**, and **3.15.0** with the portable PowerShell tests enabled. Python 3.12/3.14 passed 125 tests and skipped Windows DPAPI; Python 3.15 passed 124 and skipped Windows DPAPI plus the unavailable cryptg wheel. Actual Windows PowerShell 5.1, the Windows Python runtime, and a real Telegram account still need acceptance testing.

Windows acceptance: on a machine without Python, run Start.bat and confirm the official runtime and dependencies are prepared without administrator rights. Repeat with an old Python 3.15 `.venv` and a saved Telegram queue: verify the new `.venv-py314` is used, the old environment is retained, and saved downloads resume. Repeat offline after successful setup to confirm no Python download is attempted. Test a failed initial download and retry, two simultaneous launches, and Check updates from an older version. Verify no system PATH/file associations change.

The Actions workflow is now **manual only** (`workflow_dispatch`). Pushes no longer schedule Windows validation, preventing the automatic failure-email cycle requested by the owner. Tests remain available locally and through manual Actions runs; account-wide notification settings and billing were not changed.

## Native cryptography and batched progress — 20 October 2026

The source passed the full **114-test** suite on macOS with Python **3.12.3** and **3.14.8** (113 passed, Windows DPAPI skipped), and **3.15.0** (112 passed, Windows DPAPI and unavailable cryptg wheel skipped). All processes exited with code 0. The Qt test for progress events without embedded summaries was also rerun successfully after updating that fixture.

cryptg 0.6.0 was installed from native wheels in the isolated 3.12 and 3.14 test environments. Tests confirm Telethon selects it and that its AES-IGE ciphertext and decrypted plaintext match the Python implementation. The launcher probe checks the pinned version, native import, backend selection, and encryption round trip. [cryptg package metadata](https://pypi.org/project/cryptg/0.6.0/#files) lists Windows x64 wheels through CPython 3.14 but no CPython 3.15 wheel. The conditional requirement preserves 3.15 without attempting a compiler build. This follows Telethon's [native acceleration guidance](https://docs.telethon.dev/en/stable/basic/installation.html#optional-dependencies).

Storage tests cover display overlays before commit, account isolation, decreasing offsets after alignment, one transaction for multiple files, immediate completion/stop checkpoints, flushing on close, rollback of an entire failed batch with pending data retained, and closing the database after a checkpoint error. An engine test emits 800 progress callbacks without any SQL writes or summary scans, then verifies the owned timer persists the final count in one transaction. Existing real-file pause/stop/reopen/resume tests still pass.

A controlled 10,000-item queue simulation with 20 active downloads and 20 progress rounds recorded **400 commits and 800 summary scans** for the old callback pattern versus **5 commits and 20 scans** for batching. Measured local bookkeeping time was 2.0648 s versus 0.0507 s. A separate 512 KiB AES-IGE comparison produced identical ciphertext in 0.008928 s with cryptg versus 0.652805 s with the Python-only reference. These are synthetic component measurements, not Telegram download rates or an end-to-end speed guarantee.

Actual Windows launch/dependency installation, real WinRAR, and live Telegram throughput remain unverified here. Existing 3.15 virtual environments are preserved; enabling cryptg there requires switching to 3.14 as described in the user guide. For Windows acceptance, update through Check updates, confirm Activity reports cryptg on 3.11–3.14, compare the same queue/concurrency settings, and exercise Pause/Stop/restart while multiple archives are partially downloaded.

## Parallel archive downloads — 19 October 2026

**107 tests per runtime: 106 passed, 1 skipped**, with exit code 0 on Python **3.15.0**, **3.14.8**, and **3.12.3** on macOS. The skip remains Windows DPAPI.

A 43-file simulated queue proves that 20 transfers overlap, the pool never exceeds 20, freeing one slot starts the next file while others remain blocked, and each file is claimed once. Aggregate progress and speed are checked. A separate test uses the real file transfer code with three synthetic streams: pausing holds in-flight chunks, Stop flushes and closes every stream, and reopening the SQLite store resumes every partial at the saved offset. Other checks cover independent file failure, saved and bounded concurrency settings, rejecting setting changes while busy, per-file UI progress, combined metrics, and pause status across rows. Existing archive-check, cancellation, and update tests remain passing.

The real Qt demo was rendered and visually inspected at 1220×850 and 1000×680. The parallel setting fits in the sidebar without reducing the queue area. Sample data illustrates multiple active files and a background archive check.

No live Telegram account, Windows desktop, or actual WinRAR was used. Real throughput with 20 Telegram transfers still needs user acceptance testing. The previously inspected Windows Actions run was blocked before any test started because GitHub reported an account billing lock; these local results do not establish Windows CI success.

For Windows acceptance, update the app, leave Parallel downloads at 20, and start a queue with more than 20 archives. Confirm multiple rows advance, freed slots refill, Pause/Resume affects all transfers, and Stop/restart resumes partial files. Confirm archive checks continue independently. If Telegram repeatedly requires a wait, stop and lower Parallel downloads.

## Compact interface and automatic updates — 19 October 2026

The update ran **102 tests per runtime: 101 passed, 1 skipped**, with exit code 0 on Python **3.15.0**, **3.14.8**, and **3.12.3** on macOS. The skip is the Windows-only DPAPI test. The compact interface was rendered and visually inspected; the default window shows approximately 11 queue rows with Activity collapsed, compared with roughly four previously.

Update tests cover ZIP preparation and installation, preservation of credentials/queue/download/venv fixture files, initial-version comparison, repeat checks, staged checksum changes, obsolete managed files, malformed ZIPs, Python syntax errors, unsafe/duplicate/symlink paths, disallowed redirects, download size limits, cancellation, and rollback after a simulated locked file. An interrupted-install journal is recovered on the next launcher check.

A real helper subprocess waits for a fixture parent to exit, installs source files, and starts a fixture bootstrap process. A real temporary Git checkout is fast-forwarded while retaining an untracked download. Mocked Git checks cover dirty-source refusal; process tests cover cancellation cleanup. Engine tests cover background preparation cancellation and the UI restart request. A subprocess regression test verifies Python 3.15 asyncio thread usage after importing the app and Qt.

After publication, the real public GitHub updater downloaded commit `f31ec95` without authentication, validated 27 application files, installed them into an isolated temporary ZIP installation while preserving a local settings fixture, and reported up to date on the next check. Native Windows update handoff, locked-file behavior, WinRAR, live Telegram login, and dependency changes during a real Windows restart still require Windows acceptance testing. No claim is made that Windows CI has passed.

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
