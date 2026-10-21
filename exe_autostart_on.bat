@echo off
cd /d "%~dp0"
if not exist "%~dp0zhijian_silent.exe" (
 echo [ERROR] zhijian_silent.exe was not found. Extract the complete release folder first.
 pause
 exit /b 1
)
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "ZhiJianPostMonitor" /t REG_SZ /d "\"%~dp0zhijian_silent.exe\"" /f >nul
if errorlevel 1 exit /b 1
start "" "%~dp0zhijian_silent.exe"
echo Auto-start enabled.
pause
