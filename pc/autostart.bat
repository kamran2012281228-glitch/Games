@echo off
setlocal
set "S=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\PCRemote.vbs"
if exist "%S%" (
  del "%S%"
  echo Autostart DISABLED.
) else (
  > "%S%" echo CreateObject^("WScript.Shell"^).Run """%~dp0start.bat""", 0, False
  echo Autostart ENABLED. PC Remote will start hidden when Windows starts.
  echo PIN is saved in config.json in this folder.
)
pause
