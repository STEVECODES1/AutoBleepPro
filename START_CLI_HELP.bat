@echo off
echo ========================================
echo   AutoBleep Pro - Command Line Help
echo ========================================
echo.

python cli.py -h

echo.
echo ----------------------------------------
echo Examples:
echo.
echo   python cli.py "C:\videos\stream.mp4" -o "C:\videos\clean"
echo   python cli.py "C:\videos" -o "C:\videos\clean" --batch
echo   python cli.py "C:\videos\stream.mp4" --srt --txt --sensitivity 50
echo ----------------------------------------
echo.
pause
