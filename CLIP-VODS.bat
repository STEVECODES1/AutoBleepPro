@echo off
REM ============================================================================
REM  CLIP-VODS.bat - pull recent VODs off your own Rumble channel and clip them.
REM
REM  Double-click it. No arguments needed.
REM
REM  What it does, in order:
REM    1. Downloads the newest VODs from rumble.com/user/stackswopo10k that it
REM       has not taken before (the archive file beside them is what remembers).
REM    2. Checks each one really is yours before downloading it - a channel page
REM       carries recommended videos from other creators too.
REM    3. Transcribes each VOD and cuts clips out of it.
REM    4. Drops the clips in auto_uploader\watch_folder.
REM
REM  It does NOT post them. START.bat's uploader window does that, picking them
REM  up as they land. Have that running too.
REM
REM  This is slow and that is normal: a 3-hour VOD is a 10-minute download and
REM  a much longer transcription. Leave it alone and check back.
REM
REM  Optional arguments:
REM    CLIP-VODS.bat 5                       take 5 VODs instead of 3
REM    CLIP-VODS.bat 3 "D:\videos stizz"     clip a folder instead of a channel
REM ============================================================================

cd /d "%~dp0"

REM ---------------------------------------------------------------------------
REM  Turn OFF console QuickEdit. With it on - the Windows default - clicking
REM  in this window starts a text selection, and that FREEZES this program the
REM  moment it next tries to print. The title bar gains a "Select" prefix and
REM  nothing else happens: no error, no crash, no hint.
REM
REM  That is exactly how a VOD run sat paused mid-remux from 4am to 4pm on one
REM  stray click, while the recorder in the next window kept going and made it
REM  all look healthy.
REM
REM  Takes effect for console windows opened from now on, so this run is only
REM  protected if you started it by double-clicking. To put it back:
REM    reg add "HKCU\Console" /v QuickEdit /t REG_DWORD /d 1 /f
REM ---------------------------------------------------------------------------
reg add "HKCU\Console" /v QuickEdit /t REG_DWORD /d 0 /f >nul 2>&1

set LIMIT=%~1
if "%LIMIT%"=="" set LIMIT=3

set SOURCE=%~2
if "%SOURCE%"=="" set SOURCE=https://rumble.com/user/stackswopo10k

REM  INSTALL-DAILY.bat sets these two. Unattended means: do not wait for a
REM  keypress at the end (a scheduled task would sit on that forever), and
REM  delete the VODs after clipping them, because three a day at 3-5 GB
REM  each fills the drive inside a week.
set EXTRA=
if "%AUTOBLEEP_UNATTENDED%"=="1" set EXTRA=--tidy-vods

echo ============================================================
echo  Pulling latest code from GitHub...
echo ============================================================
git pull

echo.
echo ============================================================
echo  Clipping VODs
echo ============================================================
echo  Source : %SOURCE%
echo  Take   : %LIMIT%
echo  Clips  : %~dp0auto_uploader\watch_folder
echo.
echo  Do NOT click inside this window while it runs - see the note at the
echo  top of this file. If the title bar ever says "Select", press Esc.
echo.

python "%~dp0auto_uploader\main.py" --clips-from "%SOURCE%" --limit %LIMIT% %EXTRA%

echo.
echo ============================================================
echo  Finished. Clips are in auto_uploader\watch_folder.
echo  START.bat's uploader window posts them on each platform's spacing.
echo ============================================================
if not "%AUTOBLEEP_UNATTENDED%"=="1" pause
