@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title PC Remote

set "PY="
python -c "import sys" >nul 2>&1 && set "PY=python"
if not defined PY py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"

if defined PY goto havepy
echo [*] Python not found. Installing Python 3.12 via winget...
where winget >nul 2>&1
if errorlevel 1 goto nowinget
winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined PY goto havepy
:nowinget
echo.
echo [!] Could not install Python automatically.
echo     Install it from https://www.python.org/downloads/
echo     (check "Add python.exe to PATH"), then run start.bat again.
start "" https://www.python.org/downloads/
pause
exit /b 1

:havepy
%PY% -c "import mss, PIL, pyautogui, qrcode" >nul 2>&1
if not errorlevel 1 goto run
echo [*] First run: installing libraries (about 1 minute)...
%PY% -m pip install --disable-pip-version-check --user -q mss pillow pyautogui qrcode
if errorlevel 1 (
  echo [!] pip install failed. Check your internet connection.
  pause
  exit /b 1
)

:run
%PY% server.py
pause
