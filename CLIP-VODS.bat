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
REM  Run from a COPY of this file.
REM
REM  `git pull` below updates the repo - including this batch file. cmd.exe
REM  does not read a .bat into memory; it reads one line, runs it, then seeks
REM  back to the saved byte offset for the next one. Rewrite the file mid-run
REM  and that offset now points into the middle of some other line, so the
REM  rest of the run executes garbage. It fails differently every time, which
REM  is the worst kind of failure to chase.
REM
REM  So: copy this file to TEMP first, hand over to the copy, and let the pull
REM  rewrite the original harmlessly. The copy is never touched by git.
REM
REM  %~dp0 in the copy points at TEMP, not the repo, so the real repo path
REM  travels in AUTOBLEEP_ROOT and every path below uses that instead.
REM
REM  If the copy cannot be made, the run simply continues unguarded - the same
REM  behaviour as before. A missing safety net is not a reason not to run.
REM ---------------------------------------------------------------------------
if not "%AUTOBLEEP_STAGE2%"=="1" set "AUTOBLEEP_ROOT=%~dp0"
REM  The stale copy goes first, so a copy that fails hands over to nothing
REM  and the run continues inline rather than replaying last week's file.
if not "%AUTOBLEEP_STAGE2%"=="1" del /q "%TEMP%\autobleep_clipvods.bat" >nul 2>&1
if not "%AUTOBLEEP_STAGE2%"=="1" copy /y "%~f0" "%TEMP%\autobleep_clipvods.bat" >nul 2>&1
if not "%AUTOBLEEP_STAGE2%"=="1" if exist "%TEMP%\autobleep_clipvods.bat" ( set "AUTOBLEEP_STAGE2=1" & call "%TEMP%\autobleep_clipvods.bat" %* & exit /b )
if "%AUTOBLEEP_ROOT%"=="" set "AUTOBLEEP_ROOT=%~dp0"
cd /d "%AUTOBLEEP_ROOT%"

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

REM  An empty second argument is the same as not passing one - the
REM  scheduled task always passes both, and passing "" must not mean
REM  "clip a folder called nothing".
set SOURCE=%~2
if "%SOURCE%"=="" set SOURCE=https://rumble.com/user/stackswopo10k

REM  INSTALL-DAILY.bat sets these two. Unattended means: do not wait for a
REM  keypress at the end (a scheduled task would sit on that forever), and
REM  delete the VODs after clipping them, because three a day at 3-5 GB
REM  each fills the drive inside a week.
REM
REM  Compared with the spaces taken out. `set FOO=1 && next` - which is how
REM  an older INSTALL-DAILY.bat wrote the scheduled command - stores "1 ",
REM  space and all, because everything between the = and the && is the value.
REM  "1 " never equals "1", so --tidy-vods was never passed and the `pause`
REM  at the bottom ran inside a scheduled task with no keyboard attached:
REM  the task sat there forever and never ran again the next day.
set "UNATTENDED=%AUTOBLEEP_UNATTENDED%"
set "UNATTENDED=%UNATTENDED: =%"

set EXTRA=
if "%UNATTENDED%"=="1" set EXTRA=--tidy-vods

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
echo  Clips  : %AUTOBLEEP_ROOT%auto_uploader\watch_folder
echo.
echo  Do NOT click inside this window while it runs - see the note at the
echo  top of this file. If the title bar ever says "Select", press Esc.
echo.

python "%AUTOBLEEP_ROOT%auto_uploader\main.py" --clips-from "%SOURCE%" --limit %LIMIT% %EXTRA%

echo.
echo ============================================================
echo  Finished. Clips are in auto_uploader\watch_folder.
echo  START.bat's uploader window posts them on each platform's spacing.
echo ============================================================
if not "%UNATTENDED%"=="1" pause
