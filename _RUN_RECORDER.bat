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

setlocal
set RESTARTS=0

REM  Down to two sources as of 2026-08-31. Four simultaneous recordings
REM  plus a VOD download plus GPU transcription overloaded the drive's
REM  write throughput on a real night and cost real recording time.
REM  @OnlyThaGuys26, the Twitch clips page and Kick were all dropped -
REM  add any back only as a deliberate decision, not by copying this
REM  file forward unchanged.
:loop
python record_stream.py "https://www.youtube.com/@stackswopo_/live" "https://www.twitch.tv/stackswopo" --name "Stackswopo"

REM  Grabbed BEFORE anything else runs. ERRORLEVEL is whatever the LAST
REM  command set, and `set /a` sets it too - so reading it after the
REM  counter increments reports the counter's success, not the crash.
REM  Every "exit 0" in this window's history was that: the recorder died
REM  of something and the banner said it exited cleanly.
set "CODE=%ERRORLEVEL%"

set /a RESTARTS+=1
call :backoff

echo.
echo ============================================================
echo  [Keepalive] The recorder STOPPED at %TIME% (exit %CODE%).
echo  [Keepalive] Restart #%RESTARTS% in %WAIT% seconds.
echo  [Keepalive] Scroll up for the reason. Ctrl+C twice to stop
echo              for good.
echo ============================================================
echo.
timeout /t %WAIT% /nobreak >nul
goto loop

REM ---------------------------------------------------------------------------
REM  Back off, but never give up.
REM
REM  A crash that happens instantly - a bad import after a pull, a missing
REM  yt-dlp - restarts every 10 seconds forever. The window fills with
REM  identical banners, the real error scrolls out of reach, and from across
REM  the room it looks busy rather than broken.
REM
REM  So the wait grows with the restart count and stops at two minutes. It
REM  never stops retrying: a stream that starts an hour later must still be
REM  caught, and two minutes of latency on that is nothing.
REM ---------------------------------------------------------------------------
:backoff
set WAIT=10
if %RESTARTS% GEQ 6 set WAIT=30
if %RESTARTS% GEQ 21 set WAIT=120
goto :eof
