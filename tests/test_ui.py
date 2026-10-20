"""Offscreen Qt checks. No Telegram connection or actual credentials are required."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import time
import subprocess
import sys
import unittest
from unittest.mock import patch
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
        self.assertEqual(window.file_bar.value(), window.overall_bar.value())
        self.assertEqual(window.file_name.text(), '4 active downloads')
        self.assertEqual(window.archive_bar.value(), 620)
        self.assertEqual(window.archive_name.text(), 'Environment assets — autumn.rar')
        self.assertNotEqual(window.archive_bar.value(), window.overall_bar.value())
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

    def test_speed_limit_presets_stay_available_during_downloads_and_pause(self):
        window = Window(demo=True)
        self.assertEqual([window.speed_box.itemText(index) for index in range(window.speed_box.count())],
                         ['Unlimited', '10 MB/s', '5 MB/s', '2 MB/s', '1 MB/s', '0.5 MB/s', '0.25 MB/s'])
        self.assertEqual(window.speed_box.currentData(), 0)
        self.assertTrue(window.speed_box.isEnabled())
        with patch.object(window, 'send') as send:
            window.speed_box.setCurrentIndex(6)
            send.assert_called_once_with('speed_limit', value=.25)
            send.reset_mock()
            window.on_event('settings', {'speed_limit': 5})
            send.assert_not_called()
            self.assertEqual(window.speed_box.currentData(), 5)
        window.on_event('state', 'paused')
        self.assertTrue(window.speed_box.isEnabled())
        window.close()

    def test_filename_restoration_has_clear_status_and_pause_stop_controls(self):
        window = Window(demo=True)
        window.on_event('state', 'renaming')
        self.assertEqual(window.file_status.text(), 'Restoring filenames…')
        self.assertTrue(window.pause_btn.isEnabled())
        self.assertTrue(window.stop_btn.isEnabled())
        self.assertFalse(window.start_btn.isEnabled())
        self.assertTrue(window.chat_box.isEnabled())
        window.on_event('rename_progress', {'done': 12, 'total': 20})
        self.assertEqual(window.file_bar.value(), 600)
        self.assertEqual(window.percent_label.text(), '60%')
        self.assertEqual(window.byte_label.text(), '12 of 20 archives processed')
        self.assertEqual(window.footer.text(), 'Restoring filenames: 12 / 20')
        self.assertEqual(window.file_status.text(), 'Restoring filenames…')
        window.on_event('rename_result', {'restored': 18, 'skipped': 2})
        self.assertEqual(window.file_name.text(), 'Filename restoration finished')
        self.assertEqual(window.byte_label.text(), '18 restored · 2 skipped')
        self.assertEqual(window.file_bar.value(), 1000)
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

    def test_parallel_display_retains_totals_and_updates_each_file(self):
        window = Window(demo=True)
        self.assertEqual(window.parallel_box.currentData(), 20)
        self.assertFalse(window.parallel_box.isEnabled())
        original = (window.file_name.text(), window.file_bar.value(), window.speed_value.text())
        window.on_event('progress', {'done': 70, 'size': 100, 'speed': 1024,
                                    'message': 4})
        self.assertEqual(window.table.item(3, 2).text(), '70%')
        self.assertEqual(original, (window.file_name.text(), window.file_bar.value(), window.speed_value.text()))
        window.on_event('state', 'paused')
        self.assertEqual(window.table.item(3, 3).text(), 'Paused')
        self.assertEqual(window.speed_value.text(), '—')
        window.on_event('state', 'downloading')
        self.assertEqual(window.table.item(3, 3).text(), 'Downloading')
        window.on_event('state', 'idle')
        self.assertTrue(window.parallel_box.isEnabled())
        window.on_event('settings', {'parallel_downloads': 5})
        self.assertEqual(window.parallel_box.currentData(), 5)
        window.close()

    def test_individual_bar_stays_on_one_active_file_then_follows_next(self):
        window = Window(demo=True)
        window.on_event('progress', {'message': 4, 'done': 70, 'size': 100, 'speed': 10})
        self.assertEqual(window.watched_job['message'], 3)
        self.assertEqual(window.archive_bar.value(), 620)
        window.on_event('progress', {'message': 3, 'done': 100, 'size': 100, 'speed': 10})
        self.assertEqual(window.archive_bar.value(), 1000)
        window.on_event('transfers', dict(window.transfers_info,
                                         files=[row for mid, row in window.active_files.items() if mid != 3],
                                         active=3))
        self.assertEqual(window.watched_job['message'], 4)
        self.assertEqual(window.archive_bar.value(), 700)
        window.close()

    def test_row_selection_pins_progress_without_changing_overall_bar(self):
        window = Window(demo=True)
        overall = window.file_bar.value()
        window.table.setCurrentCell(3, 0)
        self.assertFalse(window.archive_follow.isChecked())
        self.assertEqual(window.watched_job['message'], 4)
        self.assertEqual(window.archive_bar.value(), 400)
        window.on_event('progress', {'message': 3, 'done': 95, 'size': 100, 'speed': 10})
        self.assertEqual(window.archive_bar.value(), 400)
        window.on_event('progress', {'message': 4, 'done': 75, 'size': 100, 'speed': 10})
        self.assertEqual(window.archive_bar.value(), 750)
        self.assertIn('75%', window.archive_detail.text())
        self.assertEqual(window.file_bar.value(), overall)
        # A queue refresh may insert/reorder rows but must retain file identity.
        rows = list(window.queue_jobs.values())
        window.on_event('queue', {'rows': rows[::-1], 'summary': window.transfers_info['summary']})
        self.assertEqual(window.watched_job['message'], 4)
        self.assertFalse(window.archive_follow.isChecked())
        self.assertEqual(window.table.currentRow(), 8)
        window.on_event('state', 'paused')
        self.assertEqual(window.archive_bar.value(), 750)
        window.close()

    def test_completed_selection_stays_visible_until_auto_follow_is_enabled(self):
        window = Window(demo=True)
        window.table.setCurrentCell(0, 0)
        self.assertEqual(window.archive_bar.value(), 1000)
        self.assertEqual(window.watched_job['message'], 1)
        window.on_event('verification', {'name': 'another.zip', 'detail': 'Checking attempt 2/3'})
        self.assertEqual(window.watched_job['message'], 1)
        window.archive_follow.setChecked(True)
        self.assertEqual(window.watched_job['message'], 3)
        self.assertEqual(window.archive_bar.value(), 620)
        # Clicking the still-highlighted row must pin it again after Auto-follow.
        window.table.cellClicked.emit(0, 0)
        self.assertEqual(window.watched_job['message'], 1)
        self.assertFalse(window.archive_follow.isChecked())
        window.close()

    def test_active_file_outside_visible_queue_and_chat_reset(self):
        window = Window(demo=True)
        job = {'message': 2001, 'name': 'Later archive.RAR', 'path': 'C:/Later archive.RAR',
               'done': 25, 'size': 100}
        window.on_event('transfers', dict(window.transfers_info, active=1, files=[job]))
        self.assertNotIn(2001, window.queue_rows)
        self.assertEqual(window.archive_name.text(), 'Later archive.RAR')
        self.assertEqual(window.archive_bar.value(), 250)
        window.on_event('progress', {'message': 2001, 'done': 100, 'size': 100, 'speed': 0})
        window.on_event('transfers', dict(window.transfers_info, active=0, files=[]))
        self.assertEqual(window.archive_bar.value(), 1000)
        window.on_event('selected', -100456)
        self.assertIsNone(window.watched_job)
        self.assertEqual(window.archive_bar.value(), 0)
        self.assertEqual(window.archive_name.text(), 'No file selected')
        self.assertTrue(window.archive_follow.isChecked())
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
