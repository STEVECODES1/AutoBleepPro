@echo off
setlocal enabledelayedexpansion
REM ============================================================================
REM  migrate-to-git.bat - move from a ZIP extract to a real git clone, once.
REM
REM  A ZIP extract cannot `git pull`, so every fix has to be re-downloaded by
REM  hand and it is never obvious which version is actually running. A clone
REM  updates with one command and the build stamp always tells the truth.
REM
REM  This does NOT touch the existing folder. It clones alongside it and
REM  copies the files git deliberately does not track - credentials, tokens
REM  and upload history - into the new one. If anything goes wrong the old
REM  folder is still there, untouched.
REM
REM  Run it from anywhere:  tools\migrate-to-git.bat
REM ============================================================================

set "REPO=https://github.com/STEVECODES1/AutoBleepPro.git"
set "OLD=%~dp0.."
set "NEW=%~dp0..\..\AutoBleepPro-git"

echo ============================================================
echo  From : %OLD%
echo  To   : %NEW%
echo ============================================================
echo.

where git >nul 2>&1
if errorlevel 1 (
    echo [ERROR] git is not installed. Get it from https://git-scm.com/download/win
    echo         then run this again.
    pause
    exit /b 1
)

if exist "%NEW%\.git" (
    echo [SKIP] %NEW% is already a clone - pulling instead.
    pushd "%NEW%"
    git pull
    popd
    goto :copysecrets
)

if exist "%NEW%" (
    echo [ERROR] %NEW% already exists but is not a git clone.
    echo         Rename or delete it, then run this again.
    pause
    exit /b 1
)

echo Cloning...
git clone "%REPO%" "%NEW%"
if errorlevel 1 (
    echo [ERROR] Clone failed. Check your internet connection.
    pause
    exit /b 1
)

:copysecrets
echo.
echo Copying the files git does not track...

REM Each of these is either a credential or state that must not be lost.
REM uploaded_hashes.json especially: without it the tool has no memory of
REM what it already uploaded and would re-upload the lot.
call :copyone ".env"
call :copyone "client_secrets.json"
call :copyone "youtube_token.json"
call :copyone "uploaded_hashes.json"
call :copyone "posting_state.json"
call :copyone "clip_jobs.json"

echo.
echo ============================================================
echo  Done. Work from the new folder from now on:
echo.
echo     cd /d "%NEW%\auto_uploader"
echo     python main.py --posting-status
echo.
echo  Check the banner says Build: 2026-08-05.2 or newer.
echo  Future updates are just:  git pull
echo.
echo  The old folder is untouched. Delete it once you are happy.
echo ============================================================
pause
exit /b 0

:copyone
if exist "%OLD%\auto_uploader\%~1" (
    copy /Y "%OLD%\auto_uploader\%~1" "%NEW%\auto_uploader\%~1" >nul
    if errorlevel 1 (
        echo   [WARN] could not copy %~1
    ) else (
        echo   copied %~1
    )
) else (
    echo   [--]   no %~1 to copy
)
exit /b 0
