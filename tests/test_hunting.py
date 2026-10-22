"""Global hunting rules: name filter, files known from any chat, pinned chats and folder layout."""
import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tgzip.engine import Engine
from tgzip.storage import DEFAULT_NAME_FILTER, SKIP_FILTER, SKIP_KNOWN, name_matcher
from tgzip.transfer import TransferError


def dialog(id, name):
    return SimpleNamespace(id=id, name=name, is_group=True, is_channel=False, input_entity=id)


class NameMatcherTests(unittest.TestCase):
    def test_default_pattern_matches_generated_uploader_names(self):
        matches = name_matcher({})
        self.assertEqual({}.get('name_filter', DEFAULT_NAME_FILTER), DEFAULT_NAME_FILTER)
        for name in ('7407604.67a7dac35b142.rar', '1.a.zip', 'abc123.0f9.rar'):
            self.assertTrue(matches(name), name)
        for name in ('Model.rar', '7407604.67A7dac35b142.rar', '7407604.67a7dac35b142.RAR', '7407604_67a7.rar',
                     '7407604.67a7dac35b142.7z', '7407604.67a7.dac.rar', 'x.7407604.67a7dac35b142.rar.exe'):
            self.assertFalse(matches(name), name)

    def test_text_mode_disabled_filter_and_invalid_regex(self):
        matches = name_matcher({'name_filter_mode': 'text', 'name_filter': 'Pack'})
        self.assertTrue(matches('big.PACK.v2.rar'))
        self.assertFalse(matches('model.rar'))
        self.assertIsNone(name_matcher({'name_filter_enabled': False, 'name_filter': '('}))
        with self.assertRaises(ValueError):
            name_matcher({'name_filter': '(unclosed'})


class HuntingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.events = []
        self.engine = Engine(self.root, lambda name, value: self.events.append((name, value)))
        self.engine.account = 123
        self.engine.chat = -100
        self.engine.dialogs = {-100: dialog(-100, 'Hunt A'), -200: dialog(-200, 'Hunt B')}
        self.downloads = self.root / 'files'
        self.engine.settings['download_dir'] = str(self.downloads)
        self.fetched = []

    async def asyncTearDown(self):
        await self.engine.cancel()
        self.engine.store.close()
        self.temp.cleanup()

    def add(self, chat, message, name, document=None, state='queued', size=100):
        self.engine.store.scanned(123, chat, message, {
            'document': document or message, 'name': name, 'size': size,
            'path': self.downloads / name, 'naming_version': 2})
        if state != 'queued':
            self.engine.store.update(123, chat, message, state=state, done=size if state == 'done' else 0)

    async def download(self):
        async def get_messages(entity, ids):
            return SimpleNamespace(document=SimpleNamespace(id=int(self.job(-100, ids)['document']), size=100),
                                   file=SimpleNamespace(name=self.job(-100, ids)['name']), media=ids)
        async def transfer(client, message, job, gate, progress, *, limiter=None):
            self.fetched.append(job['message'])
            progress(job['size'], 0)
        self.engine.client = SimpleNamespace(get_messages=get_messages)
        with patch.object(self.engine, 'checker', return_value=None), patch('tgzip.engine.download', transfer):
            await asyncio.wait_for(self.engine.download_queue(), 5)

    def job(self, chat, message):
        return next(job for job in self.engine.store.jobs(123, chat) if job['message'] == message)

    async def test_download_skips_filtered_and_files_known_from_any_chat(self):
        self.add(-200, 1, '1111.aaaa.rar', document=501, state='done')
        self.add(-200, 2, '2222.bbbb.zip', document=502, state='done')
        self.engine.store.update(123, -200, 2, verification='quarantined')
        self.add(-100, 10, '1111.AAAA.rar', document=901)   # same name, any case, other chat
        self.add(-100, 11, '3333.cccc.zip', document=501)   # same Telegram file, renamed
        self.add(-100, 12, 'Readable name.rar')              # does not match the default filter
        self.add(-100, 13, '2222.bbbb.zip', document=902)   # only a quarantined copy exists
        self.add(-100, 14, '4444.dddd.rar', document=903)
        self.add(-100, 15, '4444.dddd.rar', document=904)   # duplicate inside this pass
        await self.download()
        self.assertEqual(self.fetched, [13, 14])
        states = {job['message']: (job['state'], job['error']) for job in self.engine.store.jobs(123, -100)}
        self.assertEqual(states[10], ('skipped', SKIP_KNOWN))
        self.assertEqual(states[11], ('skipped', SKIP_KNOWN))
        self.assertEqual(states[12], ('skipped', SKIP_FILTER))
        self.assertEqual(states[15], ('skipped', SKIP_KNOWN))
        self.assertEqual(states[13][0], 'done')
        summary = self.engine.store.summary(123, -100)
        self.assertEqual((summary['skipped'], summary['complete']), (4, 2))
        page = self.engine.store.queue_page(123, -100, 'skipped')
        self.assertEqual([row['message'] for row in page['rows']], [10, 11, 12, 15])
        self.assertEqual(self.engine.store.queue_page(123, -100, 'unfinished')['matching'], 0)
        self.assertTrue(any(name == 'log' and 'skip 2 already downloaded and 1 not matching' in value
                            for name, value in self.events))

    async def test_relaxed_rules_restore_skipped_files_for_next_pass(self):
        self.add(-200, 1, 'pack.rar', document=501, state='done')
        self.add(-100, 10, 'pack.rar', document=901)
        self.add(-100, 11, 'Readable name.rar')
        await self.download()
        self.assertEqual(self.fetched, [])
        await self.engine.command('hunting', name_filter_enabled=False, skip_known=False)
        self.assertEqual([job['state'] for job in self.engine.store.jobs(123, -100)], ['queued', 'queued'])
        self.assertTrue(self.engine.settings['name_filter_enabled'] is False)
        await self.download()
        self.assertEqual(self.fetched, [10, 11])

    async def test_text_filter_and_invalid_regex_command(self):
        self.add(-100, 10, 'Big Pack v2.rar')
        self.add(-100, 11, 'other.rar')
        await self.engine.command('hunting', name_filter_mode='text', name_filter='pack')
        self.assertEqual([job['state'] for job in self.engine.store.jobs(123, -100)], ['queued', 'skipped'])
        with self.assertRaises(TransferError):
            await self.engine.command('hunting', name_filter_mode='regex', name_filter='(')
        self.assertEqual(self.engine.settings['name_filter_mode'], 'text')
        with self.assertRaises(ValueError):
            await self.engine.command('hunting', name_filter_mode='glob')

    async def test_rule_change_during_download_skips_waiting_files(self):
        self.engine.settings.update(name_filter_enabled=False, parallel_downloads=1)
        for message in range(1, 4):
            self.add(-100, message, f'{message}.zip')
        release = asyncio.Event()
        started = asyncio.Event()
        async def get_messages(entity, ids):
            return SimpleNamespace(document=SimpleNamespace(id=ids, size=100), file=SimpleNamespace(name=f'{ids}.zip'))
        async def transfer(client, message, job, gate, progress, *, limiter=None):
            self.fetched.append(job['message'])
            started.set()
            await release.wait()
            progress(job['size'], 0)
        self.engine.client = SimpleNamespace(get_messages=get_messages)
        with patch.object(self.engine, 'checker', return_value=None), patch('tgzip.engine.download', transfer):
            self.engine.launch('downloading', self.engine.download_queue)
            await asyncio.wait_for(started.wait(), 2)
            await self.engine.command('hunting', name_filter_enabled=True, name_filter_mode='text', name_filter='1.')
            self.assertEqual(self.job(-100, 2)['state'], 'skipped')
            release.set()
            await asyncio.wait_for(self.engine.task, 2)
        self.assertEqual(self.fetched, [1])
        self.assertEqual(self.job(-100, 1)['state'], 'done')

    async def test_hunt_scans_selected_pinned_chat_then_downloads_matches(self):
        self.engine.chat = -200
        messages = [SimpleNamespace(id=i, document=SimpleNamespace(id=700 + i, size=100, mime_type='application/zip'),
                                    file=SimpleNamespace(name=name), media=i)
                    for i, name in enumerate(['7407604.67a7dac35b142.rar', 'Readable.rar', '1.b2.zip'], 1)]
        async def iter_messages(entity, *, min_id, reverse):
            for message in messages:
                if message.id > min_id:
                    yield message
        async def get_messages(entity, ids):
            return messages[ids - 1]
        async def transfer(client, message, job, gate, progress, *, limiter=None):
            self.fetched.append(job['message'])
            progress(job['size'], 0)
        self.engine.client = SimpleNamespace(iter_messages=iter_messages, get_messages=get_messages)
        await self.engine.command('pin', chat=-100)
        await self.engine.command('pin', chat=-200)
        await self.engine.command('pin', chat=-100)
        self.assertEqual(self.engine.settings['pinned_chats'], {'123': [-100, -200]})
        with patch.object(self.engine, 'checker', return_value=None), patch('tgzip.engine.download', transfer):
            await self.engine.command('hunt', chat=-100)
            await asyncio.wait_for(self.engine.task, 2)
        self.assertEqual(self.engine.chat, -100)
        self.assertEqual(self.engine.settings['selected_chats'], {'123': -100})
        self.assertEqual(self.fetched, [1, 3])
        jobs = self.engine.store.jobs(123, -100)
        self.assertEqual([job['state'] for job in jobs], ['done', 'skipped', 'done'])
        # All chats share the download folder unless Folder per chat is enabled.
        self.assertEqual({Path(job['path']).parent for job in jobs}, {self.downloads.resolve()})
        states = [value for name, value in self.events if name == 'state']
        self.assertEqual(states[states.index('scanning'):], ['scanning', 'downloading', 'idle'])
        await self.engine.command('pin', chat=-100, pinned=False)
        self.assertEqual(self.engine.settings['pinned_chats'], {'123': [-200]})

    async def test_folder_per_chat_and_unstarted_files_follow_current_layout(self):
        await self.engine.command('hunting', folder_per_chat=True)
        self.assertEqual(self.engine.target_folder(123, -100), self.downloads.resolve() / 'account_123' / 'chat_-100')
        old = self.root / 'old' / 'account_123' / 'chat_-100'
        old.mkdir(parents=True)
        for message, name in ((1, 'a.zip'), (2, 'b.zip'), (3, 'c.zip')):
            self.engine.store.scanned(123, -100, message, {'document': message, 'name': name, 'size': 100,
                                                           'path': old / name, 'naming_version': 2})
        (old / 'b.zip.part').write_bytes(b'x' * 10)
        self.engine.store.update(123, -100, 3, done=10)
        await self.engine.command('hunting', folder_per_chat=False)
        filenames = self.engine.store.filename_allocator()
        jobs = self.engine.store.jobs(123, -100)
        for job in jobs:
            self.engine.prepare_filename(job, SimpleNamespace(file=SimpleNamespace(name=job['name'])), filenames)
        paths = [Path(job['path']) for job in self.engine.store.jobs(123, -100)]
        self.assertEqual(paths, [self.downloads.resolve() / 'a.zip', old / 'b.zip', old / 'c.zip'])


if __name__ == '__main__':
    unittest.main()
