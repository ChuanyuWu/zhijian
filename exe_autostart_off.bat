@echo off
cd /d %~dp0
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "ZhiJianPostMonitor" /f >nul 2>&1
for %%f in ("%~dp0*.exe") do taskkill /f /im "%%~nxf" >nul 2>&1
echo Auto-start removed and background process stopped.
pause
