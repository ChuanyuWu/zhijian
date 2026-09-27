@echo off
cd /d %~dp0
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "ZhiJianPostMonitor" /f >nul 2>&1
taskkill /f /im pythonw.exe >nul 2>&1
if exist run_hidden.vbs del run_hidden.vbs
echo Auto-start removed and background process stopped.
pause
