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
from PySide6.QtCore import QLockFile, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QProgressBar
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
        self.assertEqual(window.table.rowCount(), 10)
        self.assertFalse(window.activity.isVisible())
        self.assertEqual((window.width(), window.height()), (960, 640))
        self.assertGreaterEqual(window.table.viewport().height() // window.table.rowHeight(0), 9)
        summary = window.transfers_info['summary']
        self.assertEqual(window.file_bar.value(), round(1000 * summary['received'] / summary['bytes']))
        self.assertEqual(window.file_name.text(), '4 active · Limit 20')
        self.assertEqual(window.transfer_tiles[3].bar.value(), 620)
        self.assertEqual(len(window.findChildren(QProgressBar)), 1 + len(window.transfer_tiles))
        self.assertIn('18.6', window.speed_value.text())
        self.assertTrue(window.chat_box.isEnabled())
        self.assertFalse(window.start_btn.isEnabled())
        for btn in window.findChildren(QPushButton):
            if btn.isVisible():
                self.assertGreaterEqual(btn.height(), 28, btn.text())
        self.assertFalse(window.grab().isNull())
        window.close()

    def test_compact_layout_keeps_controls_visible_at_minimum_size(self):
        window = Window(demo=True)
        window.resize(1000, 680)
        window.show()
        self.app.processEvents()
        self.assertEqual((window.width(), window.height()), (1000, 680))
        self.assertGreaterEqual(window.table.viewport().height() // window.table.rowHeight(0), 11)
        for state in ('downloading', 'paused', 'idle'):
            window.on_event('state', state)
            window.activity_toggle.click()
            self.app.processEvents()
            for widget in (window.table, window.active_strip, window.file_bar, window.chat_box,
                           window.speed_box, window.verify_btn, window.finish_btn, window.stop_btn):
                position = widget.mapTo(window, widget.rect().topLeft())
                self.assertGreaterEqual(position.x(), 0)
                self.assertGreaterEqual(position.y(), 0)
                self.assertLessEqual(position.x() + widget.width(), window.width())
                self.assertLessEqual(position.y() + widget.height(), window.height())
            for btn in window.findChildren(QPushButton):
                if btn.isVisible():
                    self.assertGreaterEqual(btn.width(), btn.fontMetrics().horizontalAdvance(btn.text()) + 12,
                                            btn.text())
        # Unbounded names and errors must not force a larger window or obscure controls.
        window.on_event('account', {'name': 'Long account name ' * 30, 'id': 123456789})
        window.on_event('settings', {'download_dir': 'C:/Downloads/' + 'Long folder name ' * 30})
        window.footer.setText('A very long operation detail ' * 50)
        self.app.processEvents()
        self.assertEqual((window.width(), window.height()), (1000, 680))
        self.assertEqual(window.footer.toolTip(), window.footer.text())
        window.close()

    def test_half_size_window_keeps_controls_and_scrollable_downloads_accessible(self):
        window = Window(demo=True)
        window.show()
        files = [dict(message=i, name=f'Archive {i}.zip', path=f'{i}.zip', size=100, done=i * 5)
                 for i in range(1, 21)]
        window.on_event('transfers', dict(window.transfers_info, files=files, active=20))
        for state in ('downloading', 'paused', 'idle'):
            window.on_event('state', state)
            window.resize(500, 340)
            for _ in range(5):
                self.app.processEvents()
            self.assertEqual((window.width(), window.height()), (500, 340))
            for widget in (window.panel_btn, window.update_btn, window.scan_btn, window.start_btn,
                           window.pause_btn, window.finish_btn, window.stop_btn, window.queue_filter,
                           window.first_page, window.last_page, window.page_number, window.table):
                self.assertTrue(widget.isVisible())
                position = widget.mapTo(window, widget.rect().topLeft())
                self.assertGreaterEqual(position.x(), 0)
                self.assertGreaterEqual(position.y(), 0)
                self.assertLessEqual(position.x() + widget.width(), window.width())
                self.assertLessEqual(position.y() + widget.height(), window.height())
            self.assertGreaterEqual(window.table.viewport().height(), window.table.rowHeight(0))
        scroll = window.active_scroll.horizontalScrollBar()
        self.assertGreater(scroll.maximum(), 0)
        scroll.setValue(scroll.maximum())
        self.app.processEvents()
        last = window.transfer_tiles[20]
        self.assertEqual(last.percent.text(), '100%')
        self.assertGreaterEqual(last.percent.width(), last.percent.fontMetrics().horizontalAdvance('100%'))
        self.assertLessEqual(last.mapTo(window.active_scroll.viewport(), last.rect().topRight()).x(),
                             window.active_scroll.viewport().width())
        window.panel_btn.click()
        self.app.processEvents()
        self.assertTrue(window.panel_dialog.isVisible())
        self.assertTrue(window.chat_box.isVisible())
        self.assertTrue(window.parallel_box.isVisible())
        window.panel_dialog.close()
        self.app.processEvents()
        self.assertFalse(window.sidebar_scroll.isVisible())
        window.activity_toggle.click()
        self.app.processEvents()
        self.assertTrue(window.activity_dialog.isVisible())
        window.activity_dialog.close()
        self.assertFalse(window.activity_toggle.isChecked())
        window.resize(1000, 680)
        for _ in range(5):
            self.app.processEvents()
        self.assertTrue(window.sidebar_scroll.isVisible())
        self.assertFalse(window.panel_btn.isVisible())
        self.assertEqual(len(window.transfer_tiles), 20)
        window.show_panel()
        window.activity_toggle.click()
        self.app.processEvents()
        window.close()
        self.assertFalse(window.panel_dialog.isVisible())
        self.assertFalse(window.activity_dialog.isVisible())

    def test_demo_process_renders_and_exits_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            screenshot = Path(directory) / 'preview.png'
            result = subprocess.run(
                [sys.executable, '-m', 'tgzip', '--demo', '--screenshot', str(screenshot)],
                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(screenshot.exists())
            self.assertGreater(screenshot.stat().st_size, 1000)

    def test_finish_current_control_and_status_follow_operation_lifecycle(self):
        window = Window(demo=True)
        window.resize(1000, 680)
        window.show()
        self.app.processEvents()
        self.assertTrue(window.finish_btn.isEnabled())
        with patch.object(window, 'send') as send:
            window.finish_btn.click()
            send.assert_called_once_with('finish_current')
        window.on_event('finish_current', True)
        self.assertFalse(window.finish_btn.isEnabled())
        self.assertTrue(window.stop_btn.isEnabled())
        self.assertIn('Finishing current', window.file_status.text())
        window.on_event('state', 'paused')
        self.assertIn('paused', window.file_status.text())
        self.assertTrue(window.pause_btn.isEnabled())
        self.assertTrue(window.stop_btn.isEnabled())
        window.on_event('state', 'downloading')
        self.assertIn('Finishing current', window.file_status.text())
        window.on_event('state', 'verifying')
        self.assertIn('Finishing checks', window.file_status.text())
        window.on_event('state', 'idle')
        self.assertFalse(window.finish_requested)
        self.assertFalse(window.finish_btn.isEnabled())
        window.on_event('state', 'scanning')
        window.on_event('state', 'paused')
        self.assertFalse(window.finish_btn.isEnabled())
        window.on_event('state', 'downloading')
        window.on_event('state', 'paused')
        self.assertTrue(window.finish_btn.isEnabled())
        window.close()

    def test_active_strip_fits_twenty_files_and_preserves_each_progress_on_pause(self):
        window = Window(demo=True)
        window.resize(1000, 680)
        window.show()
        files = [dict(message=i, name=f'Long model archive number {i}.zip', path=f'{i}.zip',
                      size=100, done=i * 5) for i in range(1, 21)]
        window.on_event('transfers', dict(window.transfers_info, files=files, active=20))
        self.app.processEvents()
        self.assertEqual(len(window.transfer_tiles), 20)
        self.assertEqual(window.width(), 1000)
        for i, tile in window.transfer_tiles.items():
            self.assertEqual(tile.percent.text(), f'{i * 5}%')
            self.assertEqual(tile.bar.value(), i * 50)
            self.assertGreaterEqual(tile.percent.width(), tile.percent.fontMetrics().horizontalAdvance(tile.percent.text()))
            self.assertLessEqual(tile.x() + tile.width(), window.active_strip.width())
        tiles = list(window.transfer_tiles.values())
        for left, right in zip(tiles, tiles[1:]):
            self.assertGreaterEqual(right.x() - (left.x() + left.width()), 6)
        for width in (900, 960, 1000):
            window.resize(width, 680)
            for _ in range(3):
                self.app.processEvents()
            for active_tile in window.transfer_tiles.values():
                self.assertGreaterEqual(active_tile.percent.width(),
                                        active_tile.percent.fontMetrics().horizontalAdvance('100%'))
        tile = window.transfer_tiles[3]
        window.on_event('progress', {'message': 3, 'done': 77, 'size': 100, 'speed': 10})
        self.assertEqual(tile.percent.text(), '77%')
        window.on_event('state', 'paused')
        self.assertEqual(tile.bar.value(), 770)
        self.assertIn('Paused', tile.toolTip())
        window.on_event('transfers', dict(window.transfers_info, files=files[2:4], active=2))
        self.assertIs(window.transfer_tiles[3], tile)
        self.assertEqual(set(window.transfer_tiles), {3, 4})
        window.on_event('selected', -100456)
        self.assertFalse(window.transfer_tiles)
        self.assertFalse(window.active_strip.isVisible())
        window.close()

    def test_three_queue_filters_default_to_unfinished_without_affecting_transfers(self):
        window = Window(demo=True)
        self.assertEqual(window.queue_filter.currentData(), 'unfinished')
        self.assertEqual(set(window.queue_rows), set(range(3, 13)))
        self.assertIn('10 remaining', window.queue_caption.text())
        original_tiles = dict(window.transfer_tiles)
        window.queue_filter.setCurrentIndex(window.queue_filter.findData('downloaded'))
        self.assertEqual(set(window.queue_rows), {1, 2})
        window.queue_filter.setCurrentIndex(window.queue_filter.findData('all'))
        self.assertEqual(len(window.queue_rows), 12)
        self.assertEqual(window.table.item(0, 0).data(Qt.ItemDataRole.UserRole), 3)
        self.assertEqual(window.transfer_tiles, original_tiles)
        window.queue_filter.setCurrentIndex(window.queue_filter.findData('unfinished'))
        rows = list(window.demo_rows)
        rows[2] = dict(rows[2], state='done', done=rows[2]['size'], verification='pending')
        window.demo_rows = rows
        window.demo_summary = dict(window.demo_summary, complete=3)
        window.render_demo_queue()
        self.assertNotIn(3, window.queue_rows)
        self.assertIn('9 remaining', window.queue_caption.text())
        self.assertEqual(window.state, 'downloading')
        window.close()

    def test_queue_paging_jump_clamp_and_stale_responses(self):
        window = Window(demo=True)
        window.show()
        self.app.processEvents()
        template = window.demo_rows[0]
        window.demo_rows = [dict(template, message=i, state='queued', done=0) for i in range(1, 451)]
        window.demo_rows[-1] = dict(window.demo_rows[-1], state='downloading', done=50)
        window.demo_summary = dict(window.demo_summary, total=450, complete=0)
        window.request_queue_page(0)
        self.assertEqual(window.table.rowCount(), 200)
        self.assertEqual(window.table.item(0, 0).data(Qt.ItemDataRole.UserRole), 450)
        first_reply = dict(window.queue_snapshot)
        window.page_number.setFocus()
        window.page_number.setText('3')
        QTest.keyClick(window.page_number, Qt.Key.Key_Return)
        self.assertEqual(window.queue_page, 2)
        self.assertEqual(window.table.rowCount(), 50)
        self.assertEqual(window.page_number.text(), '3')
        self.assertIn('401–450 of 450', window.total_label.text())
        self.assertFalse(window.next_page.isEnabled())
        self.assertTrue(window.first_page.isEnabled())
        current_messages = set(window.queue_rows)
        window.on_event('queue', first_reply)
        self.assertEqual(set(window.queue_rows), current_messages)
        # Finishing the last page clamps navigation rather than leaving a blank page.
        window.demo_rows = window.demo_rows[:100]
        window.demo_summary = dict(window.demo_summary, total=100)
        window.render_demo_queue()
        self.assertEqual((window.queue_page, window.queue_pages), (0, 1))
        self.assertEqual(window.page_number.text(), '1')
        self.assertEqual(window.table.rowCount(), 100)
        self.assertEqual(len(window.transfer_tiles), 4)
        window.close()

    def test_saved_queue_shows_overall_progress_before_start_and_after_stop(self):
        window = Window()
        summary = {'total': 2, 'complete': 1, 'failed': 0, 'bytes': 200, 'received': 150}
        window.on_event('queue', {'rows': [], 'summary': summary})
        self.assertEqual(window.file_bar.value(), 750)
        self.assertEqual(window.percent_label.text(), '75%')
        self.assertEqual(window.byte_label.text(), '150 B of 200 B')
        window.on_event('state', 'renaming')
        window.on_event('rename_progress', {'done': 1, 'total': 4})
        window.on_event('queue', {'rows': [], 'summary': summary})
        self.assertEqual(window.file_bar.value(), 250)
        window.on_event('state', 'idle')
        window.on_event('queue', {'rows': [], 'summary': summary})
        self.assertEqual(window.file_bar.value(), 750)
        window.on_event('current', None)
        window.on_event('queue', {'rows': [], 'summary': dict(summary, total=0, complete=0, received=0, bytes=0)})
        self.assertEqual(window.file_bar.value(), 0)
        self.assertEqual(window.file_name.text(), 'No archives queued')
        window.close()

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
        window.queue_filter.setCurrentIndex(window.queue_filter.findData('downloaded'))
        self.assertEqual(window.table.item(window.queue_rows[1], 3).text(), 'Verified')
        self.assertEqual(window.table.item(window.queue_rows[2], 3).text(), 'Checking…')
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

    def test_live_parallel_control_shows_active_count_and_limit_without_losing_progress(self):
        window = Window(demo=True)
        original = (window.file_bar.value(), dict(window.transfer_tiles))
        for state, limit in (('downloading', 2), ('paused', 10)):
            window.on_event('state', state)
            self.assertTrue(window.parallel_box.isEnabled())
            with patch.object(window, 'send') as send:
                window.parallel_box.setCurrentIndex(limit - 1)
                send.assert_called_once_with('parallel', value=limit)
                window.on_event('settings', {'parallel_downloads': limit})
                self.assertEqual(send.call_count, 1)
            self.assertEqual(window.file_name.text(), f'4 active · Limit {limit}')
            self.assertEqual(original, (window.file_bar.value(), dict(window.transfer_tiles)))
        self.assertEqual(window.speed_value.text(), '—')
        window.on_event('finish_current', True)
        self.assertTrue(window.parallel_box.isEnabled())
        self.assertFalse(window.finish_btn.isEnabled())
        window.on_event('state', 'updating')
        self.assertFalse(window.parallel_box.isEnabled())
        window.close()

    def test_parallel_display_retains_totals_and_updates_each_file(self):
        window = Window(demo=True)
        self.assertEqual(window.parallel_box.currentData(), 20)
        self.assertTrue(window.parallel_box.isEnabled())
        original = (window.file_name.text(), window.file_bar.value(), window.speed_value.text())
        window.on_event('progress', {'done': 70, 'size': 100, 'speed': 1024,
                                    'message': 4})
        row = window.queue_rows[4]
        self.assertEqual(window.table.item(row, 2).text(), '70%')
        self.assertAlmostEqual(window.table.item(row, 2).data(Qt.ItemDataRole.UserRole), .7)
        self.assertEqual(window.table.item(row, 1).text(), '70 B / 100 B')
        self.assertEqual(original, (window.file_name.text(), window.file_bar.value(), window.speed_value.text()))
        window.on_event('state', 'paused')
        self.assertEqual(window.table.item(row, 3).text(), 'Paused')
        self.assertEqual(window.speed_value.text(), '—')
        window.on_event('state', 'downloading')
        self.assertEqual(window.table.item(row, 3).text(), 'Downloading')
        window.on_event('state', 'idle')
        self.assertTrue(window.parallel_box.isEnabled())
        window.on_event('settings', {'parallel_downloads': 5})
        self.assertEqual(window.parallel_box.currentData(), 5)
        window.close()

    def test_queue_selection_preserves_overall_and_active_progress(self):
        window = Window(demo=True)
        overall = window.file_bar.value()
        window.table.setCurrentCell(window.queue_rows[4], 0)
        window.on_event('progress', {'message': 3, 'done': 95, 'size': 100, 'speed': 10})
        window.on_event('progress', {'message': 4, 'done': 75, 'size': 100, 'speed': 10})
        self.assertEqual(window.transfer_tiles[3].bar.value(), 950)
        self.assertEqual(window.transfer_tiles[4].bar.value(), 750)
        self.assertEqual(window.file_bar.value(), overall)
        rows = list(window.queue_jobs.values())
        window.on_event('queue', {'rows': rows[::-1], 'summary': window.transfers_info['summary']})
        self.assertEqual(window.table.currentRow(), window.queue_rows[4])
        self.assertEqual(window.transfer_tiles[4].bar.value(), 750)
        window.close()

    def test_active_file_outside_visible_queue_and_chat_reset(self):
        window = Window(demo=True)
        job = {'message': 2001, 'name': 'Later archive.RAR', 'path': 'C:/Later archive.RAR',
               'done': 25, 'size': 100}
        window.on_event('transfers', dict(window.transfers_info, active=1, files=[job]))
        self.assertNotIn(2001, window.queue_rows)
        tile = window.transfer_tiles[2001]
        self.assertEqual(tile.name.text(), 'Later archive.RAR')
        self.assertEqual(tile.bar.value(), 250)
        window.on_event('progress', {'message': 2001, 'done': 100, 'size': 100, 'speed': 0})
        self.assertEqual(tile.bar.value(), 1000)
        window.on_event('transfers', dict(window.transfers_info, active=0, files=[]))
        self.assertFalse(window.transfer_tiles)
        window.on_event('selected', -100456)
        self.assertFalse(window.active_files)
        self.assertFalse(window.active_strip.isVisible())
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
