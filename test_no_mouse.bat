@echo off
REM Test whether HosXP can be driven without real mouse/keyboard. Needs Administrator.
setlocal
if not "%~1"=="elevated" (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList 'elevated' -Verb RunAs"
  exit /b
)
cd /d "%~dp0"
"%~dp0venv\Scripts\python.exe" -X utf8 "%~dp0tools\test_no_mouse.py"
