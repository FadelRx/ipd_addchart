@echo off
setlocal
cd /d "%~dp0"

rem KEEP THIS FILE PURE ASCII - see the note in run.bat.
rem READ-ONLY probe of the open "IPD Medication Profile" window.
rem It never clicks, types or right-clicks anything - safe to run with a patient loaded.
rem Run it twice: once on the patient-select screen, once on the Add Chart screen.

fltmc >nul 2>&1
if errorlevel 1 goto need_admin
goto probe

:need_admin
if /i "%~1"=="elevated" (
  echo [ERROR] Still not running as administrator after the UAC prompt.
  echo Right-click probe_ipd.bat and choose "Run as administrator".
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
"%PY%" tools\probe_ipd.py
echo.
pause
