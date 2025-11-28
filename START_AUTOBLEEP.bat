@echo off
echo ========================================
echo   Starting AutoBleep Pro
echo ========================================
echo.

python autobleep_pro.py

if errorlevel 1 (
    echo.
    echo ERROR: Failed to start!
    echo Make sure you ran INSTALL.bat first.
    pause
)
