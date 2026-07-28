@echo off
echo ========================================
echo   Starting AutoReel (GUI)
echo ========================================
echo.

python autoreel_gui.py

if errorlevel 1 (
    echo.
    echo ERROR: Failed to start!
    echo Make sure you ran INSTALL.bat first.
    pause
)
