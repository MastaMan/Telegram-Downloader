import argparse
import os
import sys
from pathlib import Path
from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox
from shiboken6 import delete
from .ui import Window


def main():
    parser = argparse.ArgumentParser(description='Telegram ZIP Desktop')
    parser.add_argument('--demo', action='store_true', help='Preview with sample data; no login or credentials')
    parser.add_argument('--screenshot', help=argparse.SUPPRESS)
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName('Telegram ZIP Desktop')
    app.setOrganizationName('TelegramZipDesktop')
    if not args.demo and os.name != 'nt':
        QMessageBox.critical(None, 'Windows required', 'Secure credentials use Windows DPAPI. Run with --demo to preview the interface on another operating system.')
        return 1
    lock = None
    bridge = None
    if not args.demo:
        data_dir = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))) / 'TelegramZipDesktop'
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            lock = QLockFile(str(data_dir / 'app.lock'))
            lock.setStaleLockTime(0)
            if not lock.tryLock(0):
                QMessageBox.information(None, 'Already running', 'Telegram ZIP Desktop is already open. Use the existing window.')
                return 0
            from .engine import Bridge
            bridge = Bridge(data_dir)
        except Exception as exc:
            QMessageBox.critical(None, 'Startup failed', f'Cannot prepare application data ({type(exc).__name__}). Check your folder permissions.')
            return 1
    window = Window(bridge, demo=args.demo)
    window.show()
    if args.screenshot and args.demo:
        def capture():
            window.grab().save(args.screenshot)
            window.close()
        QTimer.singleShot(800, capture)
    result = app.exec()
    if bridge:
        # Normal window closure waits for the worker. Never terminate a live transfer thread.
        if bridge.thread.is_alive():
            bridge.send('close')
        bridge.thread.join()
    if lock:
        lock.unlock()
    # Destroy the widget tree while Python still owns the Window and QApplication.
    # Leaving signal/closure cycles to interpreter teardown can crash Shiboken.
    delete(window)
    return result


if __name__ == '__main__':
    raise SystemExit(main())
