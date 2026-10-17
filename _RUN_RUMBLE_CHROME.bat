@echo off
REM Keeps the Rumble Chrome (the logged-in browser Rumble uploads and the
REM live relay drive) running: checks every minute, starts it if it is gone.
title AutoBleep RUMBLE CHROME KEEPER
:loop
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\ensure_rumble_chrome.ps1"
timeout /t 60 /nobreak >nul
goto loop
