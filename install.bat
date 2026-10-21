@echo off
rem Blink Scroll one-click setup: Python (if needed), virtual environment, packages, model.
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title Blink Scroll setup
echo.
echo  ===  Blink Scroll setup  ===
echo.

rem --- 1. Find a supported Python (3.12 preferred; 3.11 / 3.13 also work) -------------------
set "PYEXE="
for %%v in (3.12 3.13 3.11) do (
    if not defined PYEXE (
        for /f "delims=" %%i in ('py -%%v -c "import sys; print(sys.executable)" 2^>nul') do set "PYEXE=%%i"
    )
)
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not defined PYEXE (
    echo  Python 3.12 was not found. Installing it with winget ^(about 30 MB^)...
    winget install --id Python.Python.3.12 -e --scope user --accept-package-agreements --accept-source-agreements
    if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
        set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    )
)
if not defined PYEXE (
    echo.
    echo  Could not find or install Python. Install Python 3.12 from https://www.python.org/downloads/
    echo  ^(tick "Add python.exe to PATH"^), then run install.bat again.
    pause
    exit /b 1
)
echo  Using Python: !PYEXE!

rem --- 2. Virtual environment + packages ----------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo  Creating virtual environment...
    "!PYEXE!" -m venv .venv || goto :fail
)
echo  Installing packages ^(first time: a few minutes, ~300 MB^)...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet --disable-pip-version-check
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet --disable-pip-version-check || goto :fail

rem --- 3. Face model (the only download the app ever makes) ---------------------------------
".venv\Scripts\python.exe" -m blinkscroll.download_model || goto :fail

rem --- 4. Options -----------------------------------------------------------------------------
if "%BLINKSCROLL_UNATTENDED%"=="1" (
    echo  Setup finished ^(unattended^).
    exit /b 0
)
echo.
choice /c YN /n /m " Start Blink Scroll automatically when Windows starts? [Y/N] "
if errorlevel 2 (
    ".venv\Scripts\python.exe" -m blinkscroll --autostart off >nul 2>&1
) else (
    ".venv\Scripts\python.exe" -m blinkscroll --autostart on && echo  Added to Windows startup.
)

echo.
echo  Done! Starting Blink Scroll...
echo  First start: look at the screen; close your eyes at the beep, open them at the 2nd beep.
echo  Then: hold a LEFT WINK to scroll down, close BOTH eyes ~0.6 s ^(and hold^) to scroll up.
echo  Later, start it with run.bat ^(or it starts with Windows if you chose that^).
echo.
start "" ".venv\Scripts\pythonw.exe" -m blinkscroll
pause
exit /b 0

:fail
echo.
echo  Setup failed (see the messages above). Check your internet connection and try again.
pause
exit /b 1
