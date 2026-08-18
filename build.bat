@echo off
REM Build IPDaddChart.exe (single file). Run from the project folder.
setlocal
cd /d %~dp0
if not exist venv + B + Scripts + B + python.exe (
  echo venv not found - run setup.bat first
  pause
  exit /b 1
)
%~dp0venv + B + Scripts + B + python.exe %~dp0tools + B + build_exe.py %*
pause
