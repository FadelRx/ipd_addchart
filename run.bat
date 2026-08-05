@echo off
setlocal
cd /d "%~dp0"

rem ---------------------------------------------------------------------------
rem KEEP THIS FILE PURE ASCII.
rem "chcp 65001" together with non-ASCII (Thai) text corrupts cmd's batch parser:
rem it silently skips whole blocks (elevation check, venv activation) and keeps
rem running the rest. All Thai wording lives in the web UI / README instead.
rem ---------------------------------------------------------------------------

rem HosXP (hosmy.exe) declares requireAdministrator, so it always runs elevated.
rem Windows (UIPI) blocks keyboard/mouse input sent from a lower-privilege
rem process to an elevated window, so this app MUST run elevated too.
fltmc >nul 2>&1
if errorlevel 1 goto need_admin
goto start_server

:need_admin
if /i "%~1"=="elevated" (
  echo [ERROR] Still not running as administrator after the UAC prompt.
  echo Right-click run.bat and choose "Run as administrator".
  pause
  exit /b 1
)
echo Requesting administrator rights - please click "Yes" on the UAC window.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -ArgumentList 'elevated' -WorkingDirectory '%~dp0' -Verb RunAs"
exit /b

:start_server
set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [ERROR] venv not found - run setup.bat first.
  pause
  exit /b 1
)
"%PY%" -c "import fastapi, uvicorn, pywinauto, pymysql, openpyxl" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Python packages missing or broken - run setup.bat again.
  pause
  exit /b 1
)
netstat -ano | findstr /c:"127.0.0.1:8770" | findstr /i "LISTENING" >nul
if not errorlevel 1 (
  echo [ERROR] Port 8770 is already in use - IPDaddChart may already be running.
  echo Close the other IPDaddChart window first, then start this again.
  pause
  exit /b 1
)

echo === IPDaddChart (Administrator) ===
echo Python : %PY%
echo URL    : http://127.0.0.1:8770
echo Closing this window stops the program.
echo.
rem Open the browser through explorer.exe so it runs as the normal user
rem instead of inheriting administrator rights from this window.
start "" /b cmd /c "timeout /t 3 >nul & explorer.exe http://127.0.0.1:8770"
"%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8770
echo.
echo Server stopped.
pause
