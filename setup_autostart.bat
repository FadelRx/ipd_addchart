@echo off
REM Make IPDaddChart start automatically at logon (no daily UAC prompt).
setlocal
if not "%~1"=="elevated" (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList 'elevated' -Verb RunAs"
  exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_autostart.ps1"
