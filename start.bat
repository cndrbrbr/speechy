@echo off
setlocal
cd /d "%~dp0"

rem Speechy-Starter ohne PowerShell. Manuelle Einrichtung: siehe INSTALL.txt.
set "PYTHONHOME="
set "PYTHONPATH="
set "TCL_LIBRARY="
set "TK_LIBRARY="
set "PYTHONUTF8=1"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "VENV=%~dp0.speechy-venv"
set "VENV_PY=%VENV%\Scripts\python.exe"
set "CHECK="
set "PROBE=import sys, tkinter; sys.exit(not (3, 11) <= sys.version_info[:2] <= (3, 13))"

if /i "%~1"=="-CheckOnly" set "CHECK=--check-only"
if /i "%~1"=="--check-only" set "CHECK=--check-only"

if not exist "scripts\bootstrap.py" (
  echo Fehler: scripts\bootstrap.py fehlt. Bitte das gesamte Speechy-ZIP entpacken.
  goto fail
)

rem Vorhandene virtuelle Umgebung wiederverwenden, wenn sie noch funktioniert.
if exist "%VENV_PY%" (
  "%VENV_PY%" -c "%PROBE%" >nul 2>&1 && goto run
  if defined CHECK goto nopython
  echo Die virtuelle Umgebung .speechy-venv ist unbrauchbar und wird neu erstellt ...
  rmdir /s /q "%VENV%"
)

rem Python 3.11 bis 3.13 mit Tkinter suchen.
set "PY="
for %%V in (3.13 3.12 3.11) do (
  if not defined PY py -%%V -c "%PROBE%" >nul 2>&1 && set "PY=py -%%V"
)
if not defined PY python -c "%PROBE%" >nul 2>&1 && set "PY=python"
if not defined PY goto nopython
if defined CHECK goto nopython

echo Erstelle virtuelle Python-Umgebung mit: %PY%
%PY% -m venv "%VENV%"
if errorlevel 1 (
  echo Fehler: Die virtuelle Umgebung konnte nicht erstellt werden.
  goto fail
)

:run
rem bootstrap.py installiert fehlende Pakete, laedt Stimmen/OCR/Whisper und startet Speechy.
"%VENV_PY%" -I scripts\bootstrap.py %CHECK%
if errorlevel 1 goto fail
exit /b 0

:nopython
echo.
echo Fehler: Kein geeignetes Python gefunden bzw. .speechy-venv fehlt.
echo Benoetigt wird Python 3.11 bis 3.13 (64 Bit) mit "tcl/tk and IDLE".
echo Download: https://www.python.org/downloads/windows/
echo Schritt-fuer-Schritt-Befehle stehen in INSTALL.txt.

:fail
echo.
echo Speechy konnte nicht gestartet werden. Die Fehlermeldung steht oben.
pause
exit /b 1
