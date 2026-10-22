import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from telethon import errors
from tgzip.engine import Engine, error_text


class MemoryVault:
    def __init__(self):
        self.saved = {}

    def save(self, values):
        self.saved = dict(values)

    def load(self):
        return dict(self.saved)


def dialog(id, name, group=True):
    return SimpleNamespace(id=id, name=name, is_group=group, is_channel=not group, input_entity=id)


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.events = []
        self.engine = Engine(self.root, lambda name, value: self.events.append((name, value)))
        self.engine.vault = MemoryVault()
        self.engine.account = 123
        self.engine.dialogs = {-100: dialog(-100, 'Group'), -200: dialog(-200, 'Channel', False)}
        self.engine.settings['download_dir'] = str(self.root / 'files')
        # Global hunting rules have dedicated tests; other tests download every fixture.
        self.engine.settings.update(name_filter_enabled=False, skip_known=False)

    async def asyncTearDown(self):
        await self.engine.cancel()
        self.engine.store.close()
        self.temp.cleanup()

    async def test_real_telethon_client_initializes_on_running_loop(self):
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        client = TelegramClient(StringSession(), 123, 'a' * 32)
        self.assertIs(client.loop, asyncio.get_running_loop())
        self.assertFalse(client.is_connected())
        await client.disconnect()

    async def test_queue_view_changes_while_paused_without_canceling_work(self):
        engine = self.engine
        engine.chat = -100
        with engine.store.db:
            engine.store.db.executemany(
                "INSERT INTO jobs(account,chat,message,document,name,size,path,state,done) VALUES (123,-100,?,'1','a.zip',100,'a.zip',?,?)",
                ((i, 'done' if i <= 205 else 'queued', 100 if i <= 205 else 0) for i in range(1, 411)))
        stopped = asyncio.Event()
        async def active_work():
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        engine.launch('downloading', active_work)
        await asyncio.sleep(0)
        await engine.command('pause')
        await engine.command('queue_view', mode='unfinished', page=1, request=1)
        value = [value for name, value in self.events if name == 'queue'][-1]
        self.assertEqual((value['page'], value['pages'], value['matching'], value['request']), (1, 2, 205, 1))
        self.assertEqual(len(value['rows']), 5)
        self.assertFalse(engine.gate.is_set())
        self.assertFalse(stopped.is_set())
        await engine.command('queue_view', mode='downloaded', page=0, request=2)
        value = [value for name, value in self.events if name == 'queue'][-1]
        self.assertTrue(all(row['state'] == 'done' for row in value['rows']))
        self.assertEqual(value['summary']['total'], 410)
        self.assertEqual(value['chat'], -100)
        await engine.command('select', chat=-200)
        self.assertTrue(stopped.is_set())
        value = [value for name, value in self.events if name == 'queue'][-1]
        self.assertEqual((value['page'], value['pages'], value['matching']), (0, 1, 0))
        self.assertEqual(value['mode'], 'downloaded')

    async def test_switch_chat_cancels_active_work_and_saves_selection(self):
        self.engine.chat = -100
        stopped = asyncio.Event()
        async def running():
            try:
                await asyncio.sleep(60)
            finally:
                stopped.set()
        self.engine.launch('downloading', running)
        await asyncio.sleep(0)
        await self.engine.command('select', chat=-200)
        self.assertTrue(stopped.is_set())
        self.assertEqual(self.engine.chat, -200)
        self.assertEqual(json.loads((self.root / 'settings.json').read_text())['selected_chats']['123'], -200)

    async def test_refresh_restores_last_chat_with_groups_and_channels(self):
        entries = list(self.engine.dialogs.values())
        async def iter_dialogs():
            for item in entries:
                yield item
        self.engine.client = SimpleNamespace(iter_dialogs=iter_dialogs)
        self.engine.settings['selected_chats'] = {'123': -200}
        await self.engine.refresh()
        self.assertEqual(self.engine.chat, -200)
        rows = next(value for event, value in self.events if event == 'dialogs')
        self.assertEqual({row['kind'] for row in rows}, {'Group', 'Channel'})

    async def test_missing_saved_chat_never_silently_switches(self):
        async def iter_dialogs():
            yield dialog(-100, 'Only accessible chat')
        self.engine.client = SimpleNamespace(iter_dialogs=iter_dialogs)
        self.engine.settings['selected_chats'] = {'123': -999}
        await self.engine.refresh()
        self.assertIsNone(self.engine.chat)

    async def test_incremental_scan_filters_zip_and_rar_and_resumes_cursor(self):
        self.engine.chat = -100
        seen_min_ids = []
        def message(id, name, mime='application/octet-stream'):
            return SimpleNamespace(id=id, document=SimpleNamespace(id=900 + id, size=100, mime_type=mime), file=SimpleNamespace(name=name))
        messages = [message(1, 'one.ZIP'), message(2, 'not.zip.exe'), message(3, 'two.zip'), message(4, 'pack.RAR'), message(5, None, 'application/vnd.rar'), message(6, 'pack.rar.exe')]
        async def iter_messages(entity, *, min_id, reverse):
            self.assertTrue(reverse)
            seen_min_ids.append(min_id)
            for msg in messages:
                if msg.id > min_id:
                    yield msg
        self.engine.client = SimpleNamespace(iter_messages=iter_messages)
        await self.engine.scan()
        await self.engine.scan()
        self.assertEqual(seen_min_ids, [0, 6])
        jobs = self.engine.store.jobs(123, -100)
        self.assertEqual([job['message'] for job in jobs], [1, 3, 4, 5])
        self.assertEqual([Path(job['path']).name for job in jobs],
                         ['one.ZIP', 'two.zip', 'pack.RAR', 'archive_5.rar'])
        self.assertNotEqual(jobs[0]['path'], jobs[1]['path'])
        self.assertTrue(jobs[-1]['path'].endswith('.rar'))

    async def test_twenty_identical_names_download_to_distinct_files(self):
        self.engine.chat = -100
        messages = [SimpleNamespace(id=i, document=SimpleNamespace(id=i, size=100, mime_type='application/zip'),
                                    file=SimpleNamespace(name='Model.ZIP'), media=i) for i in range(1, 21)]
        async def iter_messages(*args, **kwargs):
            for message in messages:
                yield message
        async def get_messages(entity, ids):
            return messages[ids - 1]
        class Stream:
            def __init__(stream, number):
                stream.number = number
            def __aiter__(stream):
                return stream
            async def __anext__(stream):
                await asyncio.sleep(.001)
                return bytes([stream.number]) * 100
            async def close(stream):
                pass
        self.engine.client = SimpleNamespace(iter_messages=iter_messages, get_messages=get_messages,
                                              iter_download=lambda media, **kwargs: Stream(media))
        await self.engine.scan()
        # A repeated full scan must not change assignments or reserve phantom names.
        self.engine.store.db.execute('UPDATE scans SET cursor=0')
        self.engine.store.db.commit()
        await self.engine.scan()
        with patch.object(self.engine, 'checker', return_value=None):
            await self.engine.download_queue()
        jobs = self.engine.store.jobs(123, -100)
        self.assertEqual([Path(job['path']).name for job in jobs],
                         ['Model.ZIP'] + [f'__dup_{i}__Model.ZIP' for i in range(2, 21)])
        for job in jobs:
            self.assertEqual(job['state'], 'done')
            self.assertEqual(Path(job['path']).read_bytes(), bytes([job['message']]) * 100)

    async def test_file_added_after_scan_is_preserved_even_with_same_size(self):
        self.queue_files(1)
        self.engine.store.update(123, -100, 1, naming_version=1)
        original = self.root / '1.zip'
        original.write_bytes(b'X' * 100)
        async def transfer(client, message, job, gate, progress, *, limiter=None):
            self.assertEqual(Path(job['path']).name, '__dup_2__1.zip')
            Path(job['path']).write_bytes(b'Y' * 100)
        with patch.object(self.engine, 'checker', return_value=None), patch('tgzip.engine.download', transfer):
            await self.engine.download_queue()
        self.assertEqual(original.read_bytes(), b'X' * 100)
        self.assertEqual((self.root / '__dup_2__1.zip').read_bytes(), b'Y' * 100)

    async def test_legacy_partial_migration_survives_exit_before_transfer(self):
        from tgzip.storage import CHUNK, Store
        self.engine.chat = -100
        original = 'Original Model ' + 'x' * 100 + '.RAR'
        old_name = 'Original Model ' + 'x' * 70 + '.rar'
        old = self.root / f'10_111_{old_name}'
        part = old.with_name(old.name + '.part')
        part.write_bytes(b'x' * CHUNK)
        self.engine.store.scanned(123, -100, 10, {
            'document': 111, 'name': old_name, 'size': CHUNK * 2, 'path': old})
        job = self.engine.store.jobs(123, -100)[0]
        message = SimpleNamespace(file=SimpleNamespace(name=original))
        self.engine.prepare_filename(job, message, self.engine.store.filename_allocator())
        self.assertEqual(Path(job['path']).name, original)
        self.assertEqual(job['partial_path'], str(part))
        self.assertEqual(part.stat().st_size, CHUNK)
        self.engine.store.close()
        self.engine.store = Store(self.root / 'queue.sqlite3')
        reopened = self.engine.store.jobs(123, -100)[0]
        self.engine.prepare_filename(reopened, message, self.engine.store.filename_allocator())
        self.assertEqual(reopened, job)
        self.assertEqual(part.stat().st_size, CHUNK)
        offsets = []
        class Stream:
            def __aiter__(stream):
                return stream
            async def __anext__(stream):
                return b'y' * CHUNK
            async def close(stream):
                pass
        def iter_download(media, offset, **kwargs):
            offsets.append(offset)
            return Stream()
        async def get_messages(*args, **kwargs):
            return SimpleNamespace(document=SimpleNamespace(id=111, size=CHUNK * 2),
                                   file=SimpleNamespace(name=original), media=111)
        self.engine.client = SimpleNamespace(get_messages=get_messages, iter_download=iter_download)
        with patch.object(self.engine, 'checker', return_value=None):
            await self.engine.download_queue()
        self.assertEqual(offsets, [CHUNK])
        self.assertEqual((self.root / original).read_bytes(), b'x' * CHUNK + b'y' * CHUNK)
        self.assertFalse(part.exists())
        self.assertEqual(self.engine.store.jobs(123, -100)[0]['state'], 'done')

    async def test_legacy_unstarted_queue_uses_original_and_completed_files_stay(self):
        self.engine.chat = -100
        for number in (1, 2):
            old = self.root / f'{number}_{number}_model.rar'
            self.engine.store.scanned(123, -100, number, {
                'document': number, 'name': 'model.rar', 'size': 100, 'path': old})
            if number == 2:
                old.write_bytes(b'completed')
                self.engine.store.update(123, -100, number, state='done')
        job = self.engine.store.jobs(123, -100)[0]
        self.engine.prepare_filename(job, SimpleNamespace(file=SimpleNamespace(name='model.RAR')),
                                     self.engine.store.filename_allocator())
        self.assertEqual(Path(job['path']).name, 'model.RAR')
        completed = self.engine.store.jobs(123, -100)[1]
        self.assertEqual(Path(completed['path']).name, '2_2_model.rar')
        self.assertEqual(Path(completed['path']).read_bytes(), b'completed')

    async def test_cancel_scan_keeps_checkpoint(self):
        self.engine.chat = -100
        first = asyncio.Event()
        async def iter_messages(entity, **kwargs):
            yield SimpleNamespace(id=15, document=None, file=None)
            first.set()
            await asyncio.sleep(60)
        self.engine.client = SimpleNamespace(iter_messages=iter_messages)
        self.engine.launch('scanning', self.engine.scan)
        await first.wait()
        await self.engine.cancel()
        self.assertEqual(self.engine.store.cursor(123, -100), 15)

    async def test_edited_attachment_never_appends_to_old_partial(self):
        self.engine.chat = -100
        path = self.root / 'file.zip'
        part = self.root / 'file.zip.part'
        part.write_bytes(b'preserved')
        self.engine.store.scanned(123, -100, 10, {'document': 111, 'name': 'file.zip', 'size': 100, 'path': path})
        async def get_messages(*args, **kwargs):
            return SimpleNamespace(document=SimpleNamespace(id=222, size=100))
        self.engine.client = SimpleNamespace(get_messages=get_messages)
        await self.engine.download_queue()
        self.assertEqual(part.read_bytes(), b'preserved')
        self.assertEqual(self.engine.store.jobs(123, -100)[0]['state'], 'error')

    async def test_expired_file_reference_is_refetched(self):
        self.engine.chat = -100
        self.engine.store.scanned(123, -100, 10, {'document': 111, 'name': 'file.zip', 'size': 100, 'path': self.root / 'file.zip'})
        calls = []
        async def get_messages(*args, **kwargs):
            calls.append('message')
            return SimpleNamespace(document=SimpleNamespace(id=111, size=100))
        async def transfer(*args, **kwargs):
            if calls.count('message') == 1:
                raise errors.FileReferenceExpiredError(request=None)
        async def no_wait(*args):
            pass
        self.engine.client = SimpleNamespace(get_messages=get_messages)
        with patch('tgzip.engine.download', transfer), patch('tgzip.engine.asyncio.sleep', no_wait):
            await self.engine.download_queue()
        self.assertEqual(calls.count('message'), 2)
        self.assertEqual(self.engine.store.jobs(123, -100)[0]['state'], 'done')

    async def test_server_requested_waits_never_fail_the_file(self):
        self.engine.chat = -100
        self.engine.store.scanned(123, -100, 10, {'document': 111, 'name': 'file.zip', 'size': 100, 'path': self.root / 'file.zip'})
        async def get_messages(*args, **kwargs):
            return SimpleNamespace(document=SimpleNamespace(id=111, size=100))
        # More waits than the connection retry budget, including a zero-second request.
        waits = [errors.FloodPremiumWaitError(request=None, capture=3), errors.FloodWaitError(request=None, capture=0)] * 3
        async def transfer(*args, **kwargs):
            if waits:
                raise waits.pop(0)
        slept = []
        async def record(seconds, *args):
            slept.append(seconds)
        self.engine.client = SimpleNamespace(get_messages=get_messages)
        with patch('tgzip.engine.download', transfer), patch('tgzip.engine.asyncio.sleep', record):
            await self.engine.download_queue()
        self.assertEqual([seconds for seconds in slept if seconds >= 1], [3, 1] * 3)
        job = self.engine.store.jobs(123, -100)[0]
        self.assertEqual((job['state'], job['error']), ('done', ''))
        self.assertTrue(any(name == 'log' and 'non-Premium' in value for name, value in self.events))

    async def test_waits_do_not_extend_connection_retry_budget(self):
        self.engine.chat = -100
        self.engine.store.scanned(123, -100, 10, {'document': 111, 'name': 'file.zip', 'size': 100, 'path': self.root / 'file.zip'})
        async def get_messages(*args, **kwargs):
            return SimpleNamespace(document=SimpleNamespace(id=111, size=100))
        failures = [errors.FloodPremiumWaitError(request=None, capture=1), ConnectionError(),
                    errors.FloodWaitError(request=None, capture=1)] + [ConnectionError()] * 4
        calls = []
        async def transfer(*args, **kwargs):
            calls.append(failures[len(calls)])
            raise calls[-1]
        async def no_wait(*args):
            pass
        self.engine.client = SimpleNamespace(get_messages=get_messages)
        with patch('tgzip.engine.download', transfer), patch('tgzip.engine.asyncio.sleep', no_wait):
            await self.engine.download_queue()
        self.assertEqual(sum(isinstance(item, ConnectionError) for item in calls), 4)
        self.assertEqual(len(calls), 6)
        job = self.engine.store.jobs(123, -100)[0]
        self.assertEqual(job['state'], 'error')
        self.assertEqual(job['error'], error_text(ConnectionError()))

    def queue_files(self, count, size=100):
        self.engine.chat = -100
        # Queued fixtures already sit in the download folder, so they are not relocated.
        self.engine.settings['download_dir'] = str(self.root)
        for number in range(1, count + 1):
            self.engine.store.scanned(123, -100, number, {
                'document': number, 'name': f'{number}.zip', 'size': size,
                'path': self.root / f'{number}.zip'})
        async def get_messages(entity, ids):
            return SimpleNamespace(document=SimpleNamespace(id=ids, size=size), media=ids)
        self.engine.client = SimpleNamespace(get_messages=get_messages)

    async def test_twenty_downloads_overlap_refill_and_claim_each_file_once(self):
        self.queue_files(43)
        releases = {number: asyncio.Event() for number in range(1, 44)}
        started = asyncio.Queue()
        active, visited = set(), []
        peak = 0
        async def transfer(client, message, job, gate, progress, *, limiter=None):
            nonlocal peak
            number = job['message']
            active.add(number)
            visited.append(number)
            peak = max(peak, len(active))
            progress(25, 100)
            started.put_nowait(number)
            try:
                await releases[number].wait()
                progress(100, 0)
            finally:
                active.remove(number)
        with patch.object(self.engine, 'checker', return_value=None), patch('tgzip.engine.download', transfer):
            task = asyncio.create_task(self.engine.download_queue())
            try:
                first = [await asyncio.wait_for(started.get(), 2) for _ in range(20)]
                self.assertEqual(first, list(range(1, 21)))
                self.assertTrue(started.empty())
                # Combined metrics refresh on their own timer, not per-file callbacks.
                await asyncio.sleep(.3)
                aggregate = [value for name, value in self.events if name == 'transfers'][-1]
                self.assertEqual(aggregate['active'], 20)
                self.assertEqual(aggregate['speed'], 2000)
                self.assertEqual(aggregate['summary']['received'], 500)
                releases[7].set()
                self.assertEqual(await asyncio.wait_for(started.get(), 2), 21)
                self.assertFalse(releases[1].is_set())
                for release in releases.values():
                    release.set()
                await asyncio.wait_for(task, 2)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(peak, 20)
        self.assertEqual(sorted(visited), list(range(1, 44)))
        self.assertEqual(self.engine.store.summary(123, -100)['complete'], 43)
        self.assertFalse(self.engine.transfers)

    async def test_parallel_pause_stop_and_restart_resume_every_partial(self):
        from tgzip.storage import CHUNK, Store
        self.queue_files(3, CHUNK * 2)
        self.engine.settings['parallel_downloads'] = 1
        blocked = asyncio.Queue()
        release = asyncio.Event()
        closed, offsets = [], []
        class Stream:
            def __init__(stream, number, offset):
                stream.number, stream.offset = number, offset
            def __aiter__(stream):
                return stream
            async def __anext__(stream):
                if stream.offset == CHUNK:
                    blocked.put_nowait(stream.number)
                    await release.wait()
                if stream.offset >= CHUNK * 2:
                    raise StopAsyncIteration
                stream.offset += CHUNK
                return b'x' * CHUNK
            async def close(stream):
                closed.append(stream.number)
        def iter_download(media, offset, **kwargs):
            offsets.append((media, offset))
            return Stream(media, offset)
        self.engine.client.iter_download = iter_download
        with patch.object(self.engine, 'checker', return_value=None):
            self.engine.launch('downloading', self.engine.download_queue)
            await asyncio.wait_for(blocked.get(), 2)
            await self.engine.command('parallel', value=3)
            for _ in range(2):
                await asyncio.wait_for(blocked.get(), 2)
            await self.engine.command('parallel', value=1)
            await self.engine.command('pause')
            await self.engine.command('finish_current')
            self.assertTrue(self.engine.finish_requested)
            release.set()
            # Let all in-flight chunks reach the shared pause gate.
            for _ in range(10):
                await asyncio.sleep(0)
            for number in range(1, 4):
                self.assertEqual((self.root / f'{number}.zip.part').stat().st_size, CHUNK)
                self.assertFalse((self.root / f'{number}.zip').exists())
            await asyncio.wait_for(self.engine.command('stop'), 2)
            self.assertFalse(self.engine.finish_requested)
            self.assertEqual(sorted(closed), [1, 2, 3])
            self.assertTrue(all(job['state'] == 'queued' and job['done'] == CHUNK
                                for job in self.engine.store.jobs(123, -100)))
            self.assertFalse(self.engine.transfers)
            self.engine.store.close()
            self.engine.store = Store(self.root / 'queue.sqlite3')
            await asyncio.wait_for(self.engine.download_queue(), 2)
        self.assertEqual(sorted(offsets), [(1, 0), (1, CHUNK), (2, 0), (2, CHUNK), (3, 0), (3, CHUNK)])
        self.assertEqual(self.engine.store.summary(123, -100)['complete'], 3)
        for number in range(1, 4):
            self.assertEqual((self.root / f'{number}.zip').stat().st_size, CHUNK * 2)

    async def test_finish_current_drains_claimed_files_and_checks_then_start_continues(self):
        self.queue_files(7)
        self.engine.settings['parallel_downloads'] = 3
        started = asyncio.Queue()
        release, check_release, checking = asyncio.Event(), asyncio.Event(), asyncio.Event()
        visited, checked = [], []

        async def transfer(client, message, job, gate, progress, *, limiter=None):
            visited.append(job['message'])
            started.put_nowait(job['message'])
            await release.wait()
            progress(100, 0)

        async def verify(job, *, background):
            self.assertTrue(background)
            checking.set()
            await check_release.wait()
            checked.append(job['message'])
            self.engine.store.update(123, -100, job['message'], verification='verified')

        with patch.object(self.engine, 'checker', return_value=Path('WinRAR.exe')), \
             patch.object(self.engine, 'verify_job', side_effect=verify), \
             patch('tgzip.engine.download', transfer):
            await self.engine.command('start')
            for _ in range(3):
                await asyncio.wait_for(started.get(), 2)
            await self.engine.command('finish_current')
            await self.engine.command('finish_current')
            self.assertEqual(self.events.count(('finish_current', True)), 1)
            release.set()
            await asyncio.wait_for(checking.wait(), 2)
            self.assertFalse(self.engine.task.done())
            self.assertEqual(visited, [1, 2, 3])
            check_release.set()
            await asyncio.wait_for(self.engine.task, 2)
            self.assertEqual(checked, [1, 2, 3])
            self.assertIsNone(self.engine.kind)
            self.assertFalse(self.engine.finish_requested)
            jobs = self.engine.store.jobs(123, -100)
            self.assertEqual([j['state'] for j in jobs], ['done'] * 3 + ['queued'] * 4)
            self.assertTrue(all(j['done'] == 0 for j in jobs[3:]))
            await self.engine.command('start')
            await asyncio.wait_for(self.engine.task, 2)
        self.assertEqual(visited, list(range(1, 8)))
        self.assertEqual(checked, list(range(1, 8)))
        self.assertEqual(self.engine.store.summary(123, -100)['complete'], 7)

    async def test_finish_current_while_paused_waits_for_resume_without_refilling(self):
        self.queue_files(5)
        self.engine.settings['parallel_downloads'] = 2
        started = asyncio.Queue()
        release = asyncio.Event()
        visited = []

        async def transfer(client, message, job, gate, progress, *, limiter=None):
            visited.append(job['message'])
            started.put_nowait(job['message'])
            await release.wait()
            await gate.wait()
            progress(100, 0)

        with patch.object(self.engine, 'checker', return_value=None), patch('tgzip.engine.download', transfer):
            await self.engine.command('start')
            for _ in range(2):
                await asyncio.wait_for(started.get(), 2)
            await self.engine.command('pause')
            await self.engine.command('finish_current')
            release.set()
            for _ in range(5):
                await asyncio.sleep(0)
            self.assertFalse(self.engine.gate.is_set())
            self.assertFalse(self.engine.task.done())
            self.assertEqual(self.engine.store.summary(123, -100)['complete'], 0)
            await self.engine.command('resume')
            await asyncio.wait_for(self.engine.task, 2)
        self.assertEqual(visited, [1, 2])
        self.assertEqual(self.engine.store.summary(123, -100)['complete'], 2)
        await self.engine.command('finish_current')
        self.assertFalse(self.engine.finish_requested)

    async def test_progress_burst_avoids_sql_writes_and_periodically_checkpoints(self):
        self.queue_files(1, 1000)
        entered, checkpointed = asyncio.Event(), asyncio.Event()
        statements = []
        original_flush = self.engine.store.flush_progress
        def flush():
            pending = bool(self.engine.store.progress_pending)
            original_flush()
            if pending:
                checkpointed.set()
        async def transfer(client, message, job, gate, progress, *, limiter=None):
            statements.clear()
            with patch.object(self.engine.store, 'summary', wraps=self.engine.store.summary) as summary:
                for done in range(1, 801):
                    progress(done, 200)
                self.assertEqual(summary.call_count, 0)
            self.assertFalse(any(sql.startswith(('UPDATE', 'BEGIN', 'COMMIT')) for sql in statements))
            entered.set()
            await asyncio.Event().wait()
        self.engine.store.db.set_trace_callback(statements.append)
        with patch.object(self.engine, 'checker', return_value=None), \
             patch('tgzip.engine.download', transfer), \
             patch.object(self.engine.store, 'flush_progress', side_effect=flush):
            self.engine.launch('downloading', self.engine.download_queue)
            await asyncio.wait_for(entered.wait(), 2)
            await asyncio.wait_for(checkpointed.wait(), 2)
            self.assertEqual(self.engine.store.db.execute('SELECT done FROM jobs').fetchone()[0], 800)
            self.assertEqual(sum(sql == 'COMMIT' for sql in statements), 1)
            await self.engine.cancel()
        self.assertEqual(self.engine.store.jobs(123, -100)[0]['done'], 800)
        self.assertFalse(self.engine.store.progress_pending)

    async def test_parallel_failure_does_not_retry_forever_or_block_other_files(self):
        from tgzip.transfer import TransferError
        self.queue_files(25)
        visited = []
        async def transfer(client, message, job, gate, progress, *, limiter=None):
            visited.append(job['message'])
            await asyncio.sleep(0)
            if job['message'] == 2:
                raise TransferError('Fixture attachment unavailable.')
            progress(100, 0)
        with patch.object(self.engine, 'checker', return_value=None), patch('tgzip.engine.download', transfer):
            await asyncio.wait_for(self.engine.download_queue(), 2)
        self.assertEqual(sorted(visited), list(range(1, 26)))
        self.assertEqual(self.engine.store.summary(123, -100)['complete'], 24)
        self.assertEqual(self.engine.store.summary(123, -100)['failed'], 1)

    async def test_live_parallel_grows_drains_and_rapid_changes_never_duplicate_claims(self):
        self.queue_files(12)
        self.engine.settings['parallel_downloads'] = 2
        releases = {i: asyncio.Event() for i in range(1, 13)}
        started, completed = asyncio.Queue(), asyncio.Queue()
        visited, canceled = [], []

        async def transfer(client, message, job, gate, progress, *, limiter=None):
            number = job['message']
            visited.append(number)
            started.put_nowait(number)
            try:
                await releases[number].wait()
                progress(100, 0)
                completed.put_nowait(number)
            except asyncio.CancelledError:
                canceled.append(number)
                raise

        async def settle():
            for _ in range(10):
                await asyncio.sleep(0)

        with patch.object(self.engine, 'checker', return_value=None), patch('tgzip.engine.download', transfer):
            await self.engine.command('start')
            self.assertEqual([await asyncio.wait_for(started.get(), 2) for _ in range(2)], [1, 2])
            await self.engine.command('parallel', value=5)
            self.assertEqual([await asyncio.wait_for(started.get(), 2) for _ in range(3)], [3, 4, 5])
            await self.engine.command('parallel', value=2)
            for number in (1, 2, 3):
                releases[number].set()
                self.assertEqual(await asyncio.wait_for(completed.get(), 2), number)
                await settle()
                self.assertTrue(started.empty())
            self.assertEqual(set(self.engine.transfers), {4, 5})
            releases[4].set()
            self.assertEqual(await asyncio.wait_for(started.get(), 2), 6)
            # Only the latest requested value should apply before the scheduler runs.
            for value in (20, 1, 6, 3):
                await self.engine.command('parallel', value=value)
            self.assertEqual(await asyncio.wait_for(started.get(), 2), 7)
            await settle()
            self.assertEqual(set(self.engine.transfers), {5, 6, 7})
            self.assertTrue(started.empty())
            for release in releases.values():
                release.set()
            await asyncio.wait_for(self.engine.task, 2)
        self.assertEqual(visited, list(range(1, 13)))
        self.assertFalse(canceled)
        self.assertEqual(self.engine.store.summary(123, -100)['complete'], 12)

    async def test_live_parallel_respects_pause_and_finish_current(self):
        self.queue_files(8)
        self.engine.settings['parallel_downloads'] = 1
        release = asyncio.Event()
        started = asyncio.Queue()
        visited = []

        async def transfer(client, message, job, gate, progress, *, limiter=None):
            visited.append(job['message'])
            started.put_nowait(job['message'])
            await release.wait()
            await gate.wait()
            progress(100, 0)

        with patch.object(self.engine, 'checker', return_value=None), patch('tgzip.engine.download', transfer):
            await self.engine.command('start')
            self.assertEqual(await asyncio.wait_for(started.get(), 2), 1)
            await self.engine.command('pause')
            await self.engine.command('parallel', value=4)
            for _ in range(10):
                await asyncio.sleep(0)
            self.assertEqual(visited, [1])
            self.assertFalse(self.engine.gate.is_set())
            await self.engine.command('resume')
            self.assertEqual([await asyncio.wait_for(started.get(), 2) for _ in range(3)], [2, 3, 4])
            await self.engine.command('finish_current')
            await self.engine.command('parallel', value=20)
            release.set()
            await asyncio.wait_for(self.engine.task, 2)
            self.assertEqual(visited, [1, 2, 3, 4])
            self.assertEqual(self.engine.store.summary(123, -100)['complete'], 4)
            await self.engine.command('start')
            await asyncio.wait_for(self.engine.task, 2)
        self.assertEqual(visited, list(range(1, 9)))

    async def test_live_pool_fatal_error_cleans_up_all_claimed_transfers(self):
        self.queue_files(8)
        self.engine.settings['parallel_downloads'] = 1
        started = asyncio.Queue()
        fail = asyncio.Event()
        closed = []

        async def transfer(client, message, job, gate, progress, *, limiter=None):
            number = job['message']
            started.put_nowait(number)
            progress(25, 100)
            try:
                if number == 1:
                    await fail.wait()
                    raise OSError('Fixture disk failure')
                await asyncio.Event().wait()
            finally:
                closed.append(number)

        with patch.object(self.engine, 'checker', return_value=None), patch('tgzip.engine.download', transfer), \
             patch.object(self.engine.store, 'next_job', wraps=self.engine.store.next_job) as claim:
            await self.engine.command('start')
            await asyncio.wait_for(started.get(), 2)
            await self.engine.command('parallel', value=4)
            for _ in range(3):
                await asyncio.wait_for(started.get(), 2)
            fail.set()
            await asyncio.wait_for(self.engine.task, 2)
            self.assertEqual(claim.call_count, 4)
        self.assertEqual(sorted(closed), [1, 2, 3, 4])
        self.assertFalse(self.engine.transfers)
        self.assertIsNone(self.engine.kind)
        jobs = self.engine.store.jobs(123, -100)
        self.assertEqual(jobs[0]['state'], 'error')
        self.assertTrue(all(j['state'] == 'queued' for j in jobs[1:]))
        self.assertIn(('error', error_text(OSError('Fixture disk failure'))), self.events)

    async def test_parallel_setting_is_bounded_persistent_and_live_while_busy(self):
        from tgzip.storage import parallel_downloads
        self.assertEqual([parallel_downloads(value) for value in (None, 'bad', 0, -1, 99, 4)],
                         [20, 20, 1, 1, 20, 4])
        await self.engine.command('parallel', value=5)
        self.assertEqual(json.loads(self.engine.settings_path.read_text())['parallel_downloads'], 5)
        self.engine.launch('downloading', lambda: asyncio.sleep(60))
        await self.engine.command('parallel', value=10)
        self.assertEqual(self.engine.settings['parallel_downloads'], 10)
        from tgzip.storage import load_settings
        self.assertEqual(load_settings(self.engine.settings_path)['parallel_downloads'], 10)

    async def test_speed_limit_can_change_while_busy_and_survives_restart(self):
        self.assertEqual(self.engine.limiter.rate, 0)
        self.engine.launch('downloading', lambda: asyncio.sleep(60))
        await asyncio.sleep(0)
        await self.engine.command('speed_limit', value=.25)
        self.assertEqual(self.engine.limiter.rate, 256 * 1024)
        self.assertEqual(json.loads(self.engine.settings_path.read_text())['speed_limit'], .25)
        await self.engine.command('pause')
        await self.engine.command('speed_limit', value=5)
        self.assertFalse(self.engine.gate.is_set())
        await self.engine.cancel()
        reopened = Engine(self.root, lambda *args: None)
        try:
            self.assertEqual(reopened.limiter.rate, 5 * 1024 * 1024)
        finally:
            reopened.store.close()
        await self.engine.command('speed_limit', value=0)
        self.assertEqual(self.engine.limiter.rate, 0)

    async def test_all_downloads_receive_same_live_limiter(self):
        self.queue_files(20)
        await self.engine.command('speed_limit', value=2)
        seen = []
        async def transfer(client, message, job, gate, progress, *, limiter):
            seen.append(limiter)
            await asyncio.sleep(0)
        with patch.object(self.engine, 'checker', return_value=None), patch('tgzip.engine.download', transfer):
            await self.engine.download_queue()
        self.assertEqual(len(seen), 20)
        self.assertTrue(all(limiter is self.engine.limiter for limiter in seen))
        self.assertEqual(self.engine.limiter.rate, 2 * 1024 * 1024)

    async def test_transfer_snapshot_includes_independent_file_details(self):
        self.queue_files(1)
        self.engine.transfers[1] = {'name': '1.zip', 'path': str(self.root / '1.zip'),
                                   'done': 25, 'size': 100, 'speed': 0, 'updated': 0}
        self.engine.emit_transfers()
        snapshot = next(value for event, value in reversed(self.events) if event == 'transfers')
        self.engine.transfers[1]['done'] = 99
        self.assertEqual(snapshot['files'][0]['done'], 25)
        self.assertEqual(snapshot['files'][0]['message'], 1)
        self.assertEqual(snapshot['files'][0]['name'], '1.zip')
        self.assertEqual(snapshot['files'][0]['size'], 100)

    async def test_qr_expiry_two_factor_retry_and_password_not_saved(self):
        engine = self.engine
        passwords = iter(['incorrect fixture', 'correct fixture'])
        def emit(name, value):
            self.events.append((name, value))
            if name == 'password':
                asyncio.create_task(engine.command('password', value=next(passwords)))
        engine.emit = emit
        engine.secrets = {'api_id': 123, 'api_hash': 'a' * 32}
        class QR:
            url = 'tg://login?token=test-fixture'
            waits = 0
            recreated = 0
            async def wait(self, **kwargs):
                self.waits += 1
                if self.waits == 1:
                    raise asyncio.TimeoutError
                raise errors.SessionPasswordNeededError(request=None)
            async def recreate(self):
                self.recreated += 1
        qr = QR()
        class Client:
            session = SimpleNamespace(auth_key=True, save=lambda: 'encrypted-session-fixture')
            async def connect(self):
                pass
            async def disconnect(self):
                pass
            async def is_user_authorized(self):
                return False
            async def qr_login(self):
                return qr
            async def sign_in(self, password):
                if password == 'incorrect fixture':
                    raise errors.PasswordHashInvalidError(request=None)
            async def get_me(self):
                return SimpleNamespace(id=123, first_name='Test', last_name='Account')
            async def iter_dialogs(self):
                yield dialog(-100, 'Group')
        with patch('tgzip.engine.TelegramClient', return_value=Client()):
            await asyncio.wait_for(engine.connect(), 1)
        self.assertEqual(qr.recreated, 1)
        self.assertEqual(engine.account, 123)
        self.assertNotIn('password', engine.vault.saved)
        self.assertNotIn('correct fixture', str(engine.vault.saved))
        self.assertEqual(self.events[-1][0], 'qr')

    async def test_update_preparation_is_background_and_cancel_waits_for_cleanup(self):
        import threading
        entered, stopped = threading.Event(), threading.Event()
        def prepare(root, report, cancel):
            entered.set()
            cancel.wait(3)
            stopped.set()
            return None
        with patch('tgzip.engine.prepare_update', prepare):
            self.engine.launch('updating', self.engine.update_application)
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            await asyncio.wait_for(self.engine.cancel(), 1)
        self.assertTrue(stopped.is_set())
        self.assertFalse(any(name == 'update_ready' for name, value in self.events))

    async def test_prepared_update_requests_orderly_ui_restart(self):
        import sqlite3
        self.queue_files(1)
        self.engine.store.stage_progress(123, -100, 1, 50)
        self.engine.settings['selected_chats'] = {'123': -100}
        with patch('tgzip.engine.prepare_update', return_value='prepared-plan.json'):
            await self.engine.update_application()
        snapshots = list((self.root / 'backups').glob('update-*'))
        self.assertEqual(len(snapshots), 1)
        self.assertTrue((snapshots[0] / 'queue.sqlite3').is_file())
        db = sqlite3.connect(snapshots[0] / 'queue.sqlite3')
        try:
            self.assertEqual(db.execute('SELECT done FROM jobs').fetchone()[0], 50)
        finally:
            db.close()
        self.assertEqual(json.loads((snapshots[0] / 'settings.json').read_text()), self.engine.settings)
        self.assertLess(next(i for i, (name, value) in enumerate(self.events)
                             if name == 'log' and 'backup saved' in value),
                        self.events.index(('update_ready', 'prepared-plan.json')))
        self.assertIn(('update_ready', 'prepared-plan.json'), self.events)
        self.assertFalse(self.engine.closing)

    async def test_current_version_does_not_create_backup(self):
        with patch('tgzip.engine.prepare_update', return_value=None):
            await self.engine.update_application()
        self.assertFalse((self.root / 'backups').exists())
        self.assertFalse(any(name == 'update_ready' for name, _ in self.events))

    async def test_backup_failure_prevents_restart_and_cleans_staged_update(self):
        from updater import UpdateError
        stage = self.root / 'staged-update'
        stage.mkdir()
        with patch('tgzip.engine.prepare_update', return_value=str(stage / 'plan.json')), \
             patch('tgzip.engine.load_plan', return_value=(self.root, stage, {})), \
             patch('tgzip.engine.backup_before_update', side_effect=UpdateError('Backup failed')):
            with self.assertRaises(UpdateError):
                await self.engine.update_application()
        self.assertFalse(stage.exists())
        self.assertFalse(any(name == 'update_ready' for name, _ in self.events))

    async def test_cancel_waits_for_backup_worker_and_removes_update_stage(self):
        import threading
        from updater import cancelled
        entered, stopped = threading.Event(), threading.Event()
        stage = self.root / 'staged-update'
        stage.mkdir()
        def backup(data_dir, settings, cancel, report):
            entered.set()
            try:
                cancel.wait(3)
                cancelled(cancel)
            finally:
                stopped.set()
        with patch('tgzip.engine.prepare_update', return_value=str(stage / 'plan.json')), \
             patch('tgzip.engine.load_plan', return_value=(self.root, stage, {})), \
             patch('tgzip.engine.backup_before_update', side_effect=backup):
            self.engine.launch('updating', self.engine.update_application)
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            await asyncio.wait_for(self.engine.cancel(), 1)
        self.assertTrue(stopped.is_set())
        self.assertFalse(stage.exists())
        self.assertFalse(any(name == 'update_ready' for name, _ in self.events))

    async def test_error_messages_do_not_echo_exception_contents(self):
        self.assertNotIn('private-token', error_text(RuntimeError('private-token')))
        self.assertEqual(error_text(errors.FloodPremiumWaitError(request=None, capture=5)),
                         'Telegram requires a 5-second wait. Try again later.')


if __name__ == '__main__':
    unittest.main()
