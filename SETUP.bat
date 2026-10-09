@echo off
rem ============================================================
rem  PyChat setup - run this ONCE, then use start.bat
rem ============================================================
title PyChat Setup
cd /d "%~dp0"

echo.
echo  PyChat setup - this happens only once.
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo  ERROR: Python was not found on this computer.
    echo.
    echo  1. Go to https://www.python.org/downloads/
    echo  2. Download and run the installer
    echo  3. IMPORTANT: tick "Add python.exe to PATH" on the first screen
    echo  4. Finish the install, then run THIS file again.
    echo.
    pause
    exit /b 1
)

if exist ".venv\Scripts\python.exe" goto :install
echo  [1/2] Creating a private Python environment (one time only)...
python -m venv .venv
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo  ERROR: could not create the environment.
    echo  Make sure Python installed correctly, then run this file again.
    echo.
    pause
    exit /b 1
)
goto :install

:install
echo  [2/2] Installing the chat libraries (needs internet, one time only)...
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip >nul 2>&1
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
if errorlevel 1 (
    echo.
    echo  ERROR: installing the libraries failed.
    echo  Check your internet connection and run this file again.
    echo.
    pause
    exit /b 1
)

echo.
echo  ------------------------------------------------------------
echo   Setup finished!
echo   Now double-click START.bat to run the chat.
echo  ------------------------------------------------------------
echo.
pause
