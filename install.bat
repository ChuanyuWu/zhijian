@echo off
cd /d "%~dp0"
python -m venv .venv
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
if errorlevel 1 exit /b 1
set "PLAYWRIGHT_BROWSERS_PATH=%CD%\browsers"
.venv\Scripts\python.exe -m playwright install chromium
if errorlevel 1 exit /b 1
echo Installed. Run start.bat.
pause
