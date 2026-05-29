@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

REM Loadout Editor (browser GUI). Needs the Riot Client running and logged in.
python "main.py"
if !ERRORLEVEL! NEQ 0 (
    color 0C
    echo.
    echo Failed to start. Make sure Python is installed and run INSTALL.bat first.
    pause >nul
)
