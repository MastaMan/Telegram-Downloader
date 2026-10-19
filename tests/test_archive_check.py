"""Checker decisions, subprocess lifetime and durable quarantine without WinRAR."""
import asyncio
from pathlib import Path
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tgzip.archive_check import CheckResult, _stop_process, check_archive, move_to_quarantine, quarantine_destination
from tgzip.engine import Engine


class Process:
    def __init__(self, code=0, hang=False):
        self.code, self.hang = code, hang
        self.returncode = None
        self.terminated = False
        self.started = asyncio.Event()
        self.finished = asyncio.Event()

    async def wait(self):
        self.started.set()
        if self.hang:
            await self.finished.wait()
        self.returncode = self.code
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = -15
        self.finished.set()

    def kill(self):
        self.terminate()


class CheckerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.gate = asyncio.Event()
        self.gate.set()

    async def result_for(self, codes):
        processes = [Process(code) for code in codes]
        create = AsyncMock(side_effect=processes)
        with patch('tgzip.archive_check.asyncio.create_subprocess_exec', create):
            result = await check_archive('archive & name.rar', 'WinRAR.exe', self.gate, lambda text: None, retry_delay=0)
        args, kwargs = create.call_args
        self.assertEqual(args[1:5], ('t', '-p-', '-ibck', '-inul'))
        self.assertNotIn('shell', kwargs)
        self.assertTrue(Path(args[5]).is_absolute())
        return result, create.call_count

    async def test_success_wins_and_stops_retries(self):
        for codes in ([0], [2, 0], [12, 11, 0]):
            with self.subTest(codes=codes):
                result, count = await self.result_for(codes)
                self.assertEqual(result.status, 'ok')
                self.assertEqual(count, len(codes))

    async def test_only_three_confirmed_bad_codes_allow_quarantine(self):
        for codes in ([11, 11, 11], [2, 3, 11], [3, 3, 3]):
            result, count = await self.result_for(codes)
            self.assertEqual(result.status, 'bad')
            self.assertEqual(count, 3)
        for codes in ([12, 11, 11], [2, 5, 3], [2, 3, 255], [1, 1, 1]):
            result, count = await self.result_for(codes)
            self.assertEqual(result.status, 'error')
            self.assertEqual(count, 3)

    async def test_launch_failure_never_allows_quarantine(self):
        with patch('tgzip.archive_check.asyncio.create_subprocess_exec', AsyncMock(side_effect=FileNotFoundError)):
            result = await check_archive('a.rar', 'missing.exe', self.gate, lambda text: None)
        self.assertEqual(result.status, 'error')

    async def test_timeout_terminates_owned_process(self):
        process = Process(hang=True)
        with patch('tgzip.archive_check.asyncio.create_subprocess_exec', AsyncMock(return_value=process)):
            result = await check_archive('a.rar', 'WinRAR.exe', self.gate, lambda text: None, timeout=.02)
        self.assertTrue(process.terminated)
        self.assertEqual(result.status, 'error')

    async def test_cancel_terminates_owned_process(self):
        process = Process(hang=True)
        with patch('tgzip.archive_check.asyncio.create_subprocess_exec', AsyncMock(return_value=process)):
            task = asyncio.create_task(check_archive('a.rar', 'WinRAR.exe', self.gate, lambda text: None))
            await process.started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
        self.assertTrue(process.terminated)
        self.assertIsNotNone(process.returncode)

    async def test_pause_holds_finished_result_until_resume(self):
        process = Process(hang=True)
        with patch('tgzip.archive_check.asyncio.create_subprocess_exec', AsyncMock(return_value=process)):
            task = asyncio.create_task(check_archive('a.rar', 'WinRAR.exe', self.gate, lambda text: None))
            await process.started.wait()
            self.gate.clear()
            process.finished.set()
            await asyncio.sleep(.02)
            self.assertFalse(task.done())
            self.gate.set()
            self.assertEqual((await task).status, 'ok')

    async def test_real_child_process_is_reaped_on_stop(self):
        process = await asyncio.create_subprocess_exec(sys.executable, '-c', 'import time; time.sleep(60)')
        await asyncio.wait_for(_stop_process(process), 6)
        self.assertIsNotNone(process.returncode)


class VerificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.events = []
        self.engine = Engine(self.root, lambda name, value: self.events.append((name, value)))
        self.engine.account, self.engine.chat = 1, -10
        self.source = self.root / 'sample.rar'
        self.source.write_bytes(b'fixture archive bytes')
        self.engine.store.scanned(1, -10, 9, {'document': 3, 'name': self.source.name,
                                           'size': self.source.stat().st_size, 'path': self.source})
        self.engine.store.update(1, -10, 9, state='done', done=self.source.stat().st_size)

    async def asyncTearDown(self):
        await self.engine.cancel()
        self.engine.store.close()
        self.temp.cleanup()

    def job(self):
        return self.engine.store.jobs(1, -10)[0]

    async def verify(self, result):
        checker = AsyncMock(return_value=result)
        with patch.object(self.engine, 'checker', return_value=Path('WinRAR.exe')), patch('tgzip.engine.check_archive', checker):
            await self.engine.verify_job(self.job())
        return checker

    async def test_bad_archive_is_moved_and_never_downloaded_again(self):
        await self.verify(CheckResult('bad', 'Password required after 3 attempts.', 11))
        job = self.job()
        self.assertEqual(job['state'], 'done')
        self.assertEqual(job['verification'], 'quarantined')
        self.assertFalse(self.source.exists())
        self.assertEqual(Path(job['quarantine_path']).read_bytes(), b'fixture archive bytes')
        self.assertIsNone(self.engine.store.next_job(1, -10))
        self.assertIsNone(self.engine.store.next_check_job(1, -10))
        self.assertEqual(self.engine.store.summary(1, -10)['quarantined'], 1)

    async def test_success_and_operational_errors_leave_original(self):
        await self.verify(CheckResult('ok', 'Verified.', 0))
        self.assertEqual(self.job()['verification'], 'ok')
        await self.verify(CheckResult('error', 'Read error.', 12))
        self.assertEqual(self.job()['verification'], 'error')
        self.assertTrue(self.source.exists())
        self.assertFalse((self.root / 'wrong_archives').exists())

    async def test_missing_winrar_leaves_unchecked(self):
        with patch.object(self.engine, 'checker', return_value=None):
            await self.engine.verify_job(self.job())
        self.assertEqual(self.job()['verification'], 'pending')
        self.assertTrue(self.source.exists())

    async def test_missing_or_changed_file_is_not_tested_or_moved(self):
        self.source.write_bytes(b'changed size')
        checker = await self.verify(CheckResult('bad', 'bad', 2))
        checker.assert_not_awaited()
        self.assertEqual(self.job()['verification'], 'error')
        self.assertTrue(self.source.exists())

    async def test_multipart_set_is_not_misclassified_as_broken(self):
        job = self.job()
        job['name'] = 'collection.part01.rar'
        checker = AsyncMock(return_value=CheckResult('bad', 'Missing volume', 2))
        with patch.object(self.engine, 'checker', return_value=Path('WinRAR.exe')), patch('tgzip.engine.check_archive', checker):
            await self.engine.verify_job(job)
        checker.assert_not_awaited()
        self.assertIn('Multipart', self.job()['verification_error'])
        self.assertTrue(self.source.exists())

    async def test_check_cancel_preserves_completed_download(self):
        entered = asyncio.Event()
        async def wait(*args):
            entered.set()
            await asyncio.sleep(60)
        with patch.object(self.engine, 'checker', return_value=Path('WinRAR.exe')), patch('tgzip.engine.check_archive', wait):
            self.engine.launch('verifying', self.engine.verify_queue)
            await entered.wait()
            await self.engine.cancel()
        self.assertEqual(self.job()['state'], 'done')
        self.assertEqual(self.job()['verification'], 'pending')
        self.assertTrue(self.source.exists())

    async def test_crash_between_move_and_db_commit_recovers(self):
        destination = quarantine_destination(self.source)
        self.engine.store.update(1, -10, 9, verification='quarantining', quarantine_path=str(destination), verification_error='Password required')
        move_to_quarantine(self.source, destination)
        self.engine.snapshot()
        self.assertEqual(self.job()['verification'], 'quarantined')
        self.assertEqual(destination.read_bytes(), b'fixture archive bytes')

    async def test_quarantine_collision_preserves_both_files(self):
        first = quarantine_destination(self.source)
        first.write_bytes(b'existing quarantined archive')
        await self.verify(CheckResult('bad', 'bad', 3))
        second = Path(self.job()['quarantine_path'])
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), b'existing quarantined archive')
        self.assertEqual(second.read_bytes(), b'fixture archive bytes')

    async def test_late_destination_collision_never_overwrites(self):
        destination = quarantine_destination(self.source)
        destination.write_bytes(b'created by someone else')
        with self.assertRaises(OSError):
            move_to_quarantine(self.source, destination)
        self.assertTrue(self.source.exists())
        self.assertEqual(destination.read_bytes(), b'created by someone else')

    async def test_download_checks_only_after_final_file_and_cancel_keeps_done(self):
        self.engine.store.update(1, -10, 9, state='queued', done=0)
        self.source.unlink()
        entered = asyncio.Event()
        async def transfer(client, message, job, gate, progress):
            self.source.write_bytes(b'fixture archive bytes')
            progress(job['size'], 0)
        async def check(*args):
            self.assertTrue(self.source.exists())
            self.assertEqual(self.job()['state'], 'done')
            entered.set()
            await asyncio.sleep(60)
        self.engine.dialogs = {-10: SimpleNamespace(input_entity=-10)}
        self.engine.client = SimpleNamespace(get_messages=AsyncMock(return_value=SimpleNamespace(document=SimpleNamespace(id=3, size=self.job()['size']))))
        with patch.object(self.engine, 'checker', return_value=Path('WinRAR.exe')), patch('tgzip.engine.download', transfer), patch('tgzip.engine.check_archive', check):
            self.engine.launch('downloading', self.engine.download_queue)
            await asyncio.wait_for(entered.wait(), 1)
            await self.engine.cancel()
        self.assertEqual(self.job()['state'], 'done')
        self.assertEqual(self.job()['verification'], 'pending')
        self.assertTrue(self.source.exists())
