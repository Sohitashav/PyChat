@echo off
rem ============================================================
rem  PyChat - start the server + public link
rem  Run SETUP.bat first if you have not done that yet.
rem ============================================================
title PyChat
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
echo  [1/2] Starting the chat server...
start "PyChat server (keep this window open)" ".venv\Scripts\python.exe" app.py
timeout /t 3 /nobreak >nul

echo.
echo  [2/2] Starting the public link...
echo.
echo  ------------------------------------------------------------
echo   YOUR PUBLIC LINK appears below in a few seconds.
echo   Send it to your friend. You both sign up with
echo   different accounts, then search each other's username.
echo   Keep BOTH windows open while you chat.
echo  ------------------------------------------------------------
echo.
tools\cloudflared.exe tunnel --url http://127.0.0.1:5000 --protocol http2

pause
