@echo off
setlocal
cd /d "%~dp0"

pip install pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name LoadoutEditor ^
    --hidden-import src.loadout_gui --hidden-import src.ui ^
    --hidden-import pystray._win32 main.py

echo.
echo Done. The executable is at: dist\LoadoutEditor.exe
echo.
pause
