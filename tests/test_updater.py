"""Update staging, boundaries, rollback and restart handoff without network writes."""
import io
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import zipfile

import updater

SHA = 'a' * 40


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'application'
        self.root.mkdir()
        self.cancel = threading.Event()
        self.files = {name: (b'value = 42\n' if name.endswith('.py') else b'new application file\n') for name in updater.REQUIRED}
        self.files['tgzip/new_module.py'] = b'answer = 42\n'

    def tearDown(self):
        self.temp.cleanup()

    def archive(self, extra=None):
        content = io.BytesIO()
        with zipfile.ZipFile(content, 'w') as package:
            for name, value in {**self.files, **(extra or {})}.items():
                package.writestr('repository-' + SHA + '/' + name, value)
        return content.getvalue()

    def fetch(self, url, target, limit, cancel, report):
        updater.cancelled(cancel)
        data = json.dumps({'sha': SHA}).encode() if '/commits/' in url else self.archive()
        Path(target).write_bytes(data)

    def prepare(self):
        with patch('updater.fetch', self.fetch):
            return Path(updater.prepare_update(self.root, lambda text: None, self.cancel))

    def test_zip_stage_install_and_second_check_is_current(self):
        for name in ['settings.json', 'queue.sqlite3', 'credentials.dpapi', 'Archive.zip', '.venv/keep.txt', 'downloads/file.rar']:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'private local data')
        (self.root / 'bootstrap.py').write_bytes(b'old = True\n')
        plan = self.prepare()
        self.assertEqual((self.root / 'bootstrap.py').read_bytes(), b'old = True\n')
        updater.install(plan)
        self.assertEqual((self.root / 'bootstrap.py').read_bytes(), self.files['bootstrap.py'])
        for name in ['settings.json', 'queue.sqlite3', 'credentials.dpapi', 'Archive.zip', '.venv/keep.txt', 'downloads/file.rar']:
            self.assertEqual((self.root / name).read_bytes(), b'private local data')
        with patch('updater.fetch', self.fetch):
            self.assertIsNone(updater.prepare_update(self.root, lambda text: None, self.cancel))
        self.assertEqual(json.loads((self.root / updater.STAMP).read_text())['sha'], SHA)

    def test_initial_identical_zip_install_does_not_restart(self):
        for name, data in self.files.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        with patch('updater.fetch', self.fetch):
            self.assertIsNone(updater.prepare_update(self.root, lambda text: None, self.cancel))
        self.assertEqual(list((self.root / '.updates').iterdir()), [])
        self.assertTrue((self.root / updater.STAMP).exists())

    def test_failed_replacement_restores_original_and_removes_new_files(self):
        (self.root / 'bootstrap.py').write_bytes(b'original = True\n')
        plan = self.prepare()
        real = updater.replace_file
        calls = []
        def fail_once(source, target):
            calls.append(str(target))
            if len(calls) == 3:
                raise OSError('simulated locked file')
            return real(source, target)
        with patch('updater.replace_file', fail_once), self.assertRaises(OSError):
            updater.install(plan)
        self.assertEqual((self.root / 'bootstrap.py').read_bytes(), b'original = True\n')
        self.assertFalse((self.root / 'tgzip/new_module.py').exists())
        self.assertFalse((self.root / updater.STAMP).exists())
        self.assertEqual(json.loads((plan.parent / 'transaction.json').read_text())['status'], 'rolled_back')

    def test_changed_staged_file_is_rejected_before_install(self):
        plan = self.prepare()
        (plan.parent / 'source/bootstrap.py').write_bytes(b'changed = True\n')
        with self.assertRaises(updater.UpdateError):
            updater.install(plan)
        self.assertFalse((self.root / 'bootstrap.py').exists())

    def test_only_previously_managed_obsolete_files_are_removed(self):
        (self.root / 'tgzip').mkdir()
        (self.root / 'tgzip/obsolete.py').write_text('old = True')
        (self.root / 'tgzip/user_script.py').write_text('custom = True')
        updater.atomic_json(self.root / updater.STAMP, {'sha': 'b' * 40, 'files': {'tgzip/obsolete.py': 'old hash'}})
        updater.install(self.prepare())
        self.assertFalse((self.root / 'tgzip/obsolete.py').exists())
        self.assertTrue((self.root / 'tgzip/user_script.py').exists())

    def test_cancelled_preparation_cleans_stage_without_touching_app(self):
        self.cancel.set()
        with patch('updater.fetch', self.fetch), self.assertRaises(updater.UpdateCancelled):
            updater.prepare_update(self.root, lambda text: None, self.cancel)
        self.assertEqual(list((self.root / '.updates').iterdir()), [])

    def test_bad_archive_and_python_syntax_are_rejected(self):
        for contents in [b'not a zip', self.archive({'tgzip/bad.py': b'def invalid !!!'})]:
            def fetch(url, target, *args):
                Path(target).write_bytes(json.dumps({'sha': SHA}).encode() if '/commits/' in url else contents)
            with patch('updater.fetch', fetch), self.assertRaises(updater.UpdateError):
                updater.prepare_update(self.root, lambda text: None, self.cancel)
        self.assertFalse((self.root / 'bootstrap.py').exists())

    def test_archive_traversal_duplicates_and_symlinks_are_rejected(self):
        cases = [('repo/../../escape.py', b'x'), ('repository-' + SHA + '/tgzip/UI.py', b'x')]
        for name, data in cases:
            content = io.BytesIO(self.archive())
            with zipfile.ZipFile(content, 'a') as package:
                package.writestr(name, data)
            archive = self.root / 'invalid.zip'
            archive.write_bytes(content.getvalue())
            with self.assertRaises(updater.UpdateError):
                updater.unpack(archive, self.root / 'source', self.cancel)
        link = zipfile.ZipInfo('repository-' + SHA + '/tgzip/link.py')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(self.root / 'link.zip', 'w') as package:
            package.writestr(link, '/outside')
        with self.assertRaises(updater.UpdateError):
            updater.unpack(self.root / 'link.zip', self.root / 'source', self.cancel)

    def test_unmanaged_archive_files_never_enter_install_manifest(self):
        path = self.root / 'archive.zip'
        path.write_bytes(self.archive({'.git/config': b'no', '.venv/steal.py': b'no', 'settings.json': b'no', 'credentials.dpapi': b'no'}))
        files = updater.unpack(path, self.root / 'source', self.cancel)
        self.assertEqual(set(files), set(self.files))
        self.assertFalse((self.root / 'source/settings.json').exists())

    def test_redirects_cannot_leave_github_https(self):
        handler = updater.GitHubRedirect()
        for location in ['http://github.com/update.zip', 'https://example.com/update.zip', 'https://github.com.evil.test/source']:
            with self.assertRaises(updater.UpdateError):
                handler.redirect_request(None, None, 302, '', {}, location)

    def test_failed_or_oversized_download_does_not_install(self):
        response = io.BytesIO(b'x' * 20)
        opener = Mock()
        opener.open.return_value = response
        with patch('updater.urllib.request.build_opener', return_value=opener), self.assertRaises(updater.UpdateError):
            updater.fetch('https://api.github.com/test', self.root / 'download', 10, self.cancel, lambda text: None)
        self.assertFalse((self.root / 'bootstrap.py').exists())

    def test_git_checkout_uses_fast_forward_and_rechecks_local_changes(self):
        (self.root / '.git').mkdir()
        calls = []
        def git(root, args, *rest, **kwargs):
            calls.append(args)
            if args == ['rev-parse', 'HEAD']:
                return 0, 'b' * 40
            if args == ['branch', '--show-current']:
                return 0, 'main'
            return 0, ''
        with patch('updater.run_git', git):
            plan = self.prepare()
            self.assertFalse((plan.parent / 'source.zip').exists())
            updater.install(plan)
        self.assertIn(['merge', '--ff-only', SHA], calls)
        self.assertEqual(calls.count(['status', '--porcelain', '--untracked-files=no']), 2)

    def test_dirty_git_checkout_is_not_updated(self):
        (self.root / '.git').mkdir()
        def git(root, args, *rest, **kwargs):
            if args == ['rev-parse', 'HEAD']:
                return 0, 'b' * 40
            if args == ['branch', '--show-current']:
                return 0, 'main'
            return 0, ' M tgzip/ui.py'
        with patch('updater.run_git', git), patch('updater.fetch', self.fetch), self.assertRaises(updater.UpdateError):
            updater.prepare_update(self.root, lambda text: None, self.cancel)
        self.assertFalse((self.root / 'bootstrap.py').exists())

    def test_interrupted_zip_install_rolls_back_on_next_start(self):
        plan = self.prepare()
        backup = plan.parent / 'backup'
        backup.mkdir()
        (backup / 'bootstrap.py').write_text('old = True')
        (self.root / 'bootstrap.py').write_text('new = True')
        updater.atomic_json(plan.parent / 'transaction.json', {'status': 'applying', 'original': {'bootstrap.py': True}})
        updater.atomic_json(self.root / updater.MARKER, {'pid': 123, 'plan': str(plan)})
        with patch('updater.process_alive', return_value=False):
            self.assertTrue(updater.startup_ready(self.root))
        self.assertEqual((self.root / 'bootstrap.py').read_text(), 'old = True')
        self.assertFalse((self.root / updater.MARKER).exists())

    def test_active_installer_blocks_second_launcher(self):
        plan = self.prepare()
        updater.atomic_json(self.root / updater.MARKER, {'pid': 123, 'plan': str(plan)})
        with patch('updater.process_alive', return_value=True):
            self.assertFalse(updater.startup_ready(self.root))

    def test_handoff_runs_separate_helper_and_waits_for_current_process(self):
        plan = self.prepare()
        with patch('updater.subprocess.Popen', return_value=Mock(pid=123)) as popen:
            updater.start_installer(plan)
        args = popen.call_args.args[0]
        self.assertEqual(args[-2:], ['--wait-pid', str(os.getpid())])
        self.assertEqual(Path(args[1]), plan.parent / 'helper.py')
        self.assertEqual(json.loads((self.root / updater.MARKER).read_text())['pid'], 123)

    def test_restart_returns_through_dependency_bootstrap(self):
        with patch('updater.subprocess.Popen') as popen:
            updater.resume_launcher(self.root)
        self.assertEqual(Path(popen.call_args.args[0][1]), self.root / 'bootstrap.py')

    def test_real_helper_waits_for_exit_installs_and_restarts(self):
        import subprocess
        import sys
        import time
        self.files['bootstrap.py'] = b"import os\nfrom pathlib import Path\nPath(__file__).with_name('restarted.pid').write_text(str(os.getpid()))\nPath(__file__).with_name('restarted.txt').write_text('ready')\n"
        (self.root / 'bootstrap.py').write_text('original = True')
        plan = self.prepare()
        parent = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        helper = subprocess.Popen([sys.executable, str(plan.parent / 'helper.py'), '--install', str(plan), '--wait-pid', str(parent.pid)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            time.sleep(.15)
            self.assertIsNone(helper.poll())
            self.assertEqual((self.root / 'bootstrap.py').read_text(), 'original = True')
            parent.terminate()
            parent.wait(timeout=3)
            out, err = helper.communicate(timeout=5)
            self.assertEqual(helper.returncode, 0, err.decode())
            deadline = time.monotonic() + 3
            while not (self.root / 'restarted.txt').exists() and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual((self.root / 'restarted.txt').read_text(), 'ready')
            restart_pid = int((self.root / 'restarted.pid').read_text())
            while updater.process_alive(restart_pid) and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertFalse(updater.process_alive(restart_pid))
            self.assertEqual(json.loads((self.root / updater.STAMP).read_text())['sha'], SHA)
        finally:
            for process in (parent, helper):
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=3)

    @unittest.skipUnless(shutil.which('git'), 'Git is required for checkout integration')
    def test_real_git_install_fast_forwards_without_removing_untracked_data(self):
        import subprocess
        environment = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        def git(*args):
            result = subprocess.run(['git', '-c', 'user.name=Update test', '-c', 'user.email=update@example.invalid',
                                     '-c', 'commit.gpgsign=false', *args], cwd=self.root, env=environment,
                                    capture_output=True, text=True, check=True)
            return result.stdout.strip()
        git('init', '-b', 'main')
        (self.root / 'app.py').write_text('old = True')
        git('add', 'app.py')
        git('commit', '-m', 'Old fixture')
        previous = git('rev-parse', 'HEAD')
        (self.root / 'app.py').write_text('new = True')
        git('commit', '-am', 'New fixture')
        latest = git('rev-parse', 'HEAD')
        git('reset', '--hard', previous)
        (self.root / 'my-download.rar').write_bytes(b'keep this')
        stage = self.root / '.updates' / 'fixture'
        stage.mkdir(parents=True)
        updater.atomic_json(stage / 'plan.json', {'root': str(self.root), 'mode': 'git', 'previous': previous, 'sha': latest})
        with patch.dict(os.environ, environment):
            updater.install(stage / 'plan.json')
        self.assertEqual(git('rev-parse', 'HEAD'), latest)
        self.assertEqual((self.root / 'app.py').read_text(), 'new = True')
        self.assertEqual((self.root / 'my-download.rar').read_bytes(), b'keep this')

    def test_cancelled_git_operation_reaps_its_process(self):
        import sys
        import time
        outcome = []
        code = "import os, time; from pathlib import Path; Path('child.pid').write_text(str(os.getpid())); time.sleep(30)"
        def run():
            try:
                updater.run_git(self.root, ['-c', code], self.cancel)
            except updater.UpdateCancelled:
                outcome.append('cancelled')
        with patch('updater.shutil.which', return_value=sys.executable):
            worker = threading.Thread(target=run)
            worker.start()
            try:
                deadline = time.monotonic() + 3
                while not (self.root / 'child.pid').exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue((self.root / 'child.pid').exists())
            finally:
                self.cancel.set()
                worker.join(timeout=10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcome, ['cancelled'])
        self.assertFalse(updater.process_alive(int((self.root / 'child.pid').read_text())))
