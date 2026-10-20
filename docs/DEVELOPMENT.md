# Development

## Release workflow

Follow [Release procedure](RELEASING.md) and the repository's [agent instructions](../AGENTS.md). Completing a feature does not authorize publication: first ask whether more changes are planned, then obtain release approval. An explicit release or push command authorizes the full checked publication process. Every push to `main` becomes available through **Check updates**.

## Layout

| File | Responsibility |
| --- | --- |
| `Start.bat` | The single Windows launch entry point |
| `updater.py` | Public GitHub checks, staging, ZIP rollback, Git fast-forward, exit-wait helper and restart |
| `bootstrap.py` | Standard-library environment check, setup window, dependency installation, app process launch |
| `tgzip/__main__.py` | Application lifecycle and single-instance lock |
| `tgzip/ui.py` | PySide6 window, visual progress, credential and QR dialogs |
| `tgzip/engine.py` | Dedicated asyncio worker, Telethon login, chat selection, scan/download orchestration |
| `tgzip/transfer.py` | Chunked streaming, pause gate, cancellation, partial-file recovery, ZIP/RAR finalization |
| `tgzip/archive_check.py` | WinRAR subprocess lifetime, password-free test policy, non-overwriting quarantine |
| `tgzip/storage.py` | SQLite queue/checkpoints, atomic settings writes, filename filtering |
| `tgzip/filename_restore.py` | Journaled restoration of completed legacy filenames, including quarantine |
| `tgzip/security.py` | Windows DPAPI vault and read-only legacy-session import |
| `tests/` | Core, async orchestration, security and offscreen GUI tests |

## Concurrency and persistence

`finish_current` sets an operation-scoped flag without yielding on the worker event loop. The scheduler checks it before claiming any new job and wakes immediately when the command arrives. Already claimed transfers continue normally, then the existing verifier drains pending checks. The command does not open the pause gate or cancel tasks. Operation cleanup and each new launch reset the flag; immediate Stop, chat switching, and close still cancel the task tree. The UI remembers the underlying operation while paused so this control is unavailable during scans or manual checks.

Qt runs on the main thread. A non-daemon worker owns its asyncio event loop, Telegram client, and SQLite connection. The GUI schedules commands with `run_coroutine_threadsafe`; worker events return through a Qt signal. Widgets are never changed from the worker thread.

Only one user operation runs at a time. A download operation owns two asyncio TaskGroup tasks: a bounded download worker pool and one sequential archive-check consumer. An event-driven scheduler uses a nested TaskGroup with at most 20 active transfer tasks (default limit 20); it never creates tasks for the whole saved queue. It advances a shared message cursor and reserves each slot without yielding between selection and claim on the single event loop. The scheduler wakes on completion, a live limit change, Resume, or Finish current. Lower limits leave existing tasks intact and suppress replacement until active count is below the target. Increasing the limit creates additional tasks immediately unless paused, finishing, or the queue is exhausted. A fatal error or cancellation stops claims before TaskGroup cleanup; completed tasks are removed by TaskGroup, keeping memory bounded. Each queue item is attempted once per pass; an individual transfer failure does not keep reclaiming the same item. The scheduler refills available capacity after a task completes. Per-file progress is persisted independently; the UI receives individual row progress plus aggregate speed and queue totals. The SQLite pending verification rows are the durable check queue; an asyncio Event wakes the consumer after a file is finalized. A partial index selects pending checks without rescanning all completed history. The producer never awaits an individual WinRAR test. At the end of downloads the operation drains remaining checks, then becomes idle. Start also consumes checks left pending by an earlier stop or restart. Stop and chat switching cancel the entire task tree and await every transfer and checker cleanup. A fatal task error cancels its sibling and is reported using the underlying cause. One shared pause gate applies to both tasks. No detached verification tasks survive operation cleanup. Shutdown does not terminate the thread: it waits for file flushing, encrypted-session persistence, disconnection, and database closure. Password responses and pause/resume commands can reach an active operation without waiting for it to finish.

Queue identity is `(account_id, chat_id, message_id)`. Document identity and expected size are checked before resuming; identifiers are metadata, not filename prefixes. SQLite transactions atomically add a discovered file and advance the scan cursor. WAL and FULL synchronous mode are used for committed metadata. The partial file is the authority for resume offsets; metadata progress is for display. Session and preferences use temporary-file replacement.

Aggregate transfer events include independent copies of each active file's message ID, name, path, size, and current bytes. Queue snapshots preserve selection by message ID rather than row position and block selection signals while rebuilding the table. The UI retains only the current 200-row page and active pool, so it can show active files on any page without copying the entire database. Chat/account changes clear the active tiles; row selection does not control transfer progress.

Each active transfer owns one compact `ActiveTransfer` tile in a horizontal row. Existing tiles keep their order while active; new transfers append, and finished slots remove their widgets. Direct byte events update only the affected tile; aggregate events reconcile pool membership. The strip remains independent of pagination and filtering and retains values on Pause. Tiles have six-pixel gaps; percentage text adapts to the tile width so 100% remains readable with twenty files at minimum window width. The redundant selected-file bar and Follow active control have been removed.

`Store.queue_page` filters in SQLite before applying LIMIT/OFFSET, computes the matching count, and clamps the page after membership changes. `queue_display_order` indexes the account/chat and ordering expression: downloading, completed-and-checking, unfinished, then remaining completed history; message ID breaks ties. Filtering never changes stored job state or worker scheduling. The three view modes and page live in the engine for the current session, defaulting to Unfinished/page 1; chat selection resets the page. Request IDs plus account/chat tags reject delayed snapshots after a navigation change. A dirty flag refreshes changed download membership on the existing progress timer instead of scanning the queue for each byte callback.

The compact queue paints per-row progress through `ProgressDelegate`, avoiding a widget for every entry. Progress events update both the transferred byte counts and the delegate's numeric value. The single aggregate bar also shows restored/stopped queue progress; rename operations retain their own temporary progress display. Long names and footer messages use `ElidedLabel` with full tooltips so they cannot force the window wider or consume extra queue height. Archive-check controls live in the sidebar, and Activity toggles from the queue header.

`ArrowComboBox` draws the Lucide chevron-down geometry in Qt's native dropdown hit area. Padding reserves space for the icon, disabled controls use a muted stroke, and native popup/search/keyboard handling remains intact. Drawing the small vector directly avoids introducing an external asset that older updaters would omit. Attribution is in `THIRD_PARTY_NOTICES.md`.

Original names are sanitized only for Windows-invalid characters and reserved device names. Case and length are preserved. A shared FilenameAllocator reserves case-folded paths against the entire saved queue and directory entries, including `.part` files; only collisions add `__dup_<number>__`. Each scan/download operation has its own allocator, and workers reserve destinations without yielding. Paths are checked again before the first transfer, so a same-size unrelated file added after scanning is not mistaken for a completed download. Final bytes are checkpointed before finalization; new-style paths require this checkpoint to recover an existing final file.

Additive `naming_version` and `partial_path` columns migrate existing queues without resetting scan cursors. Version 0 denotes legacy/imported paths, 1 denotes a new scanned reservation, and 2 denotes a prepared transfer or restored completed name. An unfinished legacy numbered path is reassigned using fresh Telegram metadata; the old `.part` location is stored atomically alongside the new target. Transfer resumes the old partial and finalizes directly to the new destination in the same directory. No intermediate filesystem rename is necessary during partial-file migration.

Completed legacy files are restored on connection/refresh, chat selection, and before scan/download/manual-check operations, serialized through the existing worker operation. Only exact `<message>_<document>_<saved name>` queue paths qualify. Metadata is fetched in batches of 100 IDs and checked against document ID/size. Missing, changed, symlink, or inaccessible files are skipped. Quarantined archives use their recorded physical quarantine path and stay quarantined. Other imported formats and untracked files remain untouched.

The `filename_moves` table stores each source, target, original name, quarantine flag, and filesystem fingerprint (device, inode, size, nanosecond mtime) before moving. The fingerprint is checked again after Telegram requests and before the move. Moving never replaces a destination. Updating the job's path/name/version and deleting the move record share one SQLite transaction. Recovery reconciles an existing final file, or two hard links to the same file after a POSIX link/unlink interruption; different files or changed fingerprints stop recovery without deleting either. A failed move that demonstrably left the source intact and no destination cancels its plan so locked files can be skipped and retried later. Existing ambiguous plans must be resolved before checking archives in that chat.

Downloads request 512 KB chunks. Resuming truncates only an incomplete trailing block. Network interruptions and expired references are retried with a fresh message fetch; Telegram FloodWait is honored. Transfer completion means the expected byte count was received. Format/integrity verification is a separate WinRAR operation and is never inferred from the filename or download byte count. Completed files are finalized in the same directory without intentionally overwriting an existing file.

Every worker shares one `DownloadLimiter`. Before requesting the next chunk, it acquires a byte allowance from a token bucket capped at one 512 KiB chunk. A fair asyncio lock serializes budget claims, never the network requests themselves. The `speed_limit` setting is MB/s (1,048,576 bytes per unit); zero is Unlimited, and only the dropdown presets are accepted. Rate changes reset credit and signal waiting transfers to recalculate immediately. Pause/resume and cancellation reset credit too, preventing accumulated pause credit from causing a burst. Bandwidth waits sit outside the network-read timeout and use cancellable asyncio waits. Already issued requests and network overhead are not governed retroactively.

Verification state is separate from download state. A `done` download can be `pending`, `checking`, `ok`, `error`, `quarantining`, or `quarantined`. Canceling verification keeps the download complete. Background verification events update a separate UI indicator and queue status; they never replace the aggregate download display, transfer speed, or row progress. Manual checks can still use the main card when no transfer is active. Missing WinRAR leaves it pending. Operational errors never qualify for quarantine; all three attempts must return 2, 3, or 11, and any success wins. Known `.partNN.rar` volumes are excluded from automatic checks because missing sibling volumes can produce misleading fatal errors.

WinRAR runs through `asyncio.create_subprocess_exec`, without a shell, with output suppressed. Pause gates both attempts and result handling; Stop/close terminate and await the owned process, escalating to kill if needed. Each attempt has a 15-minute timeout. Files are tested without extraction or password prompts, using the command arguments from the supplied BAT. The intended quarantine path is committed before the synchronous, non-overwriting move; a subsequent snapshot reconciles a completed move whose final database commit was interrupted.

Schema migration adds verification columns to existing queues and resets ZIP-only scan checkpoints exactly once (`user_version=1`). Existing jobs are retained; INSERT OR IGNORE prevents historical rescans from replacing completed ZIPs. Filename migration is described above. Interrupted checks become pending on reopen. The legacy module and Local AppData folder names remain unchanged.

The authorization state uses Telethon `StringSession` in memory. Only a DPAPI-encrypted JSON payload is persisted. There is no plaintext credential fallback on unsupported platforms. This is not a secure enclave: secrets necessarily exist in process memory while the application runs.

## Updating and restarting

The header and window title display `tgzip.__version__`, defined in `tgzip/__init__.py`. This display version is separate from the Git commit SHA used for update detection. Every authorized release increments the patch component by default; minor and major increments require an explicit owner request. Prepare the bump once per release, not once per feature or retry. See [Release procedure](RELEASING.md#version-policy).

The update command runs standard-library network and staging work in one explicit thread pool. Cancellation signals that worker and awaits cleanup before the engine can close. Package initialization eagerly resolves ThreadPoolExecutor before Qt import hooks load; a subprocess regression check covers a Python 3.15 lazy-import incompatibility observed during testing.

Before entering the worker, the engine flushes pending queue progress and captures its current settings. After preparation finds an update, `queue_backup.backup_before_update` opens a separate read-only SQLite connection in that worker and uses the online backup API, including committed WAL contents. The copied database passes `integrity_check`, uses DELETE journaling for a standalone file, and is flushed along with the settings copy before publishing its temporary directory. Only then may `update_ready` trigger shutdown. Failure or cancellation removes the update stage and blocks installation. Retention keeps ten completed snapshots under the app data directory's `backups` folder; unrelated entries and incomplete snapshots from interrupted processes are not pruned. No credentials or downloaded archives are copied. The new module is covered by the existing updater's `tgzip/*.py` allowlist.

The public commits API identifies a specific main SHA. ZIP installs fetch that SHA's archive with HTTPS redirects restricted to GitHub hosts, bounded compressed/uncompressed sizes, a managed-path allowlist, duplicate/symlink/traversal checks, and Python syntax validation. SHA-256 records detect local staging changes before replacement. Initial unversioned ZIP installs compare files; subsequent checks use the saved commit. Git checkouts instead fetch the commit and only permit a clean main fast-forward.

The UI's update-ready event uses the normal close path. After the worker has stopped, the entry point launches a copied standard-library helper, records an in-progress marker while still holding the app lock, and exits. The helper waits for that PID, obtains the setup lock, applies the update, and restarts through bootstrap. ZIP installation journals the original files before replacement; rollback restores those files on write failure or the next launcher after interruption. Source replacement and dependency setup are separate steps; successful source installation is not rolled back for a later pip failure. Backups remain available under .updates.

## Responsive window

The main window opens at 1150×640 with a 500×340 minimum. `apply_window_layout` responds to width and height breakpoints. A scrollable sidebar is moved into a non-modal Settings dialog on demand in compact mode and returned to the main layout when that dialog closes; this reuses live controls and avoids duplicate state or command connections. Activity uses a separate dialog at all sizes. Closing the main window closes both dialogs before shutdown.

`SettingsPanel` owns the four existing cards in a responsive grid: one column below 640 pixels of content width, two at or above it. The Settings dialog releases the scroll area's fixed sidebar width and uses 12-pixel outer margins with 10-pixel gaps; returning to the main window restores the 240-pixel sidebar. The panel and controls are reparented, never recreated. Download limits use a wrapping form layout. The footer stays outside the scroll area so Close remains accessible.

The active-transfer strip is hosted in a horizontal scroll area: tiles retain enough width for readable percentages, with scrolling whenever the available row width is insufficient. Short windows hide filename labels and secondary metrics. Narrow queue views use horizontal scrolling to preserve all columns and compact pagination labels. No window-layout change modifies download state, filters, or settings.

Horizontal and vertical scrollbars share the global Qt stylesheet: nine-pixel dark tracks, rounded handles with a 24-pixel minimum length, subdued idle color, brighter hover and pressed states, hidden arrow buttons, and matching page/corner backgrounds. The same style inherits into Settings, Activity, and dropdown lists. Preserve this treatment for new scrollable views and inspect both axes in compact mode.

## Run tests

From a terminal in the project directory:

```powershell
py -3.15 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:QT_QPA_PLATFORM = "offscreen"
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

On macOS/Linux, create a virtual environment with Python 3.11–3.15, install the same requirements, and run the same unittest command using that environment's Python. The real Windows DPAPI test is skipped there; the non-Windows refusal test is skipped on Windows.

The GUI dependencies are pinned to PySide6 Essentials / Shiboken6 6.12.0, whose package metadata supports standard CPython through 3.15. The launcher's `--check-python` probe validates version, architecture, release stability and build type without installing packages or opening a window. When checking an existing environment, it also validates that interpreter and imports the pinned dependencies in a subprocess.

## Preview without an account

```powershell
.venv\Scripts\python.exe -m tgzip --demo
```

Or pass `--demo` through the same launcher: `Start.bat --demo`. Demo mode uses clearly labeled synthetic data and makes no Telegram connection. It does not write an application queue or credential vault. It is a visual preview, not a simulated download engine.

For a screenshot in CI:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.venv\Scripts\python.exe -m tgzip --demo --screenshot preview.png
```

## Windows CI

The workflow runs only through **Actions → Windows validation → Run workflow**. Its push trigger was removed at the owner’s request to stop emails from automatically failing runs. Manual runs may still notify according to GitHub account preferences. It installs the pinned requirements on Windows x64 with Python 3.11–3.15; runs syntax and unittest checks, including actual DPAPI round-trip/tamper tests; and uploads a rendered demo screenshot. It does not use Telegram credentials or exercise real-account networking.

## API references

- [Telethon client reference](https://docs.telethon.dev/en/stable/modules/client.html): dialog enumeration, history iteration, QR login, and chunked download APIs.
- [Telethon QRLogin](https://docs.telethon.dev/en/stable/modules/custom.html#telethon.tl.custom.qrlogin.QRLogin): code expiry and recreation.
- [Telethon session documentation](https://docs.telethon.dev/en/stable/concepts/sessions.html): session persistence and authorization-key sensitivity.
- [Microsoft CryptProtectData](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata): user-scoped DPAPI protection.
- [WinRAR exit codes](https://winrar.es/soporte/manual/HELPExitCodes): official distributor manual for success, archive errors, wrong passwords, and operational failures.
- [GitHub commit API](https://docs.github.com/en/rest/commits/commits#get-a-commit) and [repository archive API](https://docs.github.com/en/rest/repos/contents#download-a-repository-archive-zip): public commit lookup and pinned ZIP downloads.
- [Qt for Python](https://doc.qt.io/qtforpython-6/): desktop widgets and signals.

The earlier conversation's downloadable Python attachments were not available through chat retrieval. No claim is made that their source code or queue format was preserved. Compatibility is provided for their described API configuration and standard Telethon SQLite authorization session.

## Progress batching and native cryptography

`Store.stage_progress` keeps the last byte count and the persisted baseline per active job. `jobs` and `summary` overlay this small buffer, including negative corrections after partial-block truncation. A task owned by the download TaskGroup reports aggregate metrics every 250 ms and flushes every second in one SQLite transaction. Per-file callbacks emit row progress without querying totals or committing. Non-progress updates merge pending bytes into the immediate state transaction; explicit completion bytes override older buffered progress. Pending entries are removed only after successful commits. Pause, pool teardown, and Store.close flush remaining progress. SQLite retains WAL/FULL durability for committed transactions, and resume still uses partial-file size.

The pinned manifest includes cryptg 0.6.0 only for Python below 3.15 because its published wheels stop at CPython 3.14. The stdlib launcher recognizes this single explicit manifest marker and fails on unknown markers; pip evaluates the same marker during installation. cryptg installation is binary-only. The environment probe verifies the installed version, Telethon backend selection, and a native encryption round trip. On Python 3.15, existing Telethon OpenSSL/Python fallback behavior remains available. Start.bat now prepares a dedicated 3.14 environment automatically and retains the old environment as a fallback copy.

## Automatic Windows runtime

Start.bat contains both the small batch entry point and a PowerShell 5.1-compatible script after `# POWERSHELL_BEGIN`. Keeping the runtime bootstrap inside an already-managed file lets older self-updaters deliver it without needing a new root-file allowlist. It probes standard 64-bit Python 3.14 with Tcl/Tk, venv, and ensurepip, preferring the existing environment/base interpreter. An unavailable runtime is replaced by the full (not embeddable) ZIP from the official [Python 3.14.8 Windows manifest](https://www.python.org/ftp/python/3.14.8/windows-3.14.8.json), verified against its pinned SHA-256 before extraction or execution. A process-owned exclusive file handle serializes preparation. Work takes place in a unique staging directory and is moved into the runtime directory only after a successful probe. Existing invalid runtimes are retained under unique previous names.

Windows bootstrap.py delegates to that same script before dependency setup, including when called by an older updater running Python 3.15. A one-hop environment marker prevents recursion; it is removed before launching the application. The environment uses a distinct `.venv-py314` folder, so it never overwrites the old running updater's `.venv`. Failed managed environments are preserved before rebuilding. App.lock PID checks avoid setup while an application instance is already running; the existing Qt lock remains authoritative at actual app startup. Python setup and source update locks are still separate. The official ZIP includes its LICENSE.txt; no installer, registry, PATH, or file-association changes are required.

PowerShell execution tests use fixture archives and fake runtime probes, with real hashing, extraction, file locks, staging, and failure cleanup. Set `TG_TEST_POWERSHELL` to a portable PowerShell executable on non-Windows hosts to run them. They also run with Windows PowerShell when available. They do not constitute validation of a real Windows Python launch.
