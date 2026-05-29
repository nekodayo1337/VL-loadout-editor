@echo off
setlocal EnableDelayedExpansion

REM Loadout Editor needs Python 3.8 or newer.
set MIN_VERSION=3.8.0
for /f "tokens=1-3 delims=." %%a in ("%MIN_VERSION%") do (
    set /a MIN_MAJOR=%%a
    set /a MIN_MINOR=%%b
    set /a MIN_PATCH=%%c
)

for /f "tokens=2 delims= " %%I in ('python --version 2^>nul') do set PYTHON_VERSION=%%I
if not defined PYTHON_VERSION (
    call :error "Python is not installed. Please install Python %MIN_VERSION% or newer (added to PATH)."
    exit /b
)

for /f "tokens=1-3 delims=." %%a in ("!PYTHON_VERSION!") do (
    set MAJOR=%%a
    set MINOR=%%b
    set PATCH=%%c
)
if not defined PATCH set PATCH=0
set /a HAVE=!MAJOR!*10000 + !MINOR!*100 + !PATCH!
set /a NEED=%MIN_MAJOR%*10000 + %MIN_MINOR%*100 + %MIN_PATCH%

if !HAVE! lss !NEED! (
    call :error "Python !PYTHON_VERSION! is too old. Please install Python %MIN_VERSION% or newer."
    exit /b
)

pip install -r requirements.txt
if %errorlevel% neq 0 (
    call :error "There was an error installing the requirements. Please check the output above for more details."
    exit /b
)

echo.
echo Requirements were successfully installed.
echo Use loadout.bat to start the Loadout Editor.
echo.
echo Press any key to exit...
pause >nul
exit /b

:error
echo.
echo %~1
echo.
echo Press any key to exit...
pause >nul
goto :eof
