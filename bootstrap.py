"""Single-entry launcher. Standard library only; installs into a private venv."""
import importlib.metadata
import json
import uuid
import os
import queue
import subprocess
import sys
import sysconfig
import threading
import venv
from pathlib import Path

WINDOWS = os.name == 'nt'
ROOT = Path(__file__).resolve().parent
ENV = ROOT / ('.venv-py314' if WINDOWS else '.venv')
PYTHON = ENV / ('Scripts/python.exe' if WINDOWS else 'bin/python')
PYTHONW = ENV / 'Scripts/pythonw.exe'
FLAGS = subprocess.CREATE_NO_WINDOW if WINDOWS else 0


def runtime_error():
    """The Qt abi3 wheels require a standard, 64-bit CPython build."""
    import struct
    if sys.implementation.name != 'cpython':
        return 'Please use standard CPython from python.org.'
    if not (3, 11) <= sys.version_info[:2] <= (3, 15):
        return 'Please install Python 3.14 (64-bit). Supported Python versions: 3.11-3.15.'
    if sys.version_info.releaselevel != 'final':
        return 'Please use a stable Python release, not an alpha, beta, or release candidate.'
    if struct.calcsize('P') != 8:
        return 'A 64-bit Python installation is required.'
    if sysconfig.get_config_var('Py_GIL_DISABLED'):
        return 'Please use standard Python, not the free-threaded build.'
    return None


def required_packages(text):
    for line in text.splitlines():
        if not line or line.startswith('#'):
            continue
        requirement, _, marker = line.partition(';')
        if marker:
            if marker.strip() != 'python_version < "3.15"':
                raise ValueError('Unsupported requirement marker')
            if sys.version_info[:2] >= (3, 15):
                continue
        name, version = requirement.strip().split('==')
        yield name, version


def check_environment():
    if not PYTHON.exists():
        return False
    code = '''import importlib.metadata as m
from bootstrap import runtime_error, required_packages
from pathlib import Path
if runtime_error():
    raise SystemExit(1)
import sys
if sys.platform == 'win32' and sys.version_info[:2] != (3, 14):
    raise SystemExit(1)
for name, version in required_packages(Path('requirements.txt').read_text()):
    assert m.version(name) == version
    if name == 'cryptg':
        import cryptg
        from telethon.crypto import aes
        assert aes.cryptg is cryptg
        data, key, iv = bytes(32), bytes(32), bytes(32)
        assert cryptg.decrypt_ige(cryptg.encrypt_ige(data, key, iv), key, iv) == data
import PySide6.QtWidgets, telethon, qrcode
'''
    try:
        result = subprocess.run([str(PYTHON), '-c', code], cwd=ROOT, capture_output=True, creationflags=FLAGS, timeout=45)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def windows_launcher(args):
    """Use the same self-contained launcher after updates, even from old Python."""
    environment = os.environ.copy()
    environment['TD_START_FILE'] = str(ROOT / 'Start.bat')
    environment['TD_PREFERRED_PYTHON'] = str(getattr(sys, '_base_executable', sys.executable))
    environment['TD_LAUNCH_ARGS'] = json.dumps(args)
    environment.pop('TD_RUNTIME_READY', None)
    command = "$s=[IO.File]::ReadAllText($env:TD_START_FILE); & ([ScriptBlock]::Create(($s -split '(?m)^# POWERSHELL_BEGIN\\r?\\n',2)[1]))"
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    return subprocess.run([str(powershell), '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                           '-Command', command], cwd=ROOT, env=environment).returncode


def app_running():
    from updater import process_alive
    lock = Path(os.environ.get('LOCALAPPDATA', '')) / 'TelegramZipDesktop/app.lock'
    try:
        return process_alive(int(lock.read_text(encoding='utf-8').splitlines()[0]))
    except (OSError, ValueError, IndexError):
        return False


def environment_runtime_ready():
    if not PYTHON.exists():
        return False
    try:
        code = 'import sys; assert sys.version_info[:2] == (3, 14)' if WINDOWS else 'import sys'
        return subprocess.run([str(PYTHON), '-I', '-c', code], capture_output=True,
                              creationflags=FLAGS, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def prepare_environment():
    if environment_runtime_ready():
        return
    if ENV.exists():
        # Retain a broken environment for recovery; never touch user data.
        ENV.rename(ENV.with_name(ENV.name + '.previous-' + uuid.uuid4().hex))
    venv.EnvBuilder(with_pip=True).create(ENV)


def launch():
    from updater import startup_ready
    if not startup_ready(ROOT):
        return
    interpreter = PYTHONW if WINDOWS and PYTHONW.exists() else PYTHON
    with open(ROOT / 'startup.log', 'a', encoding='utf-8') as log:
        environment = os.environ.copy()
        for key in ('TD_RUNTIME_READY', 'TD_PREFERRED_PYTHON', 'TD_START_FILE', 'TD_LAUNCH_ARGS'):
            environment.pop(key, None)
        subprocess.Popen([str(interpreter), '-m', 'tgzip', *sys.argv[1:]], cwd=ROOT,
                         stdout=log, stderr=log, creationflags=FLAGS, env=environment)


def main():
    if WINDOWS and '--check-python' not in sys.argv[1:]:
        from updater import startup_ready
        if not startup_ready(ROOT):
            return 0
        if app_running():
            print('Telegram File Hunter is already running. Use the existing window.')
            return 0
        if os.environ.get('TD_RUNTIME_READY') != '1' or sys.version_info[:2] != (3, 14):
            return windows_launcher(sys.argv[1:])
    problem = runtime_error()
    if problem:
        print(problem)
        return 1
    if '--check-python' in sys.argv[1:]:
        return 0
    from updater import startup_ready
    if not startup_ready(ROOT):
        print('An update is being installed. The application will restart automatically.')
        return 0
    # Serialize first-run setup across repeated double-clicks.
    lock = open(ROOT / '.setup.lock', 'a+b')
    lock.seek(0)
    if not lock.read(1):
        lock.write(b'0')
        lock.flush()
    lock.seek(0)
    if WINDOWS:
        import msvcrt
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            lock.close()
            print('Setup is already running. Please wait for the existing window.')
            return 0
    try:
        if check_environment():
            if startup_ready(ROOT):
                launch()
            return 0
        return setup_window()
    finally:
        lock.close()


def setup_window():
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
        window = tk.Tk()
    except Exception:
        print('Python tkinter is missing. Repair Python and enable Tcl/Tk support.')
        return 1
    window.title('Telegram File Hunter · First-time setup')
    window.geometry('610x285')
    window.resizable(False, False)
    window.configure(bg='#101929')
    frame = tk.Frame(window, bg='#101929', padx=28, pady=25)
    frame.pack(fill='both', expand=True)
    tk.Label(frame, text='Preparing your workspace', font=('Segoe UI', 20, 'bold'), bg='#101929', fg='#eff4fc').pack(anchor='w')
    tk.Label(frame, text='Installing required libraries in an isolated environment.\nThis can take a few minutes. Internet access is required.', justify='left',
             font=('Segoe UI', 11), bg='#101929', fg='#a9bad2').pack(anchor='w', pady=(13, 20))
    status = tk.StringVar(value='Checking dependencies…')
    tk.Label(frame, textvariable=status, font=('Segoe UI', 11), bg='#101929', fg='#8cb9ff').pack(anchor='w')
    bar = ttk.Progressbar(frame, mode='indeterminate')
    bar.pack(fill='x', pady=17)
    bar.start(12)
    tk.Label(frame, text='No administrator rights needed. You only do this once.', bg='#101929', fg='#a9bad2').pack(anchor='w')
    events = queue.Queue()
    result = [1]
    def setup():
        try:
            events.put(('status', 'Creating the private Python environment…'))
            prepare_environment()
            events.put(('status', 'Downloading and installing application libraries…'))
            with open(ROOT / 'setup.log', 'w', encoding='utf-8') as log:
                completed = subprocess.run([str(PYTHON), '-m', 'pip', 'install', '--disable-pip-version-check', '--only-binary=cryptg',
                                             '-r', str(ROOT / 'requirements.txt')], cwd=ROOT,
                                            stdout=log, stderr=subprocess.STDOUT, creationflags=FLAGS)
            if completed.returncode or not check_environment():
                raise RuntimeError('Dependency installation failed. Check setup.log, your internet connection, and folder permissions, then run Start.bat again.')
            events.put(('done', None))
        except Exception as exc:
            events.put(('error', str(exc)))
    def poll():
        try:
            while True:
                kind, text = events.get_nowait()
                if kind == 'status':
                    status.set(text)
                elif kind == 'done':
                    result[0] = 0
                    launch()
                    window.destroy()
                    return
                elif kind == 'error':
                    bar.stop()
                    messagebox.showerror('Setup could not finish', text)
                    window.destroy()
                    return
        except queue.Empty:
            pass
        window.after(100, poll)
    window.protocol('WM_DELETE_WINDOW', lambda: messagebox.showinfo('Setup in progress', 'Please allow installation to finish. Closing it midway could leave incomplete dependencies.'))
    worker = threading.Thread(target=setup, daemon=False)
    worker.start()
    window.after(100, poll)
    window.mainloop()
    worker.join()
    return result[0]


if __name__ == '__main__':
    raise SystemExit(main())
