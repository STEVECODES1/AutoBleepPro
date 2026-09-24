@echo off
REM ============================================================================
REM  COMPILE.bat - a long compilation of Stackswopo's own videos, uploaded to
REM  the VOD channel (STACKSWOPOVODS). Separate from START.bat on purpose:
REM  it is a big download + encode, run when you choose to.
REM
REM  Double-click it. It will:
REM    1. Read @stackswopo_'s videos and pick the next series in turn
REM       (Whiteboy Trolling Clips, Funny Moments, Best Moments, ...),
REM       only videos never used in a compilation before.
REM    2. Download them, even out size/frame rate/loudness, fade between them.
REM    3. Join them into one ~2 hour video with chapters and credits.
REM    4. Upload it to STACKSWOPOVODS, then delete every file it made.
REM
REM  Optional:
REM    COMPILE.bat --plan          show what it would use, change nothing
REM    COMPILE.bat --count 3       make three in a row
REM    COMPILE.bat --minutes 60    one hour instead of two
REM    COMPILE.bat --no-upload     build it and keep it, do not upload
REM
REM  FIRST RUN: a browser opens to sign in to YouTube. Pick STACKSWOPOVODS
REM  (@STACKSWOPO10K) - it has its own login, separate from the VOD
REM  uploader's, and uploads refuse to go to any other channel.
REM
REM  Settings: the "compilation" block in auto_uploader\config.json.
REM ============================================================================
title AutoBleep COMPILATION
cd /d "%~dp0auto_uploader"
python compile_channel.py %*
echo.
pause
