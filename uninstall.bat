@echo off
rem Stops Blink Scroll and removes it from Windows startup. Then delete this folder to finish.
cd /d "%~dp0"
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name like 'python%%'\" | Where-Object { $_.CommandLine -like '*blinkscroll*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Blink Scroll.lnk" >nul 2>&1
echo Blink Scroll was stopped and removed from Windows startup.
echo To remove it completely, delete this folder: %~dp0
pause
