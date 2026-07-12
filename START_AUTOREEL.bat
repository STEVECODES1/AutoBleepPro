@echo off
echo ========================================
echo   AutoReel - AI Video Post-Production Supervisor
echo ========================================
echo.

if "%~1"=="" (
    echo Usage: START_AUTOREEL.bat "path\to\video.mp4" [extra autoreel args]
    echo Example: START_AUTOREEL.bat "stream.mp4" --num-clips 5
    pause
    exit /b 1
)

python -m autoreel.cli %*

if errorlevel 1 (
    echo.
    echo ERROR: Failed to run AutoReel!
    echo Make sure you ran INSTALL.bat first.
    pause
)
