@echo off
REM ============================================================================
REM  START.bat - the whole system, in one double-click.
REM
REM  Opens two windows and leaves them running:
REM
REM    1. RECORDER  - waits for YouTube, Twitch or Kick to go live, records the
REM                   full stream, and fetches any new Twitch clips. Everything
REM                   it produces lands in auto_uploader\watch_folder.
REM
REM    2. UPLOADER  - watches that folder and handles whatever arrives:
REM                   censors it (YouTube only), uploads it, cuts a finished
REM                   stream into clips, and posts the announcements.
REM
REM  Anything already sitting in watch_folder is processed first, because
REM  --watch by design only reacts to files that ARRIVE - a file that was
REM  already there never triggers the event it is waiting for, and every
REM  clip left over from a previous run would sit there forever.
REM
REM  Close either window to stop that half. Ctrl+C does the same.
REM ============================================================================

cd /d "%~dp0"

echo ============================================================
echo  Pulling latest code from GitHub...
echo ============================================================
git pull
set GIT_EXIT=%ERRORLEVEL%
if %GIT_EXIT% neq 0 goto pull_warn
goto pull_done

:pull_warn
echo.
echo  WARNING: git pull failed ^(exit code %GIT_EXIT%^).
echo  Check your internet connection or run: git status
echo  Continuing with the version already on disk...
echo.
timeout /t 4 /nobreak >nul

:pull_done
echo.
echo ============================================================
echo  Starting AutoBleepPro
echo ============================================================
echo.
echo  Recorder : YouTube + Twitch + Kick + OnlyThaGuys live, plus new Twitch clips
echo  Uploader : censor, upload, clip, announce
echo  Folder   : %~dp0auto_uploader\watch_folder
echo.

start "AutoBleep RECORDER" cmd /k "cd /d "%~dp0tools" && python record_stream.py "https://www.youtube.com/@stackswopo_/live" "https://www.twitch.tv/stackswopo" "https://www.twitch.tv/stackswopo/clips?range=7d" "https://kick.com/stackswopo1k" "https://www.youtube.com/@OnlyThaGuys26/live" --name "Stackswopo""

REM A moment apart so the two windows do not fight over the console while
REM they start, and so the recorder's banner is readable.
timeout /t 3 /nobreak >nul

start "AutoBleep UPLOADER" cmd /k "cd /d "%~dp0auto_uploader" && python main.py --batch && python main.py --watch"

echo  Both windows are open. This one can be closed.
echo.
echo  Useful, in the uploader window:
echo    python main.py --posting-status --verify   what would post right now
echo    python main.py --reset-failures            clear a tripped breaker
echo    python main.py --set-env KEY=VALUE         add a credential to .env
echo.
echo  To stop everything immediately, including a running --watch, create:
echo    %~dp0auto_uploader\STOP_POSTING
echo.
pause
