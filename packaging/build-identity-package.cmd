@echo off
rem Builds the Speechy identity package (developer machine, Windows SDK required).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-identity-package.ps1"
if errorlevel 1 (
  echo.
  echo Paket konnte nicht gebaut werden. Die Fehlermeldung steht oben.
)
pause
