@echo off
REM Keeps the uploader alive - see _RUN_RECORDER.bat for why.
REM
REM Less urgent than the recorder: a clip that did not post is
REM still on disk and still in the queue, so a dead uploader
REM delays work rather than losing it. It still should not sit
REM dead for a day without saying so.
title AutoBleep UPLOADER
cd /d "%~dp0auto_uploader"

setlocal
set RESTARTS=0

:loop
python main.py --batch
python main.py --watch

REM  Grabbed BEFORE anything else runs - see _RUN_RECORDER.bat. `set /a`
REM  sets ERRORLEVEL too, so a banner that reads it after the counter
REM  increments reports the counter, and every crash printed "exit 0".
set "CODE=%ERRORLEVEL%"

REM  Exit 3 is checkout_sanity's "this tree is half-merged". Restarting
REM  cannot fix a file with conflict markers in it - it failed five
REM  times in seventy-five seconds proving that - and the message it
REM  printed above says exactly what to type. Stop and let it be read.
if "%CODE%"=="3" goto broken

set /a RESTARTS+=1
call :backoff

echo.
echo ============================================================
echo  [Keepalive] The uploader STOPPED at %TIME% (exit %CODE%).
echo  [Keepalive] Restart #%RESTARTS% in %WAIT% seconds.
echo  [Keepalive] Queued clips are safe - they are on disk.
echo              Ctrl+C twice to stop for good.
echo ============================================================
echo.
timeout /t %WAIT% /nobreak >nul
goto loop

:broken
echo.
echo  [Keepalive] NOT restarting - see the message above. A restart
echo              cannot repair a half-merged checkout.
echo.
pause
goto :eof

REM  Grows with the restart count, stops at two minutes, never gives up -
REM  see _RUN_RECORDER.bat for why.
:backoff
set WAIT=15
if %RESTARTS% GEQ 6 set WAIT=30
if %RESTARTS% GEQ 21 set WAIT=120
goto :eof
