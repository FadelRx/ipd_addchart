@echo off
setlocal
cd /d "%~dp0"

rem KEEP THIS FILE PURE ASCII - see the note in run.bat.
rem Dumps every control of the running HosXP window to data\hosxp_controls.txt
rem so the "Add cont meds" button can be mapped in Settings.
rem Needs administrator rights for the same UIPI reason as run.bat.

fltmc >nul 2>&1
if errorlevel 1 goto need_admin
goto scan

:need_admin
if /i "%~1"=="elevated" (
  echo [ERROR] Still not running as administrator after the UAC prompt.
  echo Right-click inspect_hosxp.bat and choose "Run as administrator".
  pause
  exit /b 1
)
echo Requesting administrator rights - please click "Yes" on the UAC window.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -ArgumentList 'elevated' -WorkingDirectory '%~dp0' -Verb RunAs"
exit /b

:scan
set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [ERROR] venv not found - run setup.bat first.
  pause
  exit /b 1
)
echo Open HosXP and keep the "IPD Medication Profile" window on screen before scanning.
echo.
"%PY%" tools\inspect_hosxp.py
echo.
pause
