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

    def test_selects_leftover_app_copies_and_old_scripts_only(self):
        self.run_script(r'''
$legacy = Join-Path $env:LOCALAPPDATA 'old downloader'
New-Item -ItemType Directory -Path $legacy | Out-Null
Set-Content -LiteralPath (Join-Path $legacy 'telegram_config.json') -Value '{}'
$other = Join-Path $env:LOCALAPPDATA 'other bot'
New-Item -ItemType Directory -Path $other | Out-Null
$app = Join-Path $root '.venv-py314\Scripts\pythonw.exe'
function P($id, $parent, $name, $line, $exe) {
    [pscustomobject]@{ ProcessId = $id; ParentProcessId = $parent; Name = $name; CommandLine = $line; ExecutablePath = $exe }
}
$list = @(
    (P 1 0 'explorer.exe' 'explorer.exe' 'C:\Windows\explorer.exe'),
    (P 10 1 'cmd.exe' 'cmd /c Start.bat' 'C:\Windows\System32\cmd.exe'),
    (P 11 10 'powershell.exe' 'powershell -Command launcher' 'powershell.exe'),
    (P 20 1 'pythonw.exe' "`"$app`" -m tgzip" $app),
    (P 21 1 'python.exe' "`"C:\Python\python.exe`" `"$(Join-Path $legacy 'downloader.py')`" --chat 5" 'C:\Python\python.exe'),
    (P 22 1 'python.exe' "python `"$(Join-Path $other 'bot.py')`"" 'C:\Python\python.exe'),
    (P 23 1 'notepad.exe' "notepad $root" 'C:\Windows\notepad.exe'),
    (P 24 1 'pythonw.exe' 'C:\Elsewhere\pythonw.exe -m tgzip' 'C:\Elsewhere\pythonw.exe'),
    (P 25 1 'python.exe' 'python -m tgzipper' 'C:\Python\python.exe')
)
$found = (@(Select-LeftoverProcesses $list $root 11) | ForEach-Object { "$($_.Id):$($_.Kind)" }) -join ','
if ($found -ne '20:app,21:script,24:app') { throw "Unexpected targets: $found" }
# The update helper and bootstrap that started this launcher are its ancestors.
$chain = @(
    (P 30 1 'python.exe' "`"$app`" `"$(Join-Path $root '.updates\stage\helper.py')`" --install plan" $app),
    (P 31 30 'python.exe' "`"$app`" `"$(Join-Path $root 'bootstrap.py')`"" $app),
    (P 32 31 'powershell.exe' 'powershell -Command launcher' 'powershell.exe')
)
if (@(Select-LeftoverProcesses $chain $root 32).Count) { throw 'Launcher ancestors were selected' }
$setup = @(
    (P 40 1 'python.exe' "`"$(Join-Path $root '.venv-py314\Scripts\python.exe')`" -m pip install -r requirements.txt" (Join-Path $root '.venv-py314\Scripts\python.exe')),
    (P 41 1 'pythonw.exe' "`"$app`" -m tgzip" $app)
)
if (@(Select-LeftoverProcesses $setup $root 99).Count) { throw 'Dependency installation was interrupted' }
''')

    def test_stop_closes_targets_logs_them_and_respects_pending_update(self):
        self.run_script(r'''
$target = Join-Path $env:LOCALAPPDATA 'install'
New-Item -ItemType Directory -Path $target | Out-Null
$app = Join-Path $target '.venv-py314\Scripts\pythonw.exe'
$script:stopped = @()
function Get-CimInstance { param($ClassName, $ErrorAction)
    @([pscustomobject]@{ ProcessId = 70; ParentProcessId = 1; Name = 'pythonw.exe'; CommandLine = "`"$app`" -m tgzip"; ExecutablePath = $app })
}
function Stop-Process { param($Id, [switch]$Force, $ErrorAction) $script:stopped += $Id }
function Wait-Process { param($Id, $Timeout, $ErrorAction) }
Stop-LeftoverProcesses $target
if (($script:stopped -join ',') -ne '70') { throw "Unexpected stops: $($script:stopped -join ',')" }
if (-not (Select-String -LiteralPath (Join-Path $target 'setup.log') -Pattern 'previous Telegram File Hunter process \(PID 70\)' -Quiet)) { throw 'Not logged' }
Set-Content -LiteralPath (Join-Path $target '.update-pending.json') -Value '{}'
function Get-CimInstance { throw 'Processes were queried during an update' }
Stop-LeftoverProcesses $target
if ($script:stopped.Count -ne 1) { throw 'A process was stopped during an update' }
''')
