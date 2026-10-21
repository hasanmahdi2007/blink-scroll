@echo off
rem Start Blink Scroll in the background (no console window). Extra arguments are passed on.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo Virtual environment missing. See README.md, "Setup".
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m blinkscroll %*
