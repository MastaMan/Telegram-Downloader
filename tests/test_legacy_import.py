"""Adopting archives saved by the earlier scripts (group_<id> folders, name__msg<id>.ext)."""
import asyncio
from pathlib import Path
import tempfile
import unittest

from tgzip.engine import Engine
from tgzip.legacy_import import adopt, find_old_files, original_name
from tgzip.storage import FilenameAllocator, SKIP_KNOWN
from tgzip.transfer import TransferError


class LegacyNameTests(unittest.TestCase):
    def test_original_name_strips_only_the_old_suffix(self):
        self.assertEqual(original_name('3800029.619b0e4d1b26c__msg89.zip'), '3800029.619b0e4d1b26c.zip')
        self.assertEqual(original_name('Model__msg1__msg12.RAR'), 'Model__msg1.RAR')
        for name in ('Model.zip', 'Model_msg89.zip', 'Model__msg.zip', 'Model__msg89.zip.part', '__dup_2__Model.zip'):
            self.assertEqual(original_name(name), name)


class LegacyImportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.target = self.root / 'telegram zips'
        self.old = self.target / 'group_1234567'
        self.old.mkdir(parents=True)
        self.events = []
        (self.root / 'appdata').mkdir()
        self.engine = Engine(self.root / 'appdata', lambda name, value: self.events.append((name, value)))
        self.engine.settings['download_dir'] = str(self.target)
        self.engine.account, self.engine.chat = 123, -100

    async def asyncTearDown(self):
        await self.engine.cancel()
        self.engine.store.close()
        self.temp.cleanup()

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_candidates_skip_partial_tracked_quarantined_and_unrelated_files(self):
        old = self.write(self.old / '1.a__msg5.zip', b'a')
        plain_in_group = self.write(self.old / 'Plain.rar', b'b')
        suffixed_elsewhere = self.write(self.root / 'other' / '2.b__msg6.rar', b'c')
        self.write(self.old / '3.c__msg7.zip.part', b'partial')
        self.write(self.old / 'wrong_archives' / '4.d__msg8.zip', b'bad')
        self.write(self.root / 'other' / 'Unrelated.zip', b'mine')
        tracked = self.write(self.old / '5.e__msg9.zip', b'queue')
        found = find_old_files(self.root, {FilenameAllocator.key(tracked)})
        self.assertEqual(set(found), {old, plain_in_group, suffixed_elsewhere})

    def test_adopt_never_overwrites(self):
        allocator = FilenameAllocator([])
        same = self.write(self.old / '1.a__msg5.zip', b'same')
        self.write(self.target / '1.a.zip', b'same')
        different = self.write(self.old / '2.b__msg6.zip', b'new data')
        self.write(self.target / '2.b.zip', b'other')
        self.assertEqual(adopt(same, self.target, allocator)[:2], (self.target / '1.a.zip', 'duplicate'))
        self.assertTrue(same.exists())
        destination, status, name, size = adopt(different, self.target, allocator)
        self.assertEqual((destination.name, status, name, size), ('__dup_2__2.b.zip', 'moved', '2.b.zip', 8))
        self.assertEqual((self.target / '2.b.zip').read_bytes(), b'other')
        self.assertFalse(different.exists())
        in_place = self.write(self.target / '3.c.zip', b'x')
        self.assertEqual(adopt(in_place, self.target, allocator)[:2], (in_place, 'kept'))

    async def test_import_moves_records_journals_and_skips_future_downloads(self):
        self.write(self.old / '3800029.619b0e4d1b26c__msg89.zip', b'archive')
        self.write(self.old / '3724523.616e8a08674c9__msg197.zip.part', b'partial')
        self.write(self.target / '2435171.5cd011430e865__msg91.zip', b'root copy')
        other_group = self.root / 'telegram zips' / 'group_555'
        self.write(other_group / 'Readable name.rar', b'r')
        for message, name in ((1, '3800029.619b0e4d1b26c.zip'), (2, 'Readable name.rar'), (3, '1.new.zip')):
            self.engine.store.scanned(123, -100, message, {'document': message, 'name': name, 'size': 7,
                                                           'path': self.target / name, 'naming_version': 2})
        await self.engine.command('import_old', path=str(self.target))
        await asyncio.wait_for(self.engine.task, 5)
        self.assertEqual((self.target / '3800029.619b0e4d1b26c.zip').read_bytes(), b'archive')
        self.assertEqual((self.target / '2435171.5cd011430e865.zip').read_bytes(), b'root copy')
        self.assertTrue((self.target / 'Readable name.rar').exists())
        # The partial file keeps its folder; the emptied group folder is removed.
        self.assertTrue((self.old / '3724523.616e8a08674c9__msg197.zip.part').exists())
        self.assertFalse(other_group.exists())
        names, _ = self.engine.store.known_files()
        self.assertTrue({'3800029.619b0e4d1b26c.zip', '2435171.5cd011430e865.zip', 'readable name.rar'} <= names)
        states = {job['message']: (job['state'], job['error']) for job in self.engine.store.jobs(123, -100)}
        self.assertEqual(states[1], ('skipped', SKIP_KNOWN))
        self.assertEqual(states[2], ('skipped', SKIP_KNOWN))
        self.assertEqual(states[3][0], 'queued')
        journals = list((self.root / 'appdata' / 'imports').glob('import-*.tsv'))
        self.assertEqual(len(journals), 1)
        lines = journals[0].read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(lines), 4)
        self.assertTrue(all(line.startswith('moved\t') for line in lines[1:]))
        self.assertTrue(any(name == 'log' and value.startswith('Imported old files: 3 renamed and moved')
                            for name, value in self.events))
        # A second import finds nothing new and changes nothing.
        await self.engine.command('import_old', path=str(self.target))
        await asyncio.wait_for(self.engine.task, 5)
        self.assertTrue(any(name == 'log' and value.startswith('Imported old files: 0 renamed')
                            for name, value in self.events))

    async def test_import_requires_download_folder_and_existing_source(self):
        with self.assertRaises(TransferError):
            await self.engine.command('import_old', path=str(self.root / 'missing'))
        self.engine.settings.pop('download_dir')
        with self.assertRaises(TransferError):
            await self.engine.command('import_old', path=str(self.target))


if __name__ == '__main__':
    unittest.main()
