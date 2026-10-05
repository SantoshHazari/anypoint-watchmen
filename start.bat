@echo off
title Anypoint Watchmen
echo ============================================
echo   Anypoint Watchmen - Starting...
echo ============================================
echo.

cd /d "%~dp0"

:: Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    pause
    exit /b 1
)

:: Check Flask is installed
python -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo ERROR: Flask is not installed. Run: pip install flask
    pause
    exit /b 1
)

:: Network binding - accessible from all devices on local network
set WATCHMEN_HOST=0.0.0.0
set WATCHMEN_PORT=5050

echo  Host: %WATCHMEN_HOST%
echo  Port: %WATCHMEN_PORT%
echo  URL:  http://localhost:%WATCHMEN_PORT%
echo.
echo  To access from other devices on your network,
echo  use this machine's IP address instead of localhost.
echo.
echo  Press Ctrl+C to stop the server.
echo ============================================
echo.

python web\app.py
pause
