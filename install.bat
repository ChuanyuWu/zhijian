@echo off
cd /d %~dp0
echo Creating virtualenv and installing dependencies (one time only)...
python -m venv .venv
if errorlevel 1 (
    echo.
    echo [ERROR] python not found. Install Python 3.10+ from python.org
    echo and CHECK "Add python.exe to PATH" during install.
    pause
    exit /b
)
.venv\Scripts\python.exe -m pip install -r requirements.txt -q
echo.
echo Done! From now on just double-click start.bat
pause
