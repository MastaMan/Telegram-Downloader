@echo off
setlocal
cd /d "%~dp0"
set "TD_START_FILE=%~f0"
set "TD_LAUNCH_ARGS="
if /i "%~1"=="--demo" set "TD_LAUNCH_ARGS=["--demo"]"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$s=[IO.File]::ReadAllText($env:TD_START_FILE); & ([ScriptBlock]::Create(($s -split '(?m)^# POWERSHELL_BEGIN\r?\n',2)[1]))"
if errorlevel 1 (
    echo.
    echo See setup.log for details. Check your connection and run Start.bat again.
    pause
    exit /b 1
)
exit /b 0
# POWERSHELL_BEGIN
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $env:TD_START_FILE
$runtimeHome = Join-Path $env:LOCALAPPDATA 'TelegramZipDesktop\runtime'
$runtimeDir = Join-Path $runtimeHome 'python-3.14.8'
$runtimeUrl = 'https://www.python.org/ftp/python/3.14.8/python-3.14.8-amd64.zip'
$runtimeHash = '4873947a8afc037846b180312b83c744a4146a851cfd316a75c3125a4d8299da'

function Test-Python([string] $executable, [string] $prefix = '') {
    if (-not $executable -or -not (Test-Path -LiteralPath $executable -PathType Leaf)) { return $null }
    $probe = New-Object System.Diagnostics.Process
    try {
        $probe.StartInfo.FileName = $executable
        $code = 'import sys,struct,sysconfig,tkinter,venv,ensurepip; assert sys.implementation.name==''cpython'' and sys.version_info[:2]==(3,14) and sys.version_info.releaselevel==''final'' and struct.calcsize(''P'')==8 and not sysconfig.get_config_var(''Py_GIL_DISABLED''); print(sys._base_executable)'
        $probe.StartInfo.Arguments = "$prefix -X utf8 -I -c `"$code`""
        $probe.StartInfo.UseShellExecute = $false
        $probe.StartInfo.CreateNoWindow = $true
        $probe.StartInfo.RedirectStandardOutput = $true
        $probe.StartInfo.RedirectStandardError = $true
        $probe.StartInfo.StandardOutputEncoding = [Text.Encoding]::UTF8
        $probe.StartInfo.StandardErrorEncoding = [Text.Encoding]::UTF8
        if (-not $probe.Start()) { return $null }
        $output = $probe.StandardOutput.ReadToEndAsync()
        $errors = $probe.StandardError.ReadToEndAsync()
        if (-not $probe.WaitForExit(20000)) { $probe.Kill(); return $null }
        if ($probe.ExitCode -eq 0) {
            $candidate = $output.Result.Trim()
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
        }
    } catch { return $null } finally { $probe.Dispose() }
    return $null
}

function Find-Python {
    foreach ($candidate in @($env:TD_PREFERRED_PYTHON,
                             (Join-Path $root '.venv-py314\Scripts\python.exe'),
                             (Join-Path $root '.venv\Scripts\python.exe'),
                             (Join-Path $runtimeDir 'python.exe'))) {
        $found = Test-Python $candidate
        if ($found) { return $found }
    }
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        $env:PYTHON_MANAGER_AUTOMATIC_INSTALL = 'false'
        $found = Test-Python $py.Source '-3.14'
        if ($found) { return $found }
    }
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python -and $python.Source -notmatch '\\WindowsApps\\') {
        $found = Test-Python $python.Source
        if ($found) { return $found }
    }
    return $null
}

function Install-Python {
    $stage = Join-Path $runtimeHome ('stage-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $stage | Out-Null
    try {
        $archive = Join-Path $stage 'python.zip'
        Write-Host 'Downloading Python 3.14 for Telegram Archive Desktop...'
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -Uri $runtimeUrl -OutFile $archive -TimeoutSec 300
        if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $runtimeHash) {
            throw 'Python download verification failed. Run Start.bat again to retry.'
        }
        Write-Host 'Preparing the private Python runtime...'
        $unpacked = Join-Path $stage 'python'
        Expand-Archive -LiteralPath $archive -DestinationPath $unpacked
        if (-not (Test-Python (Join-Path $unpacked 'python.exe'))) {
            throw 'The downloaded Python cannot run. Windows x64 is required.'
        }
        if (Test-Path -LiteralPath $runtimeDir) {
            Move-Item -LiteralPath $runtimeDir -Destination ($runtimeDir + '.previous-' + [Guid]::NewGuid().ToString('N'))
        }
        Move-Item -LiteralPath $unpacked -Destination $runtimeDir
        $found = Test-Python (Join-Path $runtimeDir 'python.exe')
        if (-not $found) { throw 'Python preparation failed. Run Start.bat again.' }
        return $found
    } finally {
        if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
    }
}

function Get-PythonRuntime {
    New-Item -ItemType Directory -Force -Path $runtimeHome | Out-Null
    $guard = $null
    try {
        try {
            $guard = [IO.File]::Open((Join-Path $runtimeHome 'setup.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
        } catch [IO.IOException] {
            Write-Host 'Python setup is already running. Please use the existing window.'
            return $null
        }
        $found = Find-Python
        if ($found) { return $found }
        return Install-Python
    } finally { if ($guard) { $guard.Dispose() } }
}

# RUN_LAUNCHER
try {
    $python = Get-PythonRuntime
    if (-not $python) { exit 0 }
    $env:TD_RUNTIME_READY = '1'
    $launchArgs = @()
    if ($env:TD_LAUNCH_ARGS) { $launchArgs = @(ConvertFrom-Json $env:TD_LAUNCH_ARGS) }
    & $python (Join-Path $root 'bootstrap.py') @launchArgs
    exit $LASTEXITCODE
} catch {
    $detail = 'Startup could not finish: ' + $_.Exception.Message
    Write-Host $detail -ForegroundColor Red
    try { Add-Content -LiteralPath (Join-Path $root 'setup.log') -Value $detail } catch {}
    exit 1
}
