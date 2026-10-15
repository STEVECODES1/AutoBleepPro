@echo off
REM ============================================================================
REM  MONKEY_PREP.bat - rough cuts of Monkey App streams for BinScripts.
REM  UPLOADS NOTHING. You finish the edit and publish it yourself.
REM
REM  Double-click:  makes the NEXT draft - the stream in "D:\videos stizz"
REM                 with the most Monkey App that has no draft yet.
REM  Drag files on: drafts exactly those streams.
REM
REM  Never the same moment twice: anything BinScripts already posted
REM  (checked against what is said in each posted video) and anything an
REM  earlier draft already used is left out, and review.html says so.
REM
REM  For each stream you get a folder in "D:\BinScripts drafts":
REM    rough_cut.mp4  ~16 min of the best moments, every swear bleeped,
REM                   anything with a slur cut out
REM    review.html    what is in the cut, what was left out and why
REM                   (age, "stop recording", insults about looks, names
REM                   and socials, slurs, already used) - open it first
REM ============================================================================
title AutoBleep MONKEY PREP
cd /d "%~dp0auto_uploader"
if "%~1"=="" (
  python monkey_prep.py --next "D:\videos stizz"
) else (
  python monkey_prep.py %*
)
echo.
start "" "D:\BinScripts drafts"
pause
