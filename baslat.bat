@echo off
setlocal EnableExtensions
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
if not exist "media\logs" mkdir "media\logs"

echo.
echo ==== GUNDEM CANLI YAYIN BASLATICI ====
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*manage.py runserver*' }) { exit 1 } else { exit 0 }"
if %errorlevel%==1 (
  echo [WEB] zaten calisiyor.
) else (
  echo [WEB] baslatiliyor: http://127.0.0.1:8000
  start "Gundem Web" cmd /k ".venv\Scripts\python.exe manage.py runserver 0.0.0.0:8000"
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*run_studio*' }) { exit 1 } else { exit 0 }"
if %errorlevel%==1 (
  echo [STUDYO] zaten calisiyor.
) else (
  echo [STUDYO] baslatiliyor: haber cek + bulten uret (7/24)
  start "Gundem Studio" cmd /k ".venv\Scripts\python.exe manage.py run_studio --interval 5"
)

echo.
echo 1-2 dakika sonra canli yayin kendiliginden baslar.
echo Canli yayin:   http://127.0.0.1:8000/canli/
echo Panel:         http://127.0.0.1:8000/
echo Yonetim:       http://127.0.0.1:8000/admin/
echo.
timeout /t 5 /nobreak >nul
start "" "http://127.0.0.1:8000/canli/"
exit /b 0