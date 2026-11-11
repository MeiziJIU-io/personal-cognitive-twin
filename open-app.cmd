@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" goto missing
start "" ".venv\Scripts\pythonw.exe" -m collector.desktop
exit /b 0
:missing
echo Python environment not found. Run start.cmd once to install dependencies.
pause
exit /b 1
