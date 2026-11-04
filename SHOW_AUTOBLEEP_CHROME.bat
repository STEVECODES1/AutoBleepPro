@echo off
REM Brings the off-screen AutoBleep Chrome onto the screen (to sign in or look).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\autobleep_chrome_window.ps1"
