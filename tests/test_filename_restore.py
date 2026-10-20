"""Real-file legacy renames with mocked Telegram and interrupted move recovery."""
import asyncio
import os
import sqlite3
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tgzip.engine import Engine
from tgzip.archive_check import CheckResult
from tgzip.storage import Store
from tgzip.transfer import TransferError


class SimulatedExit(BaseException):
    pass


class FilenameRestoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.events = []
        self.engine = Engine(self.root, lambda event, value: self.events.append((event, value)))
        self.engine.account, self.engine.chat = 7, -10
        self.engine.dialogs = {-10: SimpleNamespace(input_entity=-10)}
        self.messages = {}
        async def get_messages(entity, ids):
            return [self.messages.get(number) for number in ids]
        self.engine.client = SimpleNamespace(get_messages=AsyncMock(side_effect=get_messages))

    async def asyncTearDown(self):
        await self.engine.cancel()
        self.engine.store.close()
        self.temp.cleanup()

    def add(self, number=1, original='Original Model.ZIP', old_name='Original Model.zip', quarantine=False):
        source = self.root / f'{number}_{1000 + number}_{old_name}'
        data = f'archive content {number}'.encode()
        self.engine.store.scanned(7, -10, number, {
            'document': 1000 + number, 'name': old_name, 'path': source, 'size': len(data)})
        actual = source
        if quarantine:
            actual = self.root / 'wrong_archives' / (source.stem + '_bad_a1b2c3d4' + source.suffix)
            actual.parent.mkdir(exist_ok=True)
        actual.write_bytes(data)
        self.engine.store.update(7, -10, number, done=len(data), state='done',
                                 verification='quarantined' if quarantine else 'ok',
                                 quarantine_path=str(actual) if quarantine else '')
        self.messages[number] = SimpleNamespace(
            id=number, document=SimpleNamespace(id=1000 + number, size=len(data), mime_type='application/zip'),
            file=SimpleNamespace(name=original))
        return actual, data

    def job(self, number=1):
        return next(job for job in self.engine.store.jobs(7, -10) if job['message'] == number)

    def reopen(self):
        self.engine.store.close()
        self.engine.store = Store(self.root / 'queue.sqlite3')

    async def test_restores_full_original_name_and_keeps_completion_and_checks(self):
        name = 'Chair Модель ' + 'x' * 120 + '.ZIP'
        source, data = self.add(original=name)
        await self.engine.restore_filenames()
        job = self.job()
        self.assertEqual(job['name'], name)
        self.assertEqual(Path(job['path']).read_bytes(), data)
        self.assertEqual(Path(job['path']).name, name)
        self.assertFalse(source.exists())
        self.assertEqual((job['state'], job['done'], job['verification']), ('done', len(data), 'ok'))
        self.assertEqual(job['naming_version'], 2)
        self.assertFalse(self.engine.store.filename_moves(7, -10))
        await self.engine.restore_filenames()
        self.engine.client.get_messages.assert_awaited_once()
        with patch.object(self.engine, 'checker', return_value=None):
            await self.engine.download_queue()
        self.engine.client.get_messages.assert_awaited_once()
        self.assertEqual(self.engine.store.summary(7, -10)['complete'], 1)

    async def test_manual_archive_check_receives_restored_path(self):
        self.add()
        check = AsyncMock(return_value=CheckResult('ok', 'Verified', 0))
        with patch.object(self.engine, 'checker', return_value=Path('WinRAR.exe')), \
             patch('tgzip.engine.check_archive', check):
            await self.engine.verify_queue()
        self.assertEqual(check.call_args.args[0], self.root / 'Original Model.ZIP')
        self.assertEqual(self.job()['verification'], 'ok')

    async def test_quarantined_file_stays_in_quarantine_and_saved_path_updates(self):
        source, data = self.add(quarantine=True)
        old_normal_path = self.job()['path']
        await self.engine.restore_filenames()
        job = self.job()
        target = Path(job['quarantine_path'])
        self.assertEqual(target, self.root / 'wrong_archives' / 'Original Model.ZIP')
        self.assertEqual(target.read_bytes(), data)
        self.assertFalse(source.exists())
        self.assertEqual(job['verification'], 'quarantined')
        self.assertEqual(job['path'], old_normal_path)

    async def test_collisions_with_disk_and_queued_files_only_add_prefix(self):
        self.add(original='model.ZIP')
        existing = self.root / 'MODEL.zip'
        existing.write_bytes(b'keep me')
        self.engine.store.scanned(7, -10, 9, {
            'document': 9, 'name': 'model.ZIP', 'size': 1,
            'path': self.root / '__dup_2__model.ZIP', 'naming_version': 1})
        await self.engine.restore_filenames()
        self.assertEqual(Path(self.job()['path']).name, '__dup_3__model.ZIP')
        self.assertEqual(existing.read_bytes(), b'keep me')

    async def test_metadata_requests_are_batched_and_duplicate_names_are_unique(self):
        for number in range(1, 106):
            self.add(number, original='model.rar')
        await self.engine.restore_filenames()
        calls = self.engine.client.get_messages.call_args_list
        self.assertEqual([len(call.kwargs['ids']) for call in calls], [100, 5])
        paths = [job['path'] for job in self.engine.store.jobs(7, -10)]
        self.assertEqual(len(set(paths)), 105)
        for number, path in enumerate(paths, 1):
            self.assertEqual(Path(path).read_bytes(), f'archive content {number}'.encode())

    async def test_deleted_or_edited_messages_never_rename_files(self):
        sources = [self.add(number)[0] for number in range(1, 4)]
        del self.messages[1]
        self.messages[2].document.id = 999
        self.messages[3].document.size += 1
        await self.engine.restore_filenames()
        self.assertTrue(all(source.exists() for source in sources))
        self.assertTrue(all(job['naming_version'] == 0 for job in self.engine.store.jobs(7, -10)))

    async def test_missing_changed_and_symlink_files_are_skipped_before_network(self):
        missing, _ = self.add(1)
        missing.unlink()
        changed, _ = self.add(2)
        changed.write_bytes(b'changed')
        linked, data = self.add(3)
        linked.unlink()
        other = self.root / 'unrelated.zip'
        other.write_bytes(data)
        try:
            linked.symlink_to(other)
        except OSError:
            # Windows without symlink privileges still exercises missing/changed.
            pass
        await self.engine.restore_filenames()
        self.engine.client.get_messages.assert_not_called()
        self.assertEqual(other.read_bytes(), data)
        self.assertEqual(changed.read_bytes(), b'changed')

    async def test_untracked_and_new_style_files_are_not_touched(self):
        source, data = self.add()
        self.engine.store.update(7, -10, 1, naming_version=2)
        untracked = self.root / '42_111_guess.zip'
        untracked.write_bytes(b'untracked')
        await self.engine.restore_filenames()
        self.assertEqual(source.read_bytes(), data)
        self.assertEqual(untracked.read_bytes(), b'untracked')
        self.engine.client.get_messages.assert_not_called()

    async def test_reopens_and_recovers_exit_before_move(self):
        source, data = self.add()
        with patch('tgzip.filename_restore.move_without_replacing', side_effect=SimulatedExit()):
            with self.assertRaises(SimulatedExit):
                await self.engine.restore_filenames()
        self.assertTrue(source.exists())
        self.assertEqual(len(self.engine.store.filename_moves(7, -10)), 1)
        self.reopen()
        self.engine.client.get_messages.reset_mock()
        await self.engine.restore_filenames()
        self.engine.client.get_messages.assert_not_called()
        self.assertEqual(Path(self.job()['path']).read_bytes(), data)
        self.assertFalse(source.exists())

    async def test_reopens_and_recovers_exit_after_move_before_database_commit(self):
        source, data = self.add(quarantine=True)
        with patch.object(self.engine.store, 'finish_filename_move', side_effect=OSError('interrupted commit')):
            with self.assertRaises(OSError):
                await self.engine.restore_filenames()
        self.assertFalse(source.exists())
        self.reopen()
        await self.engine.restore_filenames()
        self.assertEqual(Path(self.job()['quarantine_path']).read_bytes(), data)
        self.assertFalse(self.engine.store.filename_moves(7, -10))

    async def test_final_metadata_and_journal_cleanup_roll_back_together(self):
        source, data = self.add()
        self.engine.store.db.execute("""CREATE TRIGGER reject_move_cleanup BEFORE DELETE ON filename_moves
            BEGIN SELECT RAISE(ABORT, 'fixture commit failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            await self.engine.restore_filenames()
        self.assertFalse(source.exists())
        self.assertEqual(self.job()['path'], str(source))
        self.assertEqual(self.job()['naming_version'], 0)
        self.assertEqual(len(self.engine.store.filename_moves(7, -10)), 1)
        self.engine.store.db.execute('DROP TRIGGER reject_move_cleanup')
        self.reopen()
        await self.engine.restore_filenames()
        self.assertEqual(Path(self.job()['path']).read_bytes(), data)
        self.assertFalse(self.engine.store.filename_moves(7, -10))

    async def test_recovery_reconciles_link_created_before_unlink(self):
        source, data = self.add()
        def interrupted_move(src, dest):
            os.link(src, dest)
            raise OSError('interrupted unlink')
        with patch('tgzip.filename_restore.move_without_replacing', side_effect=interrupted_move):
            with self.assertRaises(OSError):
                await self.engine.restore_filenames()
        self.assertTrue(source.exists())
        self.reopen()
        await self.engine.restore_filenames()
        self.assertFalse(source.exists())
        self.assertEqual(Path(self.job()['path']).read_bytes(), data)

    async def test_recovery_never_accepts_foreign_destination_of_same_size(self):
        source, data = self.add()
        with patch('tgzip.filename_restore.move_without_replacing', side_effect=SimulatedExit()):
            with self.assertRaises(SimulatedExit):
                await self.engine.restore_filenames()
        plan = self.engine.store.filename_moves(7, -10)[0][1]
        destination = Path(plan['destination'])
        destination.write_bytes(b'X' * len(data))
        self.reopen()
        with self.assertRaises(TransferError):
            await self.engine.restore_filenames()
        self.assertEqual(source.read_bytes(), data)
        self.assertEqual(destination.read_bytes(), b'X' * len(data))
        self.assertTrue(self.engine.store.filename_moves(7, -10))

    async def test_locked_source_is_skipped_and_can_be_retried(self):
        source, data = self.add()
        with patch('tgzip.filename_restore.move_without_replacing', side_effect=PermissionError('locked')):
            await self.engine.restore_filenames()
        self.assertEqual(source.read_bytes(), data)
        self.assertFalse(self.engine.store.filename_moves(7, -10))
        self.assertEqual(self.job()['naming_version'], 0)
        await self.engine.restore_filenames()
        self.assertFalse(source.exists())

    async def test_file_replaced_during_metadata_request_is_not_renamed(self):
        source, data = self.add()
        original_stat = source.stat()
        async def replace_file(*args, **kwargs):
            replacement = source.with_suffix('.tmp')
            replacement.write_bytes(b'X' * len(data))
            os.utime(replacement, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            os.replace(replacement, source)
            return list(self.messages.values())
        self.engine.client.get_messages = replace_file
        await self.engine.restore_filenames()
        self.assertEqual(source.read_bytes(), b'X' * len(data))
        self.assertEqual(self.job()['naming_version'], 0)
        self.assertFalse(self.engine.store.filename_moves(7, -10))

    async def test_refresh_restores_filenames_in_saved_chat(self):
        self.add()
        self.engine.settings['selected_chats'] = {'7': -10}
        async def dialogs():
            yield SimpleNamespace(id=-10, name='Group', is_group=True, is_channel=False, input_entity=-10)
        self.engine.client.iter_dialogs = dialogs
        await self.engine.refresh()
        self.assertEqual(Path(self.job()['path']).name, 'Original Model.ZIP')

    async def test_stop_interrupts_metadata_fetch_and_later_retry_completes(self):
        source, data = self.add()
        entered = asyncio.Event()
        async def blocked(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        original = self.engine.client.get_messages
        self.engine.client.get_messages = blocked
        self.engine.launch('renaming', self.engine.restore_filenames)
        await entered.wait()
        await self.engine.command('stop')
        self.assertEqual(source.read_bytes(), data)
        self.assertFalse(self.engine.store.filename_moves(7, -10))
        self.engine.client.get_messages = original
        await self.engine.restore_filenames()
        self.assertEqual(Path(self.job()['path']).read_bytes(), data)

    async def test_select_chat_automatically_restores_without_start(self):
        self.add()
        await self.engine.command('select', chat=-10)
        await self.engine.task
        self.assertEqual(Path(self.job()['path']).name, 'Original Model.ZIP')

    async def test_pause_holds_renames_after_metadata_fetch(self):
        source, data = self.add()
        entered = asyncio.Event()
        release = asyncio.Event()
        async def blocked(*args, **kwargs):
            entered.set()
            await release.wait()
            return list(self.messages.values())
        self.engine.client.get_messages = blocked
        self.engine.launch('renaming', self.engine.restore_filenames)
        await entered.wait()
        await self.engine.command('pause')
        release.set()
        for _ in range(5):
            await asyncio.sleep(0)
        self.assertEqual(source.read_bytes(), data)
        await self.engine.command('resume')
        await self.engine.task
        self.assertFalse(source.exists())
