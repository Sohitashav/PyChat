@echo off
rem ============================================================
rem  PyChat local-only - no public link, just this computer.
rem  Use this if START.bat's tunnel does not work,
rem  or to try the chat by yourself on this PC.
rem ============================================================
title PyChat (local only)
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo  PyChat is not set up yet.
    echo  Double-click SETUP.bat first, then run this file again.
    echo.
    pause
    exit /b 1
)

echo.
echo  PyChat is running on this computer only.
echo  Open http://127.0.0.1:5000 in your browser.
echo  Keep this window open while you chat.
echo.
start "" "http://127.0.0.1:5000"
".venv\Scripts\python.exe" app.py
pause
