"""Password-free WinRAR testing and conservative, non-overwriting quarantine."""
import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
from .storage import FilenameAllocator

BAD_CODES = {2, 3, 11}


@dataclass(frozen=True)
class CheckResult:
    status: str
    detail: str
    code: int | None = None


def find_winrar(configured=''):
    if os.name != 'nt':
        return None
    if configured:
        candidates = [Path(configured)]
    else:
        candidates = [Path(os.environ[key]) / 'WinRAR' / 'WinRAR.exe'
                      for key in ('ProgramW6432', 'ProgramFiles', 'ProgramFiles(x86)') if os.environ.get(key)]
        on_path = shutil.which('WinRAR.exe')
        if on_path:
            candidates.append(Path(on_path))
    return next((path.resolve() for path in candidates
                 if path.name.lower() == 'winrar.exe' and path.is_file()), None)


async def _stop_process(process):
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), 5)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            return
        await asyncio.wait_for(process.wait(), 5)


async def check_archive(path, executable, gate, report, *, retry_delay=3, timeout=900):
    """Mirror check_archives.bat: success wins; quarantine needs 3 bad codes.

    Codes other than 2, 3, or 11 veto quarantine, even if another attempt fails
    with a bad code. No password prompts, extraction, shell, or password guessing.
    """
    codes = []
    for attempt in range(1, 4):
        await gate.wait()
        report(f'Checking archive without a password: attempt {attempt}/3…')
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                str(executable), 't', '-p-', '-ibck', '-inul', str(Path(path).resolve()),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            code = await asyncio.wait_for(process.wait(), timeout)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            return CheckResult('error', 'WinRAR check timed out. The archive was not moved.')
        except OSError as exc:
            return CheckResult('error', f'Cannot run WinRAR ({type(exc).__name__}). The archive was not moved.')
        finally:
            if process is not None:
                await _stop_process(process)
        await gate.wait()
        if code == 0:
            return CheckResult('ok', 'Verified without a password.', code)
        codes.append(code)
        if attempt < 3:
            await asyncio.sleep(retry_delay)
    if all(code in BAD_CODES for code in codes):
        reason = 'Password required or incorrect' if codes[-1] == 11 else 'Damaged or invalid archive'
        return CheckResult('bad', f'{reason}; confirmed by 3 checks (WinRAR codes: {codes}).', codes[-1])
    return CheckResult('error', f'WinRAR reported an operational or inconclusive error (codes: {codes}). The archive was not moved.', codes[-1])


def quarantine_destination(source):
    source = Path(source)
    folder = source.parent / 'wrong_archives'
    folder.mkdir(parents=True, exist_ok=True)
    return FilenameAllocator([]).allocate(folder, source.name)


def move_to_quarantine(source, destination):
    """Move within the same filesystem, without replacing existing files."""
    source, destination = Path(source), Path(destination)
    if source.is_symlink() or destination.is_symlink():
        raise OSError('Symbolic links are not supported for quarantine.')
    if os.name == 'nt':
        os.rename(source, destination)  # Fails if destination exists on Windows.
    else:
        os.link(source, destination)  # Fails if destination exists on POSIX.
        source.unlink()
