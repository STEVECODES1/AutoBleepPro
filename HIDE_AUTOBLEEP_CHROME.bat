@echo off
REM Puts the AutoBleep Chrome back off-screen so it never covers your work.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\autobleep_chrome_window.ps1" -Hide
