@echo off
cd /d %~dp0
if not exist .venv\Scripts\python.exe (
    echo [ERROR] .venv not found. Run the install bat first.
    pause
    exit /b
)
echo Installing PyInstaller...
.venv\Scripts\python.exe -m pip install pyinstaller -q
echo Packaging windowless version, please wait...
.venv\Scripts\python.exe -m PyInstaller --onefile --windowed --name zhijian_silent --add-data "static;static" --clean --noconfirm main.py
if errorlevel 1 (
    echo.
    echo [ERROR] Packaging failed. Send a screenshot to the author.
    pause
    exit /b
)
echo.
echo ============================================
echo SUCCESS! Windowless version: dist\zhijian_silent.exe
echo Rename to zhijian_silent.exe and share.
echo This version runs with NO console window.
echo Logs go to data\run.log next to the exe.
echo To stop it: Task Manager -^> end zhijian_silent.exe
echo NEVER include the data folder when sharing!
echo ============================================
pause
