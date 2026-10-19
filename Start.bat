@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    for %%V in (3.15 3.14 3.13 3.12 3.11) do (
        py -%%V bootstrap.py --check-python >nul 2>nul
        if not errorlevel 1 (
            py -%%V bootstrap.py %*
            if errorlevel 1 goto failed
            exit /b 0
        )
    )
)
where python >nul 2>nul
if not errorlevel 1 (
    python bootstrap.py %*
    if not errorlevel 1 exit /b 0
    goto failed
)
echo No supported Python found. Install standard Python 3.15, 64-bit, from python.org.
echo Enable "Add python.exe to PATH", then run Start.bat again.
start "" "https://www.python.org/downloads/windows/"
:failed
echo.
echo Startup could not finish. See the message above or setup.log in this folder.
pause
exit /b 1
