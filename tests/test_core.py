import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from tgzip.security import Vault, _crypt, import_legacy_session, validate_keys
from tgzip.storage import CHUNK, Store, atomic_write, load_settings, safe_name, zip_candidate
from tgzip.transfer import TransferError, download


def zip_bytes():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_STORED) as z:
        z.writestr('payload.bin', bytes(range(256)) * 6500)
    return stream.getvalue()


class FakeStream:
    def __init__(self, data, offset, delay=0, fail_at=None):
        self.data, self.offset, self.delay, self.fail_at = data, offset, delay, fail_at
        self.closed = False
        self.reads = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(self.delay)
        if self.fail_at is not None and self.reads == self.fail_at:
            raise ConnectionError('simulated network interruption')
        if self.offset >= len(self.data):
            raise StopAsyncIteration
        chunk = self.data[self.offset:self.offset + CHUNK]
        self.offset += len(chunk)
        self.reads += 1
        return chunk

    async def close(self):
        self.closed = True


class FakeClient:
    def __init__(self, data, delay=0, fail_at=None):
        self.data, self.delay, self.fail_at = data, delay, fail_at
        self.streams = []
        self.offsets = []

    def iter_download(self, media, *, offset, request_size, chunk_size):
        assert request_size == chunk_size == CHUNK
        stream = FakeStream(self.data, offset, self.delay, self.fail_at)
        self.streams.append(stream)
        self.offsets.append(offset)
        return stream


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'queue.db')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def item(self, doc=99):
        return dict(document=doc, name='pack.zip', size=200, path=self.root / 'pack.zip')

    def test_scan_idempotency_and_account_isolation(self):
        for _ in range(2):
            self.store.scanned(1, -10, 7, self.item())
        self.store.scanned(2, -10, 7, self.item())
        self.assertEqual(len(self.store.jobs(1, -10)), 1)
        self.store.update(1, -10, 7, state='done', done=200)
        self.assertEqual(self.store.summary(1, -10)['complete'], 1)
        self.assertEqual(self.store.summary(2, -10)['complete'], 0)

    def test_cursor_never_regresses_and_advances_without_zip(self):
        self.store.scanned(1, 10, 100)
        self.store.scanned(1, 10, 50)
        self.assertEqual(self.store.cursor(1, 10), 100)

    def test_scan_is_atomic_on_invalid_item(self):
        self.store.scanned(1, 10, 1)
        with self.assertRaises(KeyError):
            self.store.scanned(1, 10, 2, {'document': 8})
        self.assertEqual(self.store.cursor(1, 10), 1)

    def test_crash_recovers_downloading_state(self):
        self.store.scanned(1, 10, 1, self.item())
        self.store.update(1, 10, 1, state='downloading', done=128)
        self.store.close()
        self.store = Store(self.root / 'queue.db')
        self.assertEqual(self.store.jobs(1, 10)[0]['state'], 'queued')
        self.assertEqual(self.store.jobs(1, 10)[0]['done'], 128)

    def test_saved_chat_and_settings_survive(self):
        path = self.root / 'settings.json'
        original = {'selected_chats': {'123': -100001}, 'download_dir': 'C:/Archives'}
        atomic_write(path, json.dumps(original).encode())
        self.assertEqual(load_settings(path), original)
        path.write_text('{broken')
        self.assertEqual(load_settings(path), {})
        self.assertTrue(path.with_suffix('.broken.json').exists())

    def test_windows_filename_sanitizing(self):
        for name in ['../../CON.zip', 'PRN.zip', 'name<>:"|?*\\a.ZIP', 'x' * 500 + '.zip', '.zip']:
            result = safe_name(name)
            self.assertTrue(result.endswith('.zip'))
            self.assertLessEqual(len(result), 90)
            self.assertNotRegex(result, r'[<>:"/\\|?*]')
        self.assertEqual(safe_name('CON.zip'), '_CON.zip')

    def test_zip_only_filter(self):
        self.assertTrue(zip_candidate('pack.ZIP', 'application/octet-stream'))
        self.assertTrue(zip_candidate(None, 'application/zip'))
        self.assertFalse(zip_candidate('pack.rar', 'application/zip'))
        self.assertFalse(zip_candidate('pack.zip.exe', 'application/zip'))
        self.assertFalse(zip_candidate(None, 'application/octet-stream'))


class TransferTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = zip_bytes()
        self.target = self.root / 'archive.zip'
        self.part = self.root / 'archive.zip.part'
        self.job = {'path': str(self.target), 'size': len(self.data)}
        self.gate = asyncio.Event()
        self.gate.set()
        self.updates = []

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def run_download(self, client=None):
        client = client or FakeClient(self.data)
        await download(client, SimpleNamespace(media='media'), self.job, self.gate,
                       lambda done, speed: self.updates.append((done, speed)))
        return client

    async def test_complete_exact_bytes_and_close_stream(self):
        client = await self.run_download()
        self.assertEqual(self.target.read_bytes(), self.data)
        self.assertFalse(self.part.exists())
        self.assertTrue(client.streams[0].closed)

    async def test_resume_truncates_unaligned_tail_and_matches_hash(self):
        self.part.write_bytes(self.data[:CHUNK] + b'corrupt tail')
        client = await self.run_download()
        self.assertEqual(client.offsets, [CHUNK])
        self.assertEqual(hashlib.sha256(self.target.read_bytes()).digest(), hashlib.sha256(self.data).digest())

    async def test_network_failure_keeps_part_and_can_resume(self):
        client = FakeClient(self.data, fail_at=1)
        with self.assertRaises(ConnectionError):
            await self.run_download(client)
        self.assertEqual(self.part.stat().st_size, CHUNK)
        self.assertTrue(client.streams[0].closed)
        await self.run_download()
        self.assertEqual(self.target.read_bytes(), self.data)

    async def test_pause_does_not_read_and_resume_completes(self):
        self.gate.clear()
        client = FakeClient(self.data)
        task = asyncio.create_task(self.run_download(client))
        await asyncio.sleep(.03)
        self.assertEqual(client.streams[0].reads, 0)
        self.assertFalse(task.done())
        self.gate.set()
        await asyncio.wait_for(task, 1)
        self.assertTrue(self.target.exists())

    async def test_stop_while_paused_closes_stream(self):
        self.gate.clear()
        client = FakeClient(self.data)
        task = asyncio.create_task(self.run_download(client))
        await asyncio.sleep(.02)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(client.streams[0].closed)
        self.assertTrue(self.part.exists())
        self.assertFalse(self.target.exists())

    async def test_stop_interrupts_pending_network_request(self):
        client = FakeClient(self.data, delay=20)
        task = asyncio.create_task(self.run_download(client))
        await asyncio.sleep(.02)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, .5)
        self.assertTrue(client.streams[0].closed)

    async def test_recover_crash_after_last_byte_before_rename(self):
        self.part.write_bytes(self.data)
        client = await self.run_download()
        self.assertEqual(client.streams, [])
        self.assertEqual(self.target.read_bytes(), self.data)

    async def test_existing_complete_file_is_not_downloaded_again(self):
        self.target.write_bytes(self.data)
        client = await self.run_download()
        self.assertEqual(client.streams, [])

    async def test_existing_unrelated_file_never_overwritten(self):
        self.target.write_bytes(b'keep me')
        with self.assertRaises(TransferError):
            await self.run_download()
        self.assertEqual(self.target.read_bytes(), b'keep me')

    async def test_invalid_zip_never_promoted_to_final_filename(self):
        self.data = b'this is not a ZIP archive'
        self.job['size'] = len(self.data)
        with self.assertRaises(TransferError):
            await self.run_download()
        self.assertTrue(self.part.exists())
        self.assertFalse(self.target.exists())

    async def test_short_transfer_never_finalizes(self):
        self.job['size'] += 50
        with self.assertRaises(TransferError):
            await self.run_download()
        self.assertFalse(self.target.exists())

    async def test_oversized_partial_is_preserved(self):
        self.part.write_bytes(self.data + b'extra')
        with self.assertRaises(ValueError):
            await self.run_download()
        self.assertEqual(self.part.stat().st_size, len(self.data) + 5)


class SecurityTests(unittest.TestCase):
    def test_validate_keys(self):
        self.assertEqual(validate_keys('123', 'a' * 32), (123, 'a' * 32))
        for api_id, api_hash in [('0', 'a' * 32), ('abc', 'a' * 32), ('12', 'wrong')]:
            with self.assertRaises(ValueError):
                validate_keys(api_id, api_hash)

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI requires Windows')
    def test_real_dpapi_roundtrip_and_tamper_detection(self):
        secret = b'fixture credential, not a real secret'
        encrypted = _crypt(secret)
        self.assertNotIn(secret, encrypted)
        self.assertEqual(_crypt(encrypted, decrypt=True), secret)
        corrupt = bytearray(encrypted)
        corrupt[-1] ^= 1
        with self.assertRaises(OSError):
            _crypt(bytes(corrupt), decrypt=True)

    @unittest.skipIf(os.name == 'nt', 'Non-Windows fail-closed test')
    def test_no_plaintext_fallback(self):
        with self.assertRaises(RuntimeError):
            _crypt(b'secret')

    def test_vault_roundtrip_uses_encryption_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'credentials.dpapi'
            values = {'api_id': 123, 'api_hash': 'a' * 32, 'session': 'test fixture'}
            def fake_crypt(data, decrypt=False):
                return bytes(byte ^ 0xA5 for byte in data)
            with patch('tgzip.security._crypt', fake_crypt):
                vault = Vault(path)
                vault.save(values)
                self.assertNotIn(b'api_hash', path.read_bytes())
                self.assertEqual(vault.load(), values)

    def test_import_old_session_does_not_change_source(self):
        from telethon.sessions import StringSession
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'telegram_zip_session.session'
            key = bytes(range(256))
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE sessions(dc_id INTEGER, server_address TEXT, port INTEGER, auth_key BLOB)')
                db.execute('INSERT INTO sessions VALUES(2, ?, 443, ?)', ('149.154.167.51', key))
            before = path.read_bytes()
            session = StringSession(import_legacy_session(path))
            self.assertEqual(session.auth_key.key, key)
            self.assertEqual(session.dc_id, 2)
            self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
