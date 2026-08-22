@echo off
REM Double-clickable launcher for the CTS MOA sourcing engine, Windows version.
REM
REM All this does is start the local web app; the actual interface is the
REM browser window that opens. There is nothing to sign in to and nothing to
REM install beyond Python 3.
REM
REM The macOS equivalent is "Start MOA engine.command".

cd /d "%~dp0"

echo.
echo CTS MOA sourcing engine
echo ------------------------------------------------------------

REM The py launcher is the reliable way to find Python 3 on Windows; fall back
REM to whatever "python" resolves to.
set PY=
where py >nul 2>&1 && set PY=py -3
if "%PY%"=="" (
  where python >nul 2>&1 && set PY=python
)

if "%PY%"=="" (
  echo.
  echo   Python 3 is not installed.
  echo.
  echo   Install it from https://www.python.org/downloads/
  echo   During setup, tick "Add Python to PATH".
  echo   Then double-click this file again.
  echo.
  pause
  exit /b 1
)

for /f "delims=" %%v in ('%PY% -c "import sys;print('.'.join(map(str,sys.version_info[:3])))"') do set PYVER=%%v
echo   Python %PYVER%
echo.
echo Opening the app in your web browser...
echo.
echo Keep this window open while you work.
echo To stop, close the browser tab and press Ctrl-C here.
echo.

%PY% gui.py

echo.
pause
