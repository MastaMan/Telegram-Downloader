"""Shared bandwidth budgeting, live updates, and transfer cancellation."""
import asyncio
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest

from tgzip.storage import CHUNK, SPEED_LIMITS, speed_limit
from tgzip.transfer import DownloadLimiter, download
from test_core import FakeClient, zip_bytes


class SpeedLimitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.gate = asyncio.Event()
        self.gate.set()

    def test_default_presets_and_invalid_saved_values(self):
        self.assertEqual(SPEED_LIMITS, (0, 10, 5, 2, 1, .5, .25))
        for value in SPEED_LIMITS:
            self.assertEqual(speed_limit(str(value)), value)
        for value in (None, 'invalid', -1, .1, 100, float('nan'), float('inf'), {}, []):
            self.assertEqual(speed_limit(value), 0)

    async def test_twenty_workers_share_one_budget(self):
        limiter = DownloadLimiter(.25)
        started = time.monotonic()
        await asyncio.gather(*(limiter.acquire(4096, self.gate) for _ in range(20)))
        # 80 KiB at 256 KiB/s needs 0.3125 s, not 0.015625 s per independent worker.
        self.assertGreaterEqual(time.monotonic() - started, .30)

    async def test_unlimited_does_not_wait_for_byte_budget(self):
        limiter = DownloadLimiter()
        await asyncio.wait_for(asyncio.gather(
            *(limiter.acquire(CHUNK, self.gate) for _ in range(20))), 1)

    async def test_switching_to_unlimited_wakes_all_waiters(self):
        limiter = DownloadLimiter(.25)
        tasks = [asyncio.create_task(limiter.acquire(CHUNK, self.gate)) for _ in range(20)]
        await asyncio.sleep(.01)
        self.assertTrue(all(not task.done() for task in tasks))
        limiter.set_limit(0)
        await asyncio.wait_for(asyncio.gather(*tasks), .5)

    async def test_increasing_limit_recalculates_current_wait(self):
        limiter = DownloadLimiter(.25)
        task = asyncio.create_task(limiter.acquire(CHUNK, self.gate))
        await asyncio.sleep(.01)
        limiter.set_limit(10)
        await asyncio.wait_for(task, .5)

    async def test_decreasing_limit_recalculates_current_wait(self):
        limiter = DownloadLimiter(10)
        task = asyncio.create_task(limiter.acquire(CHUNK, self.gate))
        await asyncio.sleep(.005)
        limiter.set_limit(.25)
        await asyncio.sleep(.08)
        self.assertFalse(task.done())
        limiter.set_limit(0)
        await asyncio.wait_for(task, .5)

    async def test_canceling_first_waiter_does_not_block_following_file(self):
        limiter = DownloadLimiter(.25)
        first = asyncio.create_task(limiter.acquire(CHUNK, self.gate))
        await asyncio.sleep(.01)
        second = asyncio.create_task(limiter.acquire(1024, self.gate))
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        await asyncio.wait_for(second, .5)

    async def test_pause_and_limit_change_do_not_start_reads_until_resume(self):
        limiter = DownloadLimiter(.25)
        task = asyncio.create_task(limiter.acquire(CHUNK, self.gate))
        await asyncio.sleep(.01)
        self.gate.clear()
        limiter.reset()
        limiter.set_limit(0)
        await asyncio.sleep(.02)
        self.assertFalse(task.done())
        limiter.reset()
        self.gate.set()
        await asyncio.wait_for(task, .5)

    async def test_resume_does_not_spend_credit_accumulated_while_paused(self):
        limiter = DownloadLimiter(.25)
        self.gate.clear()
        task = asyncio.create_task(limiter.acquire(32768, self.gate))
        await asyncio.sleep(.15)
        limiter.reset()
        self.gate.set()
        await asyncio.sleep(.04)
        self.assertFalse(task.done())
        limiter.set_limit(0)
        await asyncio.wait_for(task, .5)

    async def test_limiter_runs_before_network_read_and_stop_keeps_resumable_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'original.ZIP'
            data = zip_bytes()
            client = FakeClient(data)
            job = {'path': str(target), 'size': len(data)}
            limiter = DownloadLimiter(.25)
            task = asyncio.create_task(download(client, SimpleNamespace(media='media'), job,
                                                self.gate, lambda *args: None, limiter=limiter))
            await asyncio.sleep(.02)
            self.assertEqual(client.streams[0].reads, 0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(client.streams[0].closed)
            self.assertEqual(target.with_name(target.name + '.part').stat().st_size, 0)
            limiter.set_limit(0)
            await download(client, SimpleNamespace(media='media'), job,
                           self.gate, lambda *args: None, limiter=limiter)
            self.assertEqual(target.read_bytes(), data)
            self.assertFalse(target.with_name(target.name + '.part').exists())
