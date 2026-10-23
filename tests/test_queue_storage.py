"""Queue database folder selection and manual backups."""
import asyncio
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from tgzip.engine import Engine
from tgzip.queue_backup import backup_queue, copy_queue
from tgzip.storage import Store
from tgzip.transfer import TransferError


class QueueFolderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.data = self.root / 'appdata'
        self.data.mkdir()
        self.synced = self.root / 'Dropbox' / 'Queue'
        self.synced.mkdir(parents=True)
        self.events = []
        self.engine = self.make_engine()
        self.engine.account, self.engine.chat = 123, -100
        self.engine.store.scanned(123, -100, 1, {'document': 9, 'name': 'a.zip', 'size': 100, 'path': self.root / 'a.zip'})

    def make_engine(self):
        return Engine(self.data, lambda name, value: self.events.append((name, value)))

    async def asyncTearDown(self):
        await self.engine.cancel()
        self.engine.store.close()
        self.temp.cleanup()

    def names(self, store=None):
        return [job['name'] for job in (store or self.engine.store).jobs(123, -100)]

    async def test_copy_to_synced_folder_and_reopen_after_restart(self):
        await self.engine.command('database', path=str(self.synced))
        self.assertEqual(self.engine.settings['database_dir'], str(self.synced))
        self.assertEqual(self.engine.database_path(), self.synced / 'queue.sqlite3')
        self.engine.store.scanned(123, -100, 2, {'document': 10, 'name': 'b.zip', 'size': 100, 'path': self.root / 'b.zip'})
        self.assertEqual(self.names(), ['a.zip', 'b.zip'])
        # The previous local file is kept but no longer written.
        with closing(sqlite3.connect(self.data / 'queue.sqlite3')) as old:
            self.assertEqual(old.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 1)
        info = [value for name, value in self.events if name == 'database'][-1]
        self.assertEqual((info['custom'], info['error']), (True, None))
        self.engine.store.close()
        self.engine = self.make_engine()
        self.assertIsNone(self.engine.database_error)
        self.assertEqual(self.names(), ['a.zip', 'b.zip'])
        # Choosing the application data folder returns to the default location.
        await self.engine.command('database', path=str(self.data), mode='replace')
        self.assertNotIn('database_dir', self.engine.settings)
        self.assertEqual(self.names(), ['a.zip', 'b.zip'])
        self.assertEqual(len(list(self.data.glob('queue.sqlite3.replaced-*'))), 1)

    async def test_existing_queue_requires_a_choice_and_use_opens_it(self):
        other = Store(self.synced / 'queue.sqlite3')
        other.scanned(123, -100, 7, {'document': 70, 'name': 'synced.zip', 'size': 100, 'path': self.root / 'synced.zip'})
        other.close()
        with self.assertRaises(TransferError):
            await self.engine.command('database', path=str(self.synced))
        self.assertNotIn('database_dir', self.engine.settings)
        await self.engine.command('database', path=str(self.synced), mode='use')
        self.assertEqual(self.names(), ['synced.zip'])
        empty = self.root / 'empty'
        empty.mkdir()
        with self.assertRaises(TransferError):
            await self.engine.command('database', path=str(empty), mode='use')

    async def test_network_folders_and_running_operations_are_refused(self):
        with patch('tgzip.engine.is_network_path', return_value=True):
            with self.assertRaises(TransferError):
                await self.engine.command('database', path=str(self.synced))
        started = asyncio.Event()
        async def busy():
            started.set()
            await asyncio.sleep(60)
        self.engine.launch('scanning', busy)
        await started.wait()
        with self.assertRaises(TransferError):
            await self.engine.command('database', path=str(self.synced))
        self.assertFalse((self.synced / 'queue.sqlite3').exists())

    async def test_unavailable_folder_blocks_queue_operations_without_local_fallback(self):
        self.engine.settings['database_dir'] = str(self.root / 'Missing')
        self.engine.save_settings()
        self.engine.store.close()
        self.engine = self.make_engine()
        self.engine.account, self.engine.chat = 123, -100
        self.assertIn('unavailable', self.engine.database_error)
        self.assertEqual(self.names(), [])
        for command in ('scan', 'start', 'verify', 'backup'):
            with self.assertRaises(TransferError, msg=command):
                await self.engine.command(command)
        await self.engine.command('boot')
        self.assertIn(('error', self.engine.database_error), self.events)
        # Choosing a folder with a queue recovers without copying the empty fallback.
        with self.assertRaises(TransferError):
            await self.engine.command('database', path=str(self.data))
        await self.engine.command('database', path=str(self.data), mode='use')
        self.assertIsNone(self.engine.database_error)
        self.assertEqual(self.names(), ['a.zip'])

    async def test_backup_command_saves_checked_copy_next_to_queue(self):
        await self.engine.command('database', path=str(self.synced))
        self.engine.store.stage_progress(123, -100, 1, 40)
        await self.engine.command('backup')
        destination = Path([value for name, value in self.events if name == 'backup_done'][-1])
        self.assertEqual(destination.parent, self.synced / 'backups')
        self.assertEqual(destination.suffix, '.zip')
        with zipfile.ZipFile(destination) as archive:
            self.assertEqual(sorted(archive.namelist()), ['queue.sqlite3', 'settings.json'])
            self.assertIn(b'database_dir', archive.read('settings.json'))
            archive.extract('queue.sqlite3', self.root / 'restored')
        with closing(sqlite3.connect(self.root / 'restored' / 'queue.sqlite3')) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('SELECT name, done FROM jobs').fetchall(), [('a.zip', 40)])
        self.assertEqual([p.name for p in (self.synced / 'backups').iterdir()], [destination.name])


class BackupHelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / 'queue.sqlite3'
        self.db = sqlite3.connect(self.database)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE jobs(id INTEGER PRIMARY KEY)')
        self.db.execute('INSERT INTO jobs VALUES (1)')
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_manual_zip_backups_keep_ten_and_leave_other_snapshots(self):
        backups = self.root / 'backups'
        for name in ('update-20260101T000000000000Z-' + '0' * 32, 'manual-20260101T000000000000Z-' + '1' * 32):
            (backups / name).mkdir(parents=True)
            (backups / name / 'queue.sqlite3').write_bytes(b'x')
            (backups / name / 'settings.json').write_bytes(b'{}')
        (backups / 'personal.zip').write_bytes(b'keep')
        paths = [backup_queue(self.database, b'{}') for _ in range(12)]
        self.assertEqual([path.exists() for path in paths], [False, False] + [True] * 10)
        self.assertTrue(all(path.suffix == '.zip' for path in paths))
        self.assertEqual(len([item for item in backups.iterdir() if item.is_dir()]), 2)
        self.assertEqual((backups / 'personal.zip').read_bytes(), b'keep')
        self.assertFalse([item for item in backups.iterdir() if item.name.startswith('.pending-')])

    def test_copy_keeps_replaced_queue_with_its_wal(self):
        target_dir = self.root / 'target'
        target_dir.mkdir()
        target = target_dir / 'queue.sqlite3'
        target.write_bytes(b'old')
        (target_dir / 'queue.sqlite3-wal').write_bytes(b'old wal')
        with self.assertRaises(TransferError):
            copy_queue(self.database, target)
        self.assertEqual(target.read_bytes(), b'old')
        moved = copy_queue(self.database, target, replace=True)
        self.assertEqual(moved.read_bytes(), b'old')
        self.assertEqual(moved.with_name(moved.name + '-wal').read_bytes(), b'old wal')
        self.assertFalse((target_dir / 'queue.sqlite3-wal').exists())
        with closing(sqlite3.connect(target)) as copy:
            self.assertEqual(copy.execute('SELECT id FROM jobs').fetchall(), [(1,)])
        self.assertEqual(sorted(p.name for p in target_dir.iterdir() if p.name.endswith('.tmp')), [])


if __name__ == '__main__':
    unittest.main()


class DailyBackupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        with closing(sqlite3.connect(self.root / 'queue.sqlite3')) as db:
            db.execute('CREATE TABLE jobs(id INTEGER PRIMARY KEY)')
            db.execute('INSERT INTO jobs VALUES (7)')
            db.commit()
        self.auto = self.root / 'backups' / 'auto'

    def tearDown(self):
        self.temp.cleanup()

    def test_one_backup_per_day_in_auto_folder_and_sixty_kept(self):
        from datetime import datetime, timedelta
        from tgzip.queue_backup import daily_backup
        first = daily_backup(self.root / 'queue.sqlite3', b'{}', datetime(2026, 10, 23, 9))
        self.assertEqual(first.parent, self.auto)
        self.assertTrue(first.name.startswith('auto-20261023-'))
        with zipfile.ZipFile(first) as archive:
            self.assertEqual(sorted(archive.namelist()), ['queue.sqlite3', 'settings.json'])
        self.assertIsNone(daily_backup(self.root / 'queue.sqlite3', b'{}', datetime(2026, 10, 23, 23)))
        manual = self.root / 'backups' / ('manual-20260101T000000000000Z-' + '0' * 32 + '.zip')
        manual.write_bytes(b'manual')
        (self.auto / 'notes.txt').write_text('keep')
        start = datetime(2026, 10, 24)
        for day in range(65):
            daily_backup(self.root / 'queue.sqlite3', b'{}', start + timedelta(days=day))
        names = sorted(item.name for item in self.auto.iterdir() if item.suffix == '.zip')
        self.assertEqual(len(names), 60)
        # 66 days were backed up; the six oldest (23–28 October) were pruned.
        self.assertTrue(names[0].startswith('auto-20261029-'))
        self.assertTrue(manual.exists())
        self.assertTrue((self.auto / 'notes.txt').exists())
        self.assertFalse([item for item in self.auto.iterdir() if item.name.startswith('.pending-')])

    async def test_boot_starts_backup_in_background_and_close_waits_for_it(self):
        import threading
        events = []
        self.root = self.root / 'appdata'
        self.root.mkdir()
        self.auto = self.root / 'backups' / 'auto'
        engine = Engine(self.root, lambda name, value: events.append((name, value)))
        main_thread = threading.get_ident()
        threads = []
        original = __import__('tgzip.queue_backup', fromlist=['daily_backup']).daily_backup
        def slow_backup(*args):
            threads.append(threading.get_ident())
            import time
            time.sleep(.2)
            return original(*args)
        loop = asyncio.get_running_loop()
        # Close normally stops the worker loop; keep the test's loop running.
        with patch('tgzip.engine.daily_backup', side_effect=slow_backup), patch.object(loop, 'stop'):
            await engine.command('boot')
            # Boot returns before the backup finishes.
            self.assertFalse(engine.backup_task.done())
            await engine.command('close')
        self.assertTrue(engine.backup_task.done())
        self.assertNotEqual(threads, [main_thread])
        self.assertEqual(len(list(self.auto.glob('auto-*.zip'))), 1)
        self.assertTrue(any(name == 'log' and value.startswith('Daily backup saved') for name, value in events))
        # A second start on the same day does not create another backup.
        engine = Engine(self.root, lambda name, value: events.append((name, value)))
        await engine.command('boot')
        await engine.backup_task
        self.assertEqual(len(list(self.auto.glob('auto-*.zip'))), 1)
        engine.store.close()
