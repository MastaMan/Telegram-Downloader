"""Execute launcher logic in PowerShell with offline fixture runtimes."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile

POWERSHELL = os.environ.get('TG_TEST_POWERSHELL') or shutil.which('powershell') or shutil.which('pwsh')
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(POWERSHELL, 'PowerShell is required for launcher execution tests')
class WindowsLauncherTests(unittest.TestCase):
    def run_script(self, body):
        with tempfile.TemporaryDirectory(prefix="telegram launcher's ") as directory:
            folder = Path(directory)
            archive = folder / 'fixture.zip'
            with zipfile.ZipFile(archive, 'w') as package:
                package.writestr('python.exe', 'fixture executable')
                package.writestr('Lib/venv/__init__.py', '# fixture')
            environment = os.environ.copy()
            environment.update(TD_START_FILE=str(ROOT / 'Start.bat'), LOCALAPPDATA=directory,
                               TD_FIXTURE_ZIP=str(archive), TD_FIXTURE_HASH=hashlib.sha256(archive.read_bytes()).hexdigest())
            script = folder / 'test.ps1'
            script.write_text(r'''
$ErrorActionPreference = 'Stop'
$source = [IO.File]::ReadAllText($env:TD_START_FILE)
$body = ($source -split '(?m)^# POWERSHELL_BEGIN\r?\n', 2)[1]
$tokens = $null; $parseErrors = $null
[void][System.Management.Automation.Language.Parser]::ParseInput($body, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
. ([ScriptBlock]::Create(($body -split '(?m)^# RUN_LAUNCHER', 2)[0]))
$runtimeHash = $env:TD_FIXTURE_HASH
function Invoke-WebRequest { param([switch]$UseBasicParsing, $Uri, $OutFile, $TimeoutSec)
    Copy-Item -LiteralPath $env:TD_FIXTURE_ZIP -Destination $OutFile
}
# Real probing of Windows binaries is intentionally replaced in these fixtures.
function Test-Python([string] $executable, [string] $prefix = '') {
    if (Test-Path -LiteralPath $executable -PathType Leaf) { return $executable }
    return $null
}
function Find-Python { return $null }
''' + body, encoding='utf-8')
            result = subprocess.run([POWERSHELL, '-NoLogo', '-NoProfile', '-File', str(script)],
                                    env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_bootstrap_trampoline_handles_paths_and_arguments(self):
        import bootstrap
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(prefix="launcher quote's ") as directory:
            root = Path(directory)
            (root / 'Start.bat').write_bytes(b'@echo off\r\nexit /b 0\r\n# POWERSHELL_BEGIN\r\n'
                b'$a = @(ConvertFrom-Json $env:TD_LAUNCH_ARGS)\r\n'
                b'if ($a.Count -ne 2 -or $a[0] -ne "--demo" -or $a[1] -ne "quote\'s path") { exit 9 }\r\nexit 0\r\n')
            # The value is data in JSON, never pasted into the PowerShell command.
            args = ['--demo', "quote's path"]
            original = subprocess.run
            def run(command, **kwargs):
                return original([POWERSHELL, *command[1:]], **kwargs)
            with patch.object(bootstrap, 'ROOT', root), patch.object(bootstrap.subprocess, 'run', side_effect=run):
                self.assertEqual(bootstrap.windows_launcher(args), 0)

    def test_download_hash_extract_and_publish_without_python(self):
        self.run_script(r'''
$found = Get-PythonRuntime
if ($found -ne (Join-Path $runtimeDir 'python.exe')) { throw 'Wrong runtime path' }
if (-not (Test-Path -LiteralPath $found)) { throw 'Runtime missing' }
if (@(Get-ChildItem -LiteralPath $runtimeHome -Filter 'stage-*').Count) { throw 'Staging leaked' }
''')

    def test_hash_mismatch_never_extracts_or_replaces_runtime(self):
        self.run_script(r'''
$runtimeHash = 'incorrect'
$failed = $false
try { Get-PythonRuntime } catch { $failed = $true }
if (-not $failed) { throw 'Invalid digest accepted' }
if (Test-Path -LiteralPath $runtimeDir) { throw 'Invalid runtime published' }
if (@(Get-ChildItem -LiteralPath $runtimeHome -Filter 'stage-*').Count) { throw 'Staging leaked' }
''')

    def test_existing_python_does_not_download(self):
        self.run_script(r'''
function Find-Python { return 'existing interpreter' }
function Invoke-WebRequest { throw 'Unexpected download' }
if ((Get-PythonRuntime) -ne 'existing interpreter') { throw 'Installed Python ignored' }
''')

    def test_failed_download_keeps_previous_runtime(self):
        self.run_script(r'''
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
Set-Content -LiteralPath (Join-Path $runtimeDir 'keep.txt') -Value 'previous runtime'
function Invoke-WebRequest { throw 'Fixture offline' }
$failed = $false
try { Get-PythonRuntime } catch { $failed = $true }
if (-not $failed) { throw 'Download failure hidden' }
if (-not (Test-Path -LiteralPath (Join-Path $runtimeDir 'keep.txt'))) { throw 'Previous runtime lost' }
if (@(Get-ChildItem -LiteralPath $runtimeHome -Filter 'stage-*').Count) { throw 'Staging leaked' }
''')

    def test_invalid_existing_runtime_is_preserved_on_replacement(self):
        self.run_script(r'''
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
Set-Content -LiteralPath (Join-Path $runtimeDir 'keep.txt') -Value 'previous runtime'
$found = Get-PythonRuntime
$backups = @(Get-ChildItem -LiteralPath $runtimeHome -Filter 'python-3.14.8.previous-*')
if ($backups.Count -ne 1) { throw 'Previous runtime was not retained' }
if (-not (Test-Path -LiteralPath (Join-Path $backups[0].FullName 'keep.txt'))) { throw 'Backup missing' }
if (-not (Test-Path -LiteralPath $found)) { throw 'Replacement missing' }
''')

    def test_second_launcher_does_not_install_during_setup(self):
        self.run_script(r'''
New-Item -ItemType Directory -Force -Path $runtimeHome | Out-Null
$guard = [IO.File]::Open((Join-Path $runtimeHome 'setup.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
try {
    function Find-Python { throw 'Concurrent setup entered selection' }
    if ($null -ne (Get-PythonRuntime)) { throw 'Concurrent setup continued' }
} finally { $guard.Dispose() }
''')
