@echo off
REM ============================================================
REM  SETUP-SLEEP.bat - let the PC sleep between streams.
REM
REM  What it changes (Windows power settings, so YOU run it):
REM   - Power plan: Balanced. Ultimate Performance keeps the CPU at
REM     full speed even when idle - that is the 24/7 fan and the hot room.
REM   - Sleep after 20 minutes with nothing happening.
REM   - Screen off after 10 minutes.
REM   - The USB drive (D:) is never powered down while awake, and USB
REM     selective suspend is off - it already drops on its own sometimes.
REM   - Wake timers allowed, plus a task that wakes the PC every 20 min.
REM
REM  How the pipeline fits around it:
REM   - Every 20 minutes the PC wakes for about 2 minutes. The recorder
REM     checks if Stackswopo is live; the uploader posts anything due.
REM   - If he is live, the recorder holds the PC awake and records from
REM     the START of the stream (YouTube keeps up to 12 hours to rewind),
REM     so waking 20 minutes late loses nothing.
REM   - Editing, uploading and posting hold it awake until they finish.
REM   - Otherwise it goes back to sleep. Fans off, room cool.
REM
REM  Undo everything: UNDO-SLEEP.bat
REM ============================================================
title AutoBleep - sleep setup
echo.
echo  Setting up sleep between streams...
echo.

set BALANCED=381b4222-f694-41f0-9685-ff5bb260df2e

REM Remember the plan in use now, so UNDO-SLEEP.bat can put it back.
for /f "tokens=4" %%G in ('powercfg /getactivescheme') do set PREVIOUS=%%G
if not exist "%~dp0auto_uploader\logs" mkdir "%~dp0auto_uploader\logs"
echo %PREVIOUS%> "%~dp0auto_uploader\logs\power_plan_before_sleep.txt"

powercfg /setactive %BALANCED%
powercfg /change standby-timeout-ac 20
powercfg /change monitor-timeout-ac 10
powercfg /change disk-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
REM Allow wake timers (Sleep > Allow wake timers = Enable).
powercfg /setacvalueindex %BALANCED% SUB_SLEEP RTCWAKE 1
REM USB selective suspend = Disabled, so the Seagate is not cut off.
powercfg /setacvalueindex %BALANCED% 2a737441-1930-4402-8d77-b2bebba308a3 48e6b7a6-50f5-4782-a5d4-53bb8f07e226 0
powercfg /setactive %BALANCED%

REM The wake-up: a task that does nothing except wake the PC every 20 min.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$a = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument '/c exit';" ^
  "$t = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes 20);" ^
  "$s = New-ScheduledTaskSettingsSet -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 1);" ^
  "Register-ScheduledTask -TaskName 'AutoBleep wake check' -Description 'Wakes the PC every 20 minutes so AutoBleep can check for a live stream and post queued clips.' -Action $a -Trigger $t -Settings $s -Force | Out-Null;" ^
  "Write-Host ' Wake task created: every 20 minutes.'"

echo.
echo  Done. Current plan:
powercfg /getactivescheme
echo.
echo  Leave START.bat's RECORDER and UPLOADER windows open - they keep
echo  working across sleep. Undo any time with UNDO-SLEEP.bat.
echo.
pause
