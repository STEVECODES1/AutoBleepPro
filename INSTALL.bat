@echo off
REM ============================================================================
REM  INSTALL.bat - install everything both halves of this project need.
REM
REM  There are TWO requirements files and for a long time this installed one:
REM
REM    requirements.txt                 AutoReel - transcription, clipping,
REM                                     smart crop
REM    auto_uploader\requirements.txt   the uploader - dotenv, the Google API
REM                                     clients, playwright, watchdog, yt-dlp
REM
REM  A machine that had only ever run this file could not start the uploader.
REM  It died on `No module named 'dotenv'` and the keepalive restarted it into
REM  the same traceback every fifteen seconds, forever. Both lists now.
REM ============================================================================

setlocal

cd /d "%~dp0"

echo ========================================
echo   AutoBleep Pro - Installation
echo ========================================
echo.
echo Installing required packages.
echo This takes 5-10 minutes the first time.
echo.
pause

REM  Through THIS interpreter, not a bare `pip`. On a machine with more than
REM  one Python, a bare pip installs into the other one and every import
REM  fails afterwards with the packages visibly installed.
python -m pip install --upgrade pip
if errorlevel 1 goto failed

echo.
echo ---- AutoReel: transcription, clipping, smart crop ----
python -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto failed

echo.
echo ---- Uploader: Google APIs, Rumble, watch folder, yt-dlp ----
python -m pip install -r "%~dp0auto_uploader\requirements.txt"
if errorlevel 1 goto failed

REM  Rumble has no public API, so uploading is a real browser. Without this
REM  the browser binary is missing and every Rumble upload fails at launch
REM  with a message about running `playwright install`.
echo.
echo ---- Browser for Rumble uploads ----
python -m playwright install chromium
if errorlevel 1 (
  echo.
  echo  WARNING: the browser did not install. Everything else is fine;
  echo  Rumble uploads will not work until you run:
  echo      python -m playwright install chromium
  echo.
)

echo.
echo ---- Checking every import the uploader needs ----
python "%~dp0auto_uploader\utils\deps.py" --check
if errorlevel 1 goto incomplete

echo.
echo ========================================
echo   Installation Complete
echo ========================================
echo.
echo  Check it really works, on real video, in about a minute:
echo      VERIFY.bat
echo.
echo  Then start everything:
echo      START.bat
echo.
pause
goto :eof

:incomplete
echo.
echo ========================================
echo   Something is still missing
echo ========================================
echo  The list above says which. Run this file again, or install those
echo  packages by hand and re-run it.
echo.
pause
exit /b 1

:failed
echo.
echo ========================================
echo   Installation FAILED
echo ========================================
echo  The pip output above says why. The usual causes are no internet, or
echo  Python not being on PATH ^(reinstall Python and tick "Add to PATH"^).
echo.
pause
exit /b 1
