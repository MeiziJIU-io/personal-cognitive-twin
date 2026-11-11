@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" goto launch
call "tools\setup.cmd"
if errorlevel 1 exit /b 1
:launch
start "" ".venv\Scripts\pythonw.exe" -m collector.desktop
exit /b 0
