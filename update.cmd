@echo off
setlocal
cd /d "%~dp0"
where git >nul 2>&1
if errorlevel 1 goto missing
 git pull --ff-only origin codex/windows-capture-pipeline
if errorlevel 1 goto failed
echo Update complete. Restart the collector to use the new code.
pause
exit /b 0
:missing
echo Git is not installed. Install Git for Windows, then reopen this file.
pause
exit /b 1
:failed
echo Update stopped. Keep your local changes and share the error above.
pause
exit /b 1
