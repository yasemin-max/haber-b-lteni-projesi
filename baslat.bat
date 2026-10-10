@echo off
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "C:\Users\Win10\Desktop\GNDEM~1"
if not exist "media\logs" mkdir "media\logs"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$n = @(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*run_studio*' }).Count; if ($n -gt 0) { exit 1 } else { exit 0 }"
if errorlevel 1 exit /b 0