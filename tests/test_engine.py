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
        self.assertNotEqual(jobs[0]['path'], jobs[1]['path'])
        self.assertTrue(jobs[-1]['path'].endswith('.rar'))

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
        async def transfer(*args):
            if calls.count('message') == 1:
                raise errors.FileReferenceExpiredError(request=None)
        async def no_wait(*args):
            pass
        self.engine.client = SimpleNamespace(get_messages=get_messages)
        with patch('tgzip.engine.download', transfer), patch('tgzip.engine.asyncio.sleep', no_wait):
            await self.engine.download_queue()
        self.assertEqual(calls.count('message'), 2)
        self.assertEqual(self.engine.store.jobs(123, -100)[0]['state'], 'done')

    def queue_files(self, count, size=100):
        self.engine.chat = -100
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
        async def transfer(client, message, job, gate, progress):
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
        self.engine.settings['parallel_downloads'] = 3
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
            for _ in range(3):
                await asyncio.wait_for(blocked.get(), 2)
            await self.engine.command('pause')
            release.set()
            # Let all in-flight chunks reach the shared pause gate.
            for _ in range(10):
                await asyncio.sleep(0)
            for number in range(1, 4):
                self.assertEqual((self.root / f'{number}.zip.part').stat().st_size, CHUNK)
                self.assertFalse((self.root / f'{number}.zip').exists())
            await asyncio.wait_for(self.engine.command('stop'), 2)
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
        async def transfer(client, message, job, gate, progress):
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
        async def transfer(client, message, job, gate, progress):
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

    async def test_parallel_setting_is_bounded_persistent_and_locked_while_busy(self):
        from tgzip.storage import parallel_downloads
        from tgzip.transfer import TransferError
        self.assertEqual([parallel_downloads(value) for value in (None, 'bad', 0, -1, 99, 4)],
                         [20, 20, 1, 1, 20, 4])
        await self.engine.command('parallel', value=5)
        self.assertEqual(json.loads(self.engine.settings_path.read_text())['parallel_downloads'], 5)
        self.engine.launch('downloading', lambda: asyncio.sleep(60))
        with self.assertRaises(TransferError):
            await self.engine.command('parallel', value=10)
        self.assertEqual(self.engine.settings['parallel_downloads'], 5)

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
        with patch('tgzip.engine.prepare_update', return_value='prepared-plan.json'):
            await self.engine.update_application()
        self.assertIn(('update_ready', 'prepared-plan.json'), self.events)
        self.assertFalse(self.engine.closing)

    async def test_error_messages_do_not_echo_exception_contents(self):
        self.assertNotIn('private-token', error_text(RuntimeError('private-token')))


if __name__ == '__main__':
    unittest.main()
