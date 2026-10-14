@echo off
setlocal EnableExtensions
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
if not exist "media\logs" mkdir "media\logs"

start "" /b cmd /c ".venv\Scripts\python.exe manage.py runserver 0.0.0.0:8000 1>>media\logs\sunucu.log 2>&1"
start "" /b cmd /c ".venv\Scripts\python.exe manage.py run_studio --interval 5 1>>media\logs\studyo.log 2>&1"

timeout /t 4 /nobreak >nul
start "" "http://127.0.0.1:8000/canli/"
exit /b 0
