@echo off
setlocal
cd /d "%~dp0"

rem KEEP THIS FILE PURE ASCII - see the note in run.bat.

echo === IPDaddChart : first-time setup ===
where py >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python launcher "py" not found.
  echo Install Python 3.11+ from python.org, tick "Add python.exe to PATH", then run setup.bat again.
  pause
  exit /b 1
)

if not exist "venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -3 -m venv venv
  if errorlevel 1 (
    echo [ERROR] Could not create the virtual environment.
    pause
    exit /b 1
  )
)

set "PY=%~dp0venv\Scripts\python.exe"
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Package installation failed - check internet/proxy settings, then run setup.bat again.
  pause
  exit /b 1
)
"%PY%" -c "import fastapi, uvicorn, pywinauto, pymysql, openpyxl" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Some packages still cannot be imported - run setup.bat again.
  pause
  exit /b 1
)

if not exist "config\settings.json" (
  copy "config\default_settings.json" "config\settings.json" >nul
  echo Created config\settings.json - fill in DB / HosXP accounts on the Settings page.
)

echo.
echo === Setup finished === Start the app with run.bat
pause
