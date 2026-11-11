@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto verify
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if not errorlevel 1 goto use_py
python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if not errorlevel 1 goto use_python
echo Python 3.11 or newer was not found.
echo Install Python, enable "Add python.exe to PATH", then reopen this script.
echo Download: https://www.python.org/downloads/windows/
echo Or run in PowerShell: winget install -e --id Python.Python.3.12
goto failed
:use_py
py -3 -m venv .venv
if errorlevel 1 goto failed
goto verify
:use_python
python -m venv .venv
if errorlevel 1 goto failed
:verify
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m collector
if errorlevel 1 goto failed
exit /b 0
:failed
echo Startup failed. Please share the error above.
pause
exit /b 1
