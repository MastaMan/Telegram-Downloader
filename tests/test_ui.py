"""Offscreen Qt checks. No Telegram connection or actual credentials are required."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import time
import subprocess
import sys
import unittest
from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication, QPushButton
from shiboken6 import delete, isValid
from tgzip.engine import Bridge
from tgzip.ui import QRDialog, SettingsDialog, Window


class UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def events_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.app.processEvents()
        self.assertTrue(predicate())

    def tearDown(self):
        # Keep wrappers alive while Qt destroys their children and connected slots.
        windows = self.app.topLevelWidgets()
        for window in windows:
            if isValid(window):
                delete(window)

    def test_demo_renders_and_controls_are_readable(self):
        window = Window(demo=True)
        window.show()
        self.app.processEvents()
        self.assertEqual(window.table.rowCount(), 12)
        self.assertFalse(window.activity.isVisible())
        self.assertGreaterEqual(window.table.viewport().height() // 32, 9)
        self.assertEqual(window.file_bar.value(), 620)
        self.assertIn('18.6', window.speed_value.text())
        self.assertTrue(window.chat_box.isEnabled())
        self.assertFalse(window.start_btn.isEnabled())
        for btn in window.findChildren(QPushButton):
            if btn.isVisible():
                self.assertGreaterEqual(btn.height(), 36, btn.text())
        self.assertFalse(window.grab().isNull())
        window.close()

    def test_demo_process_renders_and_exits_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            screenshot = Path(directory) / 'preview.png'
            result = subprocess.run(
                [sys.executable, '-m', 'tgzip', '--demo', '--screenshot', str(screenshot)],
                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(screenshot.exists())
            self.assertGreater(screenshot.stat().st_size, 1000)

    def test_progress_states_and_chat_switch_availability(self):
        window = Window(demo=True)
        window.on_event('state', 'paused')
        self.assertIn('Resume', window.pause_btn.text())
        self.assertTrue(window.chat_box.isEnabled())
        self.assertEqual(window.speed_value.text(), '—')
        window.on_event('state', 'idle')
        self.assertTrue(window.start_btn.isEnabled())
        self.assertFalse(window.stop_btn.isEnabled())
        window.close()

    def test_verification_states_are_distinct_from_download_progress(self):
        window = Window(demo=True)
        self.assertEqual(window.table.item(0, 3).text(), 'Verified')
        self.assertEqual(window.table.item(1, 3).text(), 'Checking…')
        window.on_event('state', 'idle')
        self.assertTrue(window.verify_btn.isEnabled())
        window.on_event('verification', None)
        window.on_event('checker', None)
        self.assertIn('not configured', window.checker_label.text())
        window.on_event('state', 'verifying')
        self.assertTrue(window.pause_btn.isEnabled())
        self.assertFalse(window.checker_btn.isEnabled())
        window.on_event('phase', 'Checking archive without a password: attempt 2/3…')
        self.assertIn('Checking', window.file_status.text())
        self.assertIn('2/3', window.footer.text())
        window.close()

    def test_background_check_does_not_replace_download_display(self):
        window = Window(demo=True)
        original = (window.current_job, window.file_name.text(), window.file_bar.value(),
                    window.speed_value.text(), window.eta_value.text(), window.file_status.text())
        window.on_event('verification', {'name': 'previous.zip', 'detail': 'Checking archive without a password: attempt 3/3…'})
        self.assertEqual(original, (window.current_job, window.file_name.text(), window.file_bar.value(),
                                   window.speed_value.text(), window.eta_value.text(), window.file_status.text()))
        self.assertIn('3/3', window.checker_label.text())
        self.assertIn('previous.zip', window.checker_label.toolTip())
        window.on_event('state', 'paused')
        self.assertIn('paused', window.checker_label.text())
        window.on_event('state', 'downloading')
        self.assertIn('Checking', window.checker_label.text())
        window.on_event('verification', None)
        self.assertEqual(window.checker_label.text(), 'Automatic checks on')
        window.close()

    def test_update_control_and_result_survive_idle_transition(self):
        window = Window(demo=True)
        self.assertFalse(window.update_btn.isEnabled())
        window.on_event('state', 'idle')
        self.assertTrue(window.update_btn.isEnabled())
        window.on_event('state', 'updating')
        self.assertFalse(window.update_btn.isEnabled())
        window.on_event('update_progress', 'You are up to date.')
        window.on_event('state', 'idle')
        self.assertEqual(window.footer.text(), 'You are up to date.')
        window.close()

    def test_qr_image_is_rendered_and_can_be_cancelled(self):
        window = Window(demo=True)
        cancelled = []
        dialog = QRDialog(window, lambda: cancelled.append(True))
        dialog.set_url('tg://login?token=synthetic-test-token')
        self.assertFalse(dialog.picture.pixmap().isNull())
        self.assertGreater(dialog.picture.pixmap().width(), 200)
        dialog.reject()
        self.assertEqual(cancelled, [True])
        window.close()

    def test_api_settings_validate_before_accepting(self):
        window = Window(demo=True)
        dialog = SettingsDialog(window)
        dialog.api_id.setText('invalid')
        dialog.api_hash.setText('wrong')
        dialog.validate()
        self.assertIn('positive API ID', dialog.error.text())
        self.assertEqual(dialog.result(), 0)
        dialog.reject()
        window.close()

    def test_single_instance_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = str(Path(directory) / 'app.lock')
            first, second = QLockFile(lock_path), QLockFile(lock_path)
            self.assertTrue(first.tryLock(0))
            self.assertFalse(second.tryLock(0))
            first.unlock()
            self.assertTrue(second.tryLock(0))
            second.unlock()

    def test_real_background_worker_closes_without_thread_leak(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge = Bridge(directory)
            self.events_until(lambda: bridge.ready.is_set())
            window = Window(bridge)
            window.show()
            self.events_until(lambda: window.isVisible())
            # Let startup boot complete before asking the worker to shut down.
            deadline = time.monotonic() + .12
            self.events_until(lambda: time.monotonic() >= deadline)
            window.close()
            self.events_until(lambda: not bridge.thread.is_alive())
            self.events_until(lambda: window.can_close and not window.isVisible())
            bridge.thread.join(timeout=1)


if __name__ == '__main__':
    unittest.main()
