@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m collector.pipeline live
pause
