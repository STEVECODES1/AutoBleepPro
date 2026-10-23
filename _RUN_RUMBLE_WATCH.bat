@echo off
REM Keeps the Rumble recorder running (tools\rumble_watch.py).
REM Stackswopo streams on his Rumble channel (stackswopo10k) since 10-13;
REM this records it when he goes live and hands the file to the uploader.
title AutoBleep RUMBLE RECORDER
cd /d "%~dp0tools"
:loop
C:\Users\Omert\AppData\Local\Programs\Python\Python312\python.exe -X utf8 rumble_watch.py
echo [Keepalive] Rumble recorder stopped at %TIME% - restarting in 30 seconds.
timeout /t 30 /nobreak >nul
goto loop
