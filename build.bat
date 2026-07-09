@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
"%PY%" build.py
pause
