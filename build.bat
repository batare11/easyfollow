@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
"%PY%" -m PyInstaller --noconfirm --onefile --windowed --name EasyFollow --clean main.py
echo.
echo Build finished. Output: dist\EasyFollow.exe
pause
