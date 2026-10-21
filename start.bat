@echo off
cd /d "%~dp0"
if exist "%~dp0zhijian_silent.exe" (
    start "" "%~dp0zhijian_silent.exe"
    exit /b
)
if not exist .venv\Scripts\python.exe (
    echo [ERROR] Run install.bat first, then run start.bat again.
    pause
    exit /b 1
)
.venv\Scripts\python.exe main.py
pause
