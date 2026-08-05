@echo off
setlocal
cd /d "%~dp0"

rem KEEP THIS FILE PURE ASCII - see the note in run.bat.
rem READ-ONLY probe of whatever dialog/window is currently open in HosXP.
rem It never clicks or types - safe to run while a warning popup is on screen.
rem Use it when a new popup appears so its contents can be mapped.

fltmc >nul 2>&1
if errorlevel 1 goto need_admin
goto probe

:need_admin
if /i "%~1"=="elevated" (
  echo [ERROR] Still not running as administrator after the UAC prompt.
  echo Right-click probe_popup.bat and choose "Run as administrator".
  pause
  exit /b 1
)
echo Requesting administrator rights - please click "Yes" on the UAC window.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -ArgumentList 'elevated' -WorkingDirectory '%~dp0' -Verb RunAs"
exit /b

:probe
set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [ERROR] venv not found - run setup.bat first.
  pause
  exit /b 1
)
echo Leave the popup open on screen, then press a key to scan it.
pause
"%PY%" tools\probe_popup.py
echo.
pause
