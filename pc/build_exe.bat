@echo off
rem Optional: builds PCRemote.exe (a single file, no Python needed on other PCs).
cd /d "%~dp0"
set "PY=python"
python -c "import sys" >nul 2>&1 || set "PY=py -3"
%PY% -m pip install --user -q pyinstaller mss pillow pyautogui qrcode
%PY% -m PyInstaller --onefile --name PCRemote --clean server.py
if exist dist\PCRemote.exe (
  copy /y dist\PCRemote.exe PCRemote.exe >nul
  echo.
  echo Done: PCRemote.exe
)
pause
