# Development

## Layout

| File | Responsibility |
| --- | --- |
| `Start.bat` | The single Windows launch entry point |
| `bootstrap.py` | Standard-library environment check, setup window, dependency installation, app process launch |
| `tgzip/__main__.py` | Application lifecycle and single-instance lock |
| `tgzip/ui.py` | PySide6 window, visual progress, credential and QR dialogs |
| `tgzip/engine.py` | Dedicated asyncio worker, Telethon login, chat selection, scan/download orchestration |
| `tgzip/transfer.py` | Chunked streaming, pause gate, cancellation, partial-file recovery, ZIP recognition, finalization |
| `tgzip/storage.py` | SQLite queue/checkpoints, atomic settings writes, filename filtering |
| `tgzip/security.py` | Windows DPAPI vault and read-only legacy-session import |
| `tests/` | Core, async orchestration, security and offscreen GUI tests |

## Concurrency and persistence

Qt runs on the main thread. A non-daemon worker owns its asyncio event loop, Telegram client, and SQLite connection. The GUI schedules commands with `run_coroutine_threadsafe`; worker events return through a Qt signal. Widgets are never changed from the worker thread.

Only one foreground operation runs at a time. Stop and chat switching cancel the operation and await its cleanup. Shutdown does not terminate the thread: it waits for file flushing, encrypted-session persistence, disconnection, and database closure. Password responses and pause/resume commands can reach an active operation without waiting for it to finish.

Queue identity is `(account_id, chat_id, message_id)`. Document identity and expected size are checked before resuming. Paths also include the document ID. SQLite transactions atomically add a discovered file and advance the scan cursor. WAL and FULL synchronous mode are used for committed metadata. The partial file is the authority for resume offsets; metadata progress is for display. Session and preferences use temporary-file replacement.

Downloads request 512 KB chunks. Resuming truncates only an incomplete trailing block. Network interruptions and expired references are retried with a fresh message fetch; Telegram FloodWait is honored. Archive recognition uses `zipfile.is_zipfile`, without extraction. Completed files are finalized in the same directory without intentionally overwriting an existing file.

The authorization state uses Telethon `StringSession` in memory. Only a DPAPI-encrypted JSON payload is persisted. There is no plaintext credential fallback on unsupported platforms. This is not a secure enclave: secrets necessarily exist in process memory while the application runs.

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

The workflow runs on pushes to `main` and can also be started manually. It installs the pinned requirements on Windows x64 with Python 3.11–3.15; runs syntax and unittest checks, including actual DPAPI round-trip/tamper tests; and uploads a rendered demo screenshot. It does not use Telegram credentials or exercise real-account networking.

## API references

- [Telethon client reference](https://docs.telethon.dev/en/stable/modules/client.html): dialog enumeration, history iteration, QR login, and chunked download APIs.
- [Telethon QRLogin](https://docs.telethon.dev/en/stable/modules/custom.html#telethon.tl.custom.qrlogin.QRLogin): code expiry and recreation.
- [Telethon session documentation](https://docs.telethon.dev/en/stable/concepts/sessions.html): session persistence and authorization-key sensitivity.
- [Microsoft CryptProtectData](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata): user-scoped DPAPI protection.
- [Qt for Python](https://doc.qt.io/qtforpython-6/): desktop widgets and signals.

The earlier conversation's downloadable Python attachments were not available through chat retrieval. No claim is made that their source code or queue format was preserved. Compatibility is provided for their described API configuration and standard Telethon SQLite authorization session.
