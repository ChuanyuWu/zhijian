@echo off
cd /d %~dp0
if not exist .venv\Scripts\pythonw.exe (
    echo [ERROR] .venv not found. Run install.bat first.
    pause
    exit /b
)

echo Set ws = CreateObject("Wscript.Shell")> run_hidden.vbs
echo ws.CurrentDirectory = "%CD%">> run_hidden.vbs
echo ws.Run """%CD%\.venv\Scripts\pythonw.exe"" ""main.py""", 0, False>> run_hidden.vbs

reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "ZhiJianPostMonitor" /t REG_SZ /d "wscript.exe \"%CD%\run_hidden.vbs\"" /f >nul
if errorlevel 1 (
    echo [ERROR] Failed to set auto-start.
) else (
    echo.
    echo Done! ZhiJian will now start silently at every logon.
    echo Starting it now in background...
    wscript.exe "%CD%\run_hidden.vbs"
    echo It is running. Open http://127.0.0.1:8000 in your browser.
)
pause
