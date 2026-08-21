@echo off
REM Keeps the uploader alive - see _RUN_RECORDER.bat for why.
REM
REM Less urgent than the recorder: a clip that did not post is
REM still on disk and still in the queue, so a dead uploader
REM delays work rather than losing it. It still should not sit
REM dead for a day without saying so.
title AutoBleep UPLOADER
cd /d "%~dp0auto_uploader"

set RESTARTS=0

:loop
python main.py --batch
python main.py --watch

set /a RESTARTS+=1
echo.
echo ============================================================
echo  [Keepalive] The uploader STOPPED at %TIME% (exit %errorlevel%).
echo  [Keepalive] Restart #%RESTARTS% in 15 seconds.
echo  [Keepalive] Queued clips are safe - they are on disk.
echo              Ctrl+C twice to stop for good.
echo ============================================================
echo.
timeout /t 15 /nobreak >nul
goto loop
