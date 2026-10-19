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

    async def test_error_messages_do_not_echo_exception_contents(self):
        self.assertNotIn('private-token', error_text(RuntimeError('private-token')))


if __name__ == '__main__':
    unittest.main()
