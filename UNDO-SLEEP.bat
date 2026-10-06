@echo off
REM ============================================================
REM  UNDO-SLEEP.bat - put power back the way it was before
REM  SETUP-SLEEP.bat: the previous plan, no sleep, no wake task.
REM ============================================================
title AutoBleep - undo sleep setup
set SAVED=%~dp0auto_uploader\logs\power_plan_before_sleep.txt
set PREVIOUS=
if exist "%SAVED%" set /p PREVIOUS=<"%SAVED%"
if defined PREVIOUS (
  powercfg /setactive %PREVIOUS%
) else (
  echo  No saved plan found - staying on the current one.
)
powercfg /change standby-timeout-ac 0
powershell -NoProfile -Command "Unregister-ScheduledTask -TaskName 'AutoBleep wake check' -Confirm:$false -ErrorAction SilentlyContinue; Write-Host ' Wake task removed.'"
echo.
powercfg /getactivescheme
echo.
pause
