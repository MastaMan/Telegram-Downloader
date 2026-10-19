"""Launcher runtime checks, without opening setup or contacting Telegram."""
from collections import namedtuple
from pathlib import Path
import struct
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

    def test_installed_pinned_dependencies_load_in_a_subprocess(self):
        with patch.object(bootstrap, 'PYTHON', Path(sys.executable)):
            self.assertTrue(bootstrap.check_environment())


if __name__ == '__main__':
    unittest.main()
