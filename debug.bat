@echo off
rem Start Blink Scroll with the debug window and console log. Extra arguments are passed on,
rem e.g.  debug.bat --record
cd /d "%~dp0"
".venv\Scripts\python.exe" -m blinkscroll --debug %*
pause
