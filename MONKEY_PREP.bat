@echo off
REM ============================================================================
REM  MONKEY_PREP.bat - rough cuts of Monkey App streams for BinScripts.
REM  UPLOADS NOTHING. You finish the edit and publish it yourself.
REM
REM  Drag one or more stream files onto this file, or double-click it to
REM  list which videos in "D:\videos stizz" have a Monkey App part.
REM
REM  For each stream you get a folder in "D:\BinScripts drafts":
REM    rough_cut.mp4  ~16 min of the best moments, every swear bleeped,
REM                   anything with a slur cut out
REM    review.html    what is in the cut, what was left out and why
REM                   (age, "stop recording", insults about looks, names
REM                   and socials, slurs) - open it before you edit
REM ============================================================================
title AutoBleep MONKEY PREP
cd /d "%~dp0auto_uploader"
if "%~1"=="" (
  python monkey_prep.py --scan "D:\videos stizz"
) else (
  python monkey_prep.py %*
)
echo.
pause
