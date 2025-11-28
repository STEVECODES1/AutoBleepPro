@echo off
echo ========================================
echo   AutoBleep Pro - Installation
echo ========================================
echo.
echo Installing required packages...
echo This will take 5-10 minutes.
echo.
pause

pip install --upgrade pip
pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo ERROR: Installation failed!
    pause
    exit /b 1
)

echo.
echo ========================================
echo   Installation Complete!
echo ========================================
echo.
echo Run: START_AUTOBLEEP.bat
echo.
pause
