@echo off
rem Guided ~3 minute tuning session; saves a labelled score CSV (numbers only) in recordings\.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m blinkscroll.guided
pause
