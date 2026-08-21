@echo off
REM ============================================================
REM  Keeps the recorder alive.
REM
REM  START.bat used to run the recorder once. When it exited -
REM  a crash, a bad update, anything - the window sat at a
REM  prompt doing nothing, silently, and the next stream went by
REM  with nobody watching. Two streams were lost that way in
REM  three days, and both times the first anyone knew was the
REM  video not being there afterwards.
REM
REM  A recorder that is not running is the one failure this
REM  project cannot recover from: a clip can be re-cut and a post
REM  can be redone, but a stream that was never captured is gone.
REM  So it restarts, and it says so loudly enough to find in the
REM  scrollback.
REM ============================================================
title AutoBleep RECORDER
cd /d "%~dp0tools"

set RESTARTS=0

:loop
python record_stream.py "https://www.youtube.com/@stackswopo_/live" "https://www.twitch.tv/stackswopo" "https://www.twitch.tv/stackswopo/clips?range=7d" "https://kick.com/stackswopo1k" "https://www.youtube.com/@OnlyThaGuys26/live" --name "Stackswopo"

set /a RESTARTS+=1
echo.
echo ============================================================
echo  [Keepalive] The recorder STOPPED at %TIME% (exit %errorlevel%).
echo  [Keepalive] Restart #%RESTARTS% in 10 seconds.
echo  [Keepalive] Scroll up for the reason. Ctrl+C twice to stop
echo              for good.
echo ============================================================
echo.
timeout /t 10 /nobreak >nul
goto loop
