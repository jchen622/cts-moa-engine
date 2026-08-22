@echo off
REM Command-line wrapper, Windows version, so "moa-engine check" works the same
REM way it does on macOS. The macOS equivalent is the "moa-engine" shell script.
cd /d "%~dp0"
set PY=
where py >nul 2>&1 && set PY=py -3
if "%PY%"=="" set PY=python
%PY% moa_engine.py %*
