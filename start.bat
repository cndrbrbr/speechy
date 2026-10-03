@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0scripts\start.ps1" (
  echo Fehler: scripts\start.ps1 fehlt. Bitte das gesamte Speechy-ZIP entpacken.
  pause
  exit /b 1
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" %*
set "SPEECHY_EXIT=%ERRORLEVEL%"
if not "%SPEECHY_EXIT%"=="0" (
  echo.
  echo Speechy konnte nicht gestartet werden. Die Fehlermeldung steht oben.
  pause
)
exit /b %SPEECHY_EXIT%
