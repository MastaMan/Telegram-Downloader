"""Public GitHub updates; standard library only, including the restart helper."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

REPOSITORY = 'MastaMan/Telegram-Downloader'
API = f'https://api.github.com/repos/{REPOSITORY}'
GIT_URL = f'https://github.com/{REPOSITORY}.git'
STAMP = '.installed-update.json'
MARKER = '.update-pending.json'
MAX_DOWNLOAD = 32 * 1024 * 1024
MAX_EXPANDED = 96 * 1024 * 1024
ROOT_FILES = {'Start.bat', 'bootstrap.py', 'updater.py', 'requirements.txt', 'README.md',
              'THIRD_PARTY_NOTICES.md', 'LICENSE', '.gitignore', '.gitattributes'}
REQUIRED = {'Start.bat', 'bootstrap.py', 'updater.py', 'requirements.txt', 'tgzip/__main__.py', 'tgzip/engine.py', 'tgzip/ui.py'}
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


class UpdateError(Exception):
    pass


class UpdateCancelled(UpdateError):
    pass


def cancelled(cancel):
    if cancel.is_set():
        raise UpdateCancelled('Update cancelled. Application files were not changed.')


def atomic_json(path, value):
    path = Path(path)
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def managed(name):
    path = PurePosixPath(name)
    if not name or '\\' in name or ':' in name or path.is_absolute() or any(part in {'..', '.'} for part in name.split('/')):
        return False
    return name in ROOT_FILES or (len(path.parts) > 1 and (
        path.parts[0] in {'tgzip', 'tests'} and path.suffix == '.py' or
        path.parts[0] == 'docs' and path.suffix.lower() in {'.md', '.png'} or
        path.parts[0] == '.github' and path.suffix in {'.yml', '.yaml'}))


def safe_target(root, name):
    if name != STAMP and not managed(name):
        raise UpdateError('The update contains an unsupported application path.')
    root = Path(root).resolve()
    target = root / name
    for item in (target, *target.parents):
        if item == root:
            break
        if item.is_symlink():
            raise UpdateError('Cannot update through symbolic links. Use a regular installation folder.')
    if not target.resolve().is_relative_to(root):
        raise UpdateError('The update path is outside the application folder.')
    if target.exists() and not target.is_file():
        raise UpdateError('An application file conflicts with a local folder.')
    return target


class GitHubRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urllib.parse.urlparse(newurl)
        if url.scheme != 'https' or url.hostname not in {'api.github.com', 'github.com', 'codeload.github.com'}:
            raise UpdateError('GitHub returned an unexpected download location.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, target, limit, cancel, report):
    request = urllib.request.Request(url, headers={'User-Agent': 'Telegram-Archive-Desktop-Updater',
                                                  'Accept': 'application/vnd.github+json'})
    try:
        with urllib.request.build_opener(GitHubRedirect()).open(request, timeout=15) as response, Path(target).open('wb') as output:
            total = 0
            while True:
                cancelled(cancel)
                block = response.read(128 * 1024)
                if not block:
                    break
                total += len(block)
                if total > limit:
                    raise UpdateError('The update download exceeds the supported size.')
                output.write(block)
                report(f'Downloading update · {total / 1024**2:.1f} MB')
        cancelled(cancel)
    except urllib.error.HTTPError as exc:
        if exc.code in {403, 429}:
            raise UpdateError('GitHub request limit reached. Try Check updates later.') from exc
        raise UpdateError(f'GitHub could not provide the update (HTTP {exc.code}). Try again later.') from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise UpdateError('Cannot reach GitHub. Check your internet connection and try again.') from exc


def run_git(root, args, cancel=None, *, allow_failure=False):
    cancel = cancel or threading.Event()
    executable = shutil.which('git')
    if not executable:
        raise UpdateError('This Git installation needs Git on PATH to update. ZIP installations do not require Git.')
    environment = dict(os.environ, GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='Never')
    process = subprocess.Popen([executable, *args], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=FLAGS, env=environment, start_new_session=os.name != 'nt')
    deadline = time.monotonic() + 90
    try:
        while True:
            cancelled(cancel)
            if time.monotonic() > deadline:
                raise UpdateError('Git update timed out. Try again later.')
            try:
                out, _ = process.communicate(timeout=.2)
                break
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process.poll() is None:
            # Git fetch owns a remote-HTTPS child. Stop the process tree too,
            # otherwise cancellation could leave an inherited pipe open.
            if os.name == 'nt':
                taskkill = str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32' / 'taskkill.exe')
                try:
                    subprocess.run([taskkill, '/PID', str(process.pid), '/T', '/F'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, creationflags=FLAGS)
                except (OSError, subprocess.TimeoutExpired):
                    process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.stdout.close()
                process.stderr.close()
                process.wait(timeout=5)
    if process.returncode and not allow_failure:
        raise UpdateError('Git could not update this checkout. Check access, local file conflicts, and repository history.')
    return process.returncode, out.decode('utf-8', errors='replace').strip()


def clean_git(root, cancel=None):
    if run_git(root, ['branch', '--show-current'], cancel)[1] != 'main':
        raise UpdateError('Automatic Git updates require the main branch.')
    if run_git(root, ['status', '--porcelain', '--untracked-files=no'], cancel)[1]:
        raise UpdateError('Local source changes were found. Commit or preserve them before updating; nothing was overwritten.')


def unpack(archive, destination, cancel):
    files, folded, roots, total = {}, set(), set(), 0
    with zipfile.ZipFile(archive) as package:
        if len(package.infolist()) > 2000:
            raise UpdateError('The update archive contains too many files.')
        for item in package.infolist():
            cancelled(cancel)
            raw = item.filename
            if '\\' in raw or ':' in raw or raw.startswith('/') or '..' in raw.split('/'):
                raise UpdateError('The update archive contains an unsafe path.')
            parts = PurePosixPath(raw).parts
            if not parts:
                continue
            roots.add(parts[0])
            if len(roots) != 1:
                raise UpdateError('The update archive has an unexpected layout.')
            if stat.S_ISLNK(item.external_attr >> 16):
                raise UpdateError('Symbolic links are not supported in updates.')
            if item.is_dir() or len(parts) < 2:
                continue
            name = '/'.join(parts[1:])
            total += item.file_size
            if total > MAX_EXPANDED:
                raise UpdateError('The expanded update is too large.')
            if not managed(name):
                continue
            if name.casefold() in folded:
                raise UpdateError('The update contains duplicate filenames.')
            folded.add(name.casefold())
            target = safe_target(destination, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(item) as source, target.open('wb') as output:
                shutil.copyfileobj(source, output)
            if target.suffix == '.py':
                compile(target.read_bytes(), name, 'exec')
            files[name] = digest(target)
    if not REQUIRED <= files.keys():
        raise UpdateError('The download is missing required application files. Nothing was installed.')
    return files


def prepare_update(root, report, cancel):
    """Stage a commit-pinned update without replacing running application files."""
    root = Path(root).resolve()
    updates = root / '.updates'
    if updates.is_symlink():
        raise UpdateError('The update folder must not be a symbolic link.')
    updates.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='update-', dir=updates))
    keep = False
    try:
        report('Checking GitHub for updates…')
        fetch(f'{API}/commits/main', stage / 'commit.json', 2 * 1024**2, cancel, lambda value: None)
        sha = json.loads((stage / 'commit.json').read_text())['sha']
        if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha):
            raise UpdateError('GitHub returned an invalid version identifier.')
        if (root / '.git').exists():
            previous = run_git(root, ['rev-parse', 'HEAD'], cancel)[1]
            if previous == sha:
                return None
            clean_git(root, cancel)
            report('Downloading the new version…')
            run_git(root, ['fetch', '--no-tags', GIT_URL, sha], cancel)
            if run_git(root, ['merge-base', '--is-ancestor', previous, sha], cancel, allow_failure=True)[0]:
                raise UpdateError('Local Git history differs from GitHub main. Update this checkout manually; nothing was overwritten.')
            plan = {'mode': 'git', 'previous': previous}
        else:
            previous = {}
            stamp = safe_target(root, STAMP)
            if stamp.exists():
                previous = json.loads(stamp.read_text())
                if previous.get('sha') == sha:
                    return None
            report('Downloading the new version…')
            fetch(f'{API}/zipball/{sha}', stage / 'source.zip', MAX_DOWNLOAD, cancel, report)
            files = unpack(stage / 'source.zip', stage / 'source', cancel)
            old_files = previous.get('files', {})
            if not isinstance(old_files, dict) or any(not managed(name) for name in old_files):
                raise UpdateError('The saved update manifest is invalid.')
            removed = sorted(set(old_files) - files.keys())
            if not removed and all((target := safe_target(root, name)).is_file() and digest(target) == checksum for name, checksum in files.items()):
                atomic_json(stamp, {'sha': sha, 'files': files})
                return None
            plan = {'mode': 'zip', 'files': files, 'removed': removed}
        cancelled(cancel)
        plan.update(root=str(root), sha=sha)
        atomic_json(stage / 'plan.json', plan)
        # Use the current installed helper, never unreviewed helper code from the download.
        shutil.copy2(Path(__file__), stage / 'helper.py')
        keep = True
        return str(stage / 'plan.json')
    except (ValueError, KeyError, SyntaxError, zipfile.BadZipFile) as exc:
        raise UpdateError('The downloaded update is invalid or incompatible. Application files were not changed.') from exc
    finally:
        if not keep:
            shutil.rmtree(stage, ignore_errors=True)


def load_plan(plan_path):
    plan_path = Path(plan_path).resolve()
    plan = json.loads(plan_path.read_text())
    root = Path(plan['root']).resolve()
    if plan_path.parent.parent != root / '.updates' or not re.fullmatch('[0-9a-f]{40}', plan['sha']):
        raise UpdateError('Invalid local update plan.')
    if plan['mode'] not in {'git', 'zip'}:
        raise UpdateError('Unsupported update mode.')
    return root, plan_path.parent, plan


def replace_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + '.update-' + uuid.uuid4().hex)
    try:
        shutil.copy2(source, temp)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def rollback(root, stage):
    journal = json.loads((stage / 'transaction.json').read_text())
    for name, existed in journal['original'].items():
        target = safe_target(root, name)
        if existed:
            replace_file(stage / 'backup' / name, target)
        else:
            target.unlink(missing_ok=True)
    atomic_json(stage / 'transaction.json', {**journal, 'status': 'rolled_back'})


def install(plan_path):
    root, stage, plan = load_plan(plan_path)
    if plan['mode'] == 'git':
        clean_git(root)
        if run_git(root, ['rev-parse', 'HEAD'])[1] != plan['previous']:
            raise UpdateError('The checkout changed while downloading the update. Check updates again.')
        run_git(root, ['merge', '--ff-only', plan['sha']])
        return
    files, removed = plan['files'], plan['removed']
    for name, checksum in files.items():
        source = safe_target(stage / 'source', name)
        if digest(source) != checksum:
            raise UpdateError('A staged update file changed. Check updates again.')
    original = {}
    for name in sorted(set(files) | set(removed) | {STAMP}):
        target = safe_target(root, name)
        original[name] = target.exists()
        if original[name]:
            backup = stage / 'backup' / name
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
    atomic_json(stage / 'transaction.json', {'status': 'applying', 'original': original})
    try:
        for name in files:
            replace_file(stage / 'source' / name, safe_target(root, name))
        for name in removed:
            safe_target(root, name).unlink(missing_ok=True)
        atomic_json(root / STAMP, {'sha': plan['sha'], 'files': files})
        atomic_json(stage / 'transaction.json', {'status': 'installed', 'original': original})
    except Exception:
        rollback(root, stage)
        raise


def process_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # Access denied is not proof of exit.
        try:
            return kernel.WaitForSingleObject(handle, 0) != 0
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def resume_launcher(root):
    with (root / 'startup.log').open('a', encoding='utf-8') as log:
        subprocess.Popen([sys.executable, str(root / 'bootstrap.py')], cwd=root,
                         stdout=log, stderr=log, creationflags=FLAGS)


@contextmanager
def installation_lock(root):
    """Wait for any already-running dependency setup before replacing sources."""
    with (root / '.setup.lock').open('a+b') as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b'0')
            lock.flush()
        deadline = time.monotonic() + 120
        while True:
            try:
                lock.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise UpdateError('Another setup process is still running. No update was installed.')
                time.sleep(.2)
        yield


def start_installer(plan_path):
    root, stage, plan = load_plan(plan_path)
    marker = root / MARKER
    atomic_json(marker, {'pid': os.getpid(), 'plan': str(Path(plan_path).resolve())})
    try:
        with (root / 'update.log').open('a', encoding='utf-8') as log:
            process = subprocess.Popen([sys.executable, str(stage / 'helper.py'), '--install', str(plan_path),
                                        '--wait-pid', str(os.getpid())], cwd=root, stdout=log, stderr=log, creationflags=FLAGS)
        atomic_json(marker, {'pid': process.pid, 'plan': str(Path(plan_path).resolve())})
    except Exception:
        marker.unlink(missing_ok=True)
        raise


def startup_ready(root):
    """Prevent a second launcher during install; recover interrupted ZIP writes."""
    root = Path(root)
    marker = root / MARKER
    if not marker.exists():
        return True
    value = json.loads(marker.read_text())
    if process_alive(value['pid']):
        return False
    actual_root, stage, plan = load_plan(value['plan'])
    if actual_root != root.resolve():
        raise UpdateError('The saved update belongs to another installation.')
    journal = stage / 'transaction.json'
    if journal.exists() and json.loads(journal.read_text())['status'] == 'applying':
        rollback(root, stage)
    marker.unlink()
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--install', required=True)
    parser.add_argument('--wait-pid', required=True, type=int)
    args = parser.parse_args()
    root, stage, plan = load_plan(args.install)
    deadline = time.monotonic() + 120
    while process_alive(args.wait_pid):
        if time.monotonic() > deadline:
            (root / MARKER).unlink(missing_ok=True)
            raise UpdateError('The application did not exit. No update was installed.')
        time.sleep(.2)
    try:
        with installation_lock(root):
            install(args.install)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        message = str(exc) if isinstance(exc, UpdateError) else f'Update failed ({type(exc).__name__}). See update.log and the .updates backup folder.'
        print(message, flush=True)
        if os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, 'Update could not finish', 0x10)
    # A failed rollback keeps its journal and marker for startup recovery.
    journal = stage / 'transaction.json'
    if journal.exists() and json.loads(journal.read_text())['status'] == 'applying':
        return 1
    (root / MARKER).unlink(missing_ok=True)
    resume_launcher(root)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
