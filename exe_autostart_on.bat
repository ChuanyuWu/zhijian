@echo off
cd /d %~dp0
rem Find the exe next to this bat (works even if renamed)
set "EXE="
set "COUNT=0"
for %%f in ("%~dp0*.exe") do (
    set /a COUNT+=1
    set "EXE=%%~nxf"
)
if "%COUNT%"=="0" (
    echo [ERROR] No exe found. Put this bat in the SAME folder as zhijian_silent.exe
    pause
    exit /b
)
if not "%COUNT%"=="1" (
    echo [ERROR] Multiple exe files found here. Keep only the ZhiJian exe next to this bat.
    pause
    exit /b
)

reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "ZhiJianPostMonitor" /t REG_SZ /d "\"%CD%\%EXE%\"" /f >nul
if errorlevel 1 (
    echo [ERROR] Failed to set auto-start.
) else (
    echo.
    echo Done! "%EXE%" will now start silently at every logon.
    echo Starting it now...
    start "" "%CD%\%EXE%"
    echo Open http://127.0.0.1:8000 in your browser.
)
pause
