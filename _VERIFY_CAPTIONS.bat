@echo off
REM ============================================================
REM  _VERIFY_CAPTIONS.bat
REM
REM  One-time check after the extract_audio() fix (2026-09-04):
REM  does the real GPU model (large-v3-turbo, cuda, as configured
REM  in auto_uploader\config.json) actually put captions back on
REM  the audio's real clock now?
REM
REM  Runs the project's own diagnostic (main.py --check-sync) with
REM  no file named - it picks the newest video itself, and falls
REM  back to one of the already-rendered clips if the source VOD
REM  has already been cleaned up, which is the case right now.
REM  That is fine: it is the same file the captions came out wrong
REM  on before.
REM
REM  Read the verdict at the bottom of the output. If it says
REM  "The cut is exact" (and nothing above it about the transcript
REM  being off), captions are safe to turn back on: set
REM  clips.burn_captions to true in auto_uploader\config.json.
REM  Anything else - paste the whole output back and it decides
REM  what's still wrong.
REM ============================================================
title AutoBleep - Verify Captions
cd /d "%~dp0auto_uploader"
python main.py --check-sync
echo.
echo ============================================================
echo  Done. Read the verdict above.
echo ============================================================
pause
