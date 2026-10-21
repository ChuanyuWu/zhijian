@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
 echo Run install.bat first.
 pause
 exit /b 1
)
.venv\Scripts\python.exe -m pip install pyinstaller
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe build_release.py
pause
