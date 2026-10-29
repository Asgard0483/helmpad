@echo off
rem Startet die HelmPad-Oberflaeche im Browser. Beim ersten Start (und wenn sich requirements.txt
rem aendert) wird die Python-Umgebung eingerichtet bzw. aktualisiert.
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\helmpad-venv"

rem Umgebung nur als fertig ansehen, wenn die Installation komplett durchgelaufen ist
if not exist "%VENV%\requirements.installed" goto :install
fc /b requirements.txt "%VENV%\requirements.installed" >nul 2>&1 || goto :install
goto :start

:install
echo Richte Python-Umgebung ein ^(einmalig, dauert ein paar Minuten^) ...
if not exist "%VENV%\Scripts\python.exe" (
  py -3.13 -m venv "%VENV%" 2>nul || py -3 -m venv "%VENV%" 2>nul || python -m venv "%VENV%" || goto :error
)
"%VENV%\Scripts\python.exe" -m pip install --quiet --upgrade pip
"%VENV%\Scripts\python.exe" -m pip install --quiet -r requirements.txt || goto :error
copy /y requirements.txt "%VENV%\requirements.installed" >nul

:start
"%VENV%\Scripts\python.exe" -m helmpad.server
if errorlevel 1 (
  echo.
  echo HelmPad wurde mit einem Fehler beendet ^(siehe oben^). Ist Port 8770 schon belegt?
  pause
)
goto :eof

:error
echo.
echo Einrichtung fehlgeschlagen. Ist Python 3.13 installiert ^(python.org^) und besteht eine Internetverbindung?
pause
