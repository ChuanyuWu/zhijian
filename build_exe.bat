@echo off
cd /d %~dp0
if not exist .venv\Scripts\python.exe (
    echo [ERROR] .venv not found. Run install bat first.
    pause
    exit /b
)
echo Installing PyInstaller...
.venv\Scripts\python.exe -m pip install pyinstaller -q
echo Packaging, please wait...
.venv\Scripts\python.exe -m PyInstaller --onefile --name zhijian --add-data "static;static" --clean --noconfirm main.py
if errorlevel 1 (
    echo.
    echo [ERROR] Packaging failed. Send a screenshot to the author.
    pause
    exit /b
)
echo.
echo ============================================
echo SUCCESS! Shareable file: dist\zhijian.exe
echo Rename it and send to friends.
echo NEVER include the data folder!
echo ============================================
pause
