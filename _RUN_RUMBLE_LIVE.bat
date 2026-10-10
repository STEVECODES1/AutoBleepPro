@echo off
REM Keeps the Rumble live relay running (tools\rumble_live.py).
REM It waits for the recorder to start recording Stackswopo, then streams
REM the live to the BinScripts Rumble channel and ends it when he ends.
title AutoBleep RUMBLE LIVE
cd /d "%~dp0tools"
:loop
C:\Python314\python.exe -X utf8 rumble_live.py
echo [Keepalive] Rumble live relay stopped at %TIME% - restarting in 30 seconds.
timeout /t 30 /nobreak >nul
goto loop
