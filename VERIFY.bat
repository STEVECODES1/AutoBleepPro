@echo off
REM ============================================================================
REM  VERIFY.bat - prove the pipeline still works, on real video, in a minute.
REM
REM  Double-click it after a pull, or any time something looks wrong and you
REM  want to know whether it is the code or the stream.
REM
REM  It builds its own 20-second test footage with ffmpeg - one colour and one
REM  tone per second, so every frame and every sample says which second of the
REM  source it came from - then runs the real censor, the real clip renderer
REM  and the real caption writer over it and measures what comes out.
REM
REM  What it answers:
REM    * is a bad word actually SILENCED, and only it?
REM    * does censoring shorten the video or re-encode the picture? (no)
REM    * are clips 1080x1920, the right length, cut where they were asked?
REM    * does the SOUND of a clip start on the same second as the PICTURE?
REM    * do the captions line up with the speech instead of running against it?
REM    * is a muted word kept out of the captions printed underneath it?
REM
REM  Whisper is not used - a transcript is fed straight into the cache the
REM  real run reads - so this takes about a minute, not an hour.
REM
REM  PASSED means every one of those held. FAILED lists exactly which did not.
REM ============================================================================

cd /d "%~dp0"

echo ============================================================
echo  Verifying AutoBleepPro on real video
echo ============================================================
echo.

python "%~dp0tools\e2e_check.py"
set "CODE=%ERRORLEVEL%"

echo.
if "%CODE%"=="0" (
  echo ============================================================
  echo  Everything checked out.
  echo ============================================================
) else (
  echo ============================================================
  echo  Something is wrong - the failing checks are listed above.
  echo ============================================================
)

pause
