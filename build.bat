@echo off
setlocal
cd /d "%~dp0"

REM Build a standalone single-file LoadoutEditor.exe (output: dist\LoadoutEditor.exe)
pip install pyinstaller
python -m PyInstaller --noconfirm --onefile --console --name LoadoutEditor ^
    --hidden-import src.loadout_gui --hidden-import src.ui main.py

echo.
echo Done. The executable is at: dist\LoadoutEditor.exe
echo.
pause
