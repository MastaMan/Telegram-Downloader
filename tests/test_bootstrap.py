"""Launcher runtime checks, without opening setup or contacting Telegram."""
from collections import namedtuple
from pathlib import Path
import struct
import os
import tempfile
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

import bootstrap

Version = namedtuple('Version', 'major minor micro releaselevel serial')


class BootstrapTests(unittest.TestCase):
    def runtime(self, minor, release='final', bits=8, free_threaded=0):
        with patch.object(sys, 'version_info', Version(3, minor, 0, release, 0)), \
             patch.object(bootstrap.sysconfig, 'get_config_var', return_value=free_threaded), \
             patch.object(struct, 'calcsize', return_value=bits):
            return bootstrap.runtime_error()

    def test_stable_versions_include_314_and_315(self):
        for minor in range(11, 16):
            with self.subTest(minor=minor):
                self.assertIsNone(self.runtime(minor))

    def test_unknown_future_and_old_runtimes_are_rejected(self):
        for minor in [10, 16]:
            with self.subTest(minor=minor):
                self.assertIn('3.11-3.15', self.runtime(minor))

    def test_prereleases_do_not_pass_the_launcher_probe(self):
        for release in ['alpha', 'beta', 'candidate']:
            with self.subTest(release=release):
                self.assertIn('stable', self.runtime(15, release=release))

    def test_free_threaded_and_32_bit_builds_are_rejected(self):
        self.assertIn('free-threaded', self.runtime(15, free_threaded=1))
        self.assertIn('64-bit', self.runtime(15, bits=4))

    def test_cryptg_marker_keeps_python315_installable(self):
        manifest = 'telethon==1.45.0\ncryptg==0.6.0; python_version < "3.15"\n'
        for minor in range(11, 16):
            with self.subTest(minor=minor), patch.object(sys, 'version_info', Version(3, minor, 0, 'final', 0)):
                packages = dict(bootstrap.required_packages(manifest))
                self.assertEqual('cryptg' in packages, minor < 15)
                self.assertEqual(packages['telethon'], '1.45.0')
        with self.assertRaises(ValueError):
            list(bootstrap.required_packages('cryptg==0.6.0; unknown_marker'))

    @unittest.skipIf(sys.version_info[:2] >= (3, 15), 'cryptg has no CPython 3.15 wheel')
    def test_cryptg_is_used_and_matches_python_encryption(self):
        import cryptg
        from telethon.crypto import aes
        self.assertIs(aes.cryptg, cryptg)
        data = bytes(range(256)) * 4
        key, iv = bytes(range(32)), bytes(reversed(range(32)))
        encrypted = aes.AES.encrypt_ige(data, key, iv)
        with patch.object(aes, 'cryptg', None), \
             patch.object(aes.libssl, 'encrypt_ige', None), \
             patch.object(aes.libssl, 'decrypt_ige', None):
            self.assertEqual(aes.AES.encrypt_ige(data, key, iv), encrypted)
            self.assertEqual(aes.AES.decrypt_ige(encrypted, key, iv), data)
        self.assertEqual(aes.AES.decrypt_ige(encrypted, key, iv), data)

    def test_probe_does_not_create_environment_or_launch_app(self):
        with patch.object(sys, 'argv', ['bootstrap.py', '--check-python']), \
             patch.object(bootstrap, 'check_environment') as check, \
             patch.object(bootstrap, 'setup_window') as setup, \
             patch.object(bootstrap, 'launch') as launch, \
             patch.object(bootstrap, 'runtime_error', return_value=None):
            self.assertEqual(bootstrap.main(), 0)
            check.assert_not_called()
            setup.assert_not_called()
            launch.assert_not_called()

    def test_probe_returns_failure_for_unsupported_runtime(self):
        with patch.object(sys, 'argv', ['bootstrap.py', '--check-python']), \
             patch.object(bootstrap, 'runtime_error', return_value='Unsupported runtime'), \
             patch('builtins.print'):
            self.assertEqual(bootstrap.main(), 1)

    def test_broken_or_unresponsive_environment_needs_setup(self):
        with patch.object(bootstrap, 'PYTHON', Path(sys.executable)):
            for error in [OSError('interpreter unavailable'), subprocess.TimeoutExpired('python', 45)]:
                with self.subTest(error=type(error).__name__), \
                     patch.object(bootstrap.subprocess, 'run', side_effect=error):
                    self.assertFalse(bootstrap.check_environment())

    def test_python315_qt_import_order_keeps_asyncio_threads_working(self):
        result = subprocess.run([sys.executable, '-c',
            'import tgzip; from PySide6.QtCore import QObject; import asyncio; asyncio.run(asyncio.to_thread(lambda: None))'],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_windows_update_from_python315_returns_through_runtime_selector(self):
        with patch.object(bootstrap, 'WINDOWS', True), \
             patch.object(sys, 'argv', ['bootstrap.py']), \
             patch.object(sys, 'version_info', Version(3, 15, 0, 'final', 0)), \
             patch.dict(os.environ, {'TD_RUNTIME_READY': '1'}), \
             patch('updater.startup_ready', return_value=True), \
             patch.object(bootstrap, 'app_running', return_value=False), \
             patch.object(bootstrap, 'windows_launcher', return_value=0) as launcher:
            self.assertEqual(bootstrap.main(), 0)
            launcher.assert_called_once_with([])

    def test_running_app_prevents_environment_setup(self):
        with patch.object(bootstrap, 'WINDOWS', True), \
             patch.object(sys, 'argv', ['bootstrap.py']), \
             patch('updater.startup_ready', return_value=True), \
             patch.object(bootstrap, 'app_running', return_value=True), \
             patch.object(bootstrap, 'windows_launcher') as launcher, \
             patch.object(bootstrap, 'setup_window') as setup, patch('builtins.print'):
            self.assertEqual(bootstrap.main(), 0)
            launcher.assert_not_called()
            setup.assert_not_called()

    def test_runtime_selector_passes_paths_and_arguments_as_environment_data(self):
        with patch.object(bootstrap.subprocess, 'run') as run:
            run.return_value.returncode = 0
            self.assertEqual(bootstrap.windows_launcher(['--screenshot', "folder's image.png"]), 0)
            environment = run.call_args.kwargs['env']
            self.assertEqual(json.loads(environment['TD_LAUNCH_ARGS']), ['--screenshot', "folder's image.png"])
            self.assertNotIn('TD_RUNTIME_READY', environment)
            self.assertNotIn("folder's image.png", run.call_args.args[0][-1])
            self.assertIn('POWERSHELL_BEGIN', run.call_args.args[0][-1])

    def test_new_environment_preserves_previous_environment_and_user_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous = root / '.venv'
            previous.mkdir()
            (previous / 'fixture').write_text('old runtime')
            new = root / '.venv-py314'
            new.mkdir()
            (new / 'broken').write_text('keep for recovery')
            data = root / 'queue.sqlite3'
            data.write_bytes(b'untouched fixture')
            with patch.object(bootstrap, 'ENV', new), \
                 patch.object(bootstrap, 'environment_runtime_ready', return_value=False), \
                 patch.object(bootstrap.venv, 'EnvBuilder') as builder:
                bootstrap.prepare_environment()
                builder.return_value.create.assert_called_once_with(new)
            self.assertEqual((previous / 'fixture').read_text(), 'old runtime')
            backups = list(root.glob('.venv-py314.previous-*'))
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / 'broken').read_text(), 'keep for recovery')
            self.assertEqual(data.read_bytes(), b'untouched fixture')

    def test_healthy_environment_is_reused(self):
        with patch.object(bootstrap, 'environment_runtime_ready', return_value=True), \
             patch.object(bootstrap.venv, 'EnvBuilder') as builder:
            bootstrap.prepare_environment()
            builder.assert_not_called()

    def test_installed_pinned_dependencies_load_in_a_subprocess(self):
        with patch.object(bootstrap, 'PYTHON', Path(sys.executable)):
            self.assertEqual(bootstrap.check_environment(), not (os.name == 'nt' and sys.version_info[:2] != (3, 14)))


if __name__ == '__main__':
    unittest.main()
