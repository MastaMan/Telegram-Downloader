"""Update backups preserve WAL commits and settings without touching originals."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from tgzip.queue_backup import backup_before_update
from updater import UpdateCancelled, UpdateError


class QueueBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = sqlite3.connect(self.root / 'queue.sqlite3')
        self.source.execute('PRAGMA journal_mode=WAL')
        self.source.execute('PRAGMA wal_autocheckpoint=0')
        self.source.execute('CREATE TABLE jobs(id INTEGER PRIMARY KEY, state TEXT, done INTEGER)')
        self.source.execute("INSERT INTO jobs VALUES(1,'done',100)")
        self.source.execute("INSERT INTO jobs VALUES(2,'queued',60)")
        self.source.commit()
        self.settings = b'{"selected_chats":{"123":-1001234567890},"parallel_downloads":5}'
        self.cancel = threading.Event()

    def tearDown(self):
        self.source.close()
        self.temp.cleanup()

    def backup(self, report=lambda _: None):
        return backup_before_update(self.root, self.settings, self.cancel, report)

    def test_wal_snapshot_is_standalone_and_preserves_settings(self):
        self.assertTrue((self.root / 'queue.sqlite3-wal').exists())
        result = self.backup()
        with closing(sqlite3.connect(result / 'queue.sqlite3')) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('SELECT * FROM jobs ORDER BY id').fetchall(),
                             [(1, 'done', 100), (2, 'queued', 60)])
        self.assertEqual((result / 'settings.json').read_bytes(), self.settings)
        self.assertEqual(json.loads(self.settings)['selected_chats']['123'], -1001234567890)
        self.assertEqual(self.source.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 2)
        self.assertEqual(sorted(p.name for p in result.iterdir()), ['queue.sqlite3', 'settings.json'])

    def test_retains_ten_complete_snapshots_and_leaves_unrelated_files(self):
        backups = self.root / 'backups'
        backups.mkdir()
        (backups / 'personal.sqlite3').write_bytes(b'keep')
        (backups / 'manual').mkdir()
        paths = [self.backup() for _ in range(12)]
        self.assertFalse(paths[0].exists())
        self.assertFalse(paths[1].exists())
        self.assertTrue(all(p.exists() for p in paths[2:]))
        self.assertEqual(len(list(backups.glob('update-*'))), 10)
        self.assertEqual((backups / 'personal.sqlite3').read_bytes(), b'keep')
        self.assertTrue((backups / 'manual').is_dir())

    def test_failure_keeps_prior_backups_and_cleans_partial_snapshot(self):
        saved = self.backup()
        with patch('tgzip.queue_backup.os.fsync', side_effect=OSError('disk full')):
            with self.assertRaises(UpdateError):
                self.backup()
        self.assertEqual(list((self.root / 'backups').iterdir()), [saved])
        self.assertEqual(self.source.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 2)

    def test_missing_source_does_not_create_empty_database(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(UpdateError):
                backup_before_update(Path(directory), b'{}', self.cancel, lambda _: None)
            self.assertFalse((Path(directory) / 'queue.sqlite3').exists())

    def test_corrupt_source_is_not_published_or_pruned(self):
        saved = self.backup()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'queue.sqlite3').write_bytes(b'broken database')
            with self.assertRaises(UpdateError):
                backup_before_update(root, b'{}', self.cancel, lambda _: None)
            self.assertEqual(list((root / 'backups').iterdir()), [])
        self.assertTrue(saved.exists())

    def test_cancel_during_copy_preserves_previous_snapshot(self):
        saved = self.backup()
        def report(_):
            self.cancel.set()
        with self.assertRaises(UpdateCancelled):
            self.backup(report)
        self.assertEqual(list((self.root / 'backups').iterdir()), [saved])
