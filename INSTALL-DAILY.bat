@echo off
REM ============================================================================
REM  INSTALL-DAILY.bat - run the clip pipeline every day on its own.
REM
REM  Double-click once. After that Windows runs it daily and you do nothing.
REM
REM  What gets scheduled:
REM    Every day at 05:00, pull the newest VODs off the Rumble channel that
REM    have not been taken before, cut clips from each, and drop them in
REM    watch_folder. Then delete those VOD files - three a day at 3-5 GB each
REM    fills a drive in a week, and the archive still remembers them so they
REM    are never fetched twice.
REM
REM  What is NOT scheduled, because it already happens by itself:
REM    Clips from a stream you just did. The recorder catches the stream, the
REM    uploader censors and uploads it, and clips.auto_from_streams in
REM    config.json cuts clips out of it on the spot. That path needs no timer.
REM
REM  The clips still need the uploader running to actually go out - START.bat
REM  opens it and it posts whatever lands in watch_folder on each platform's
REM  spacing. Leave that window open, or the clips just pile up.
REM
REM  Change the time:  INSTALL-DAILY.bat 07:30
REM  Remove it again:  schtasks /delete /tn "AutoBleepPro Daily Clips" /f
REM  See it in Windows: Task Scheduler -> Task Scheduler Library
REM ============================================================================

cd /d "%~dp0"

set RUNAT=%~1
if "%RUNAT%"=="" set RUNAT=05:00

set TASKNAME=AutoBleepPro Daily Clips

echo ============================================================
echo  Scheduling the daily clip run
echo ============================================================
echo  Task    : %TASKNAME%
echo  Time    : %RUNAT% every day
echo  Runs    : %~dp0CLIP-VODS.bat
echo  Cleanup : deletes each VOD once it has been clipped
echo.

REM  /f replaces an existing task of the same name, so running this twice is
REM  safe and re-running it is how you change the time.
REM  AUTOBLEEP_UNATTENDED=1 is what tells CLIP-VODS.bat not to wait on a
REM  keypress at the end - a scheduled task would sit on that forever - and
REM  to tidy up the VODs afterwards.
schtasks /create /f ^
  /tn "%TASKNAME%" ^
  /tr "cmd /c set AUTOBLEEP_UNATTENDED=1 ^&^& \"%~dp0CLIP-VODS.bat\"" ^
  /sc DAILY ^
  /st %RUNAT%

if %ERRORLEVEL% neq 0 goto failed

echo.
echo ============================================================
echo  Done. It will run every day at %RUNAT%.
echo ============================================================
echo.
echo  Check it worked:
echo    schtasks /query /tn "%TASKNAME%"
echo.
echo  Run it right now without waiting for tomorrow:
echo    schtasks /run /tn "%TASKNAME%"
echo.
echo  Stop it:
echo    schtasks /delete /tn "%TASKNAME%" /f
echo.
echo  NOTE: the machine has to be awake and signed in at %RUNAT%. If it is
echo  asleep the task is skipped, not queued.
echo.
pause
goto :eof

:failed
echo.
echo  FAILED to create the task ^(exit code %ERRORLEVEL%^).
echo  The usual cause is permissions - right-click this file and pick
echo  "Run as administrator", then try again.
echo.
pause
