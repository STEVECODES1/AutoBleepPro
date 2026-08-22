@echo off
REM ============================================================================
REM  LINKS.bat - are the channels the recorder watches still good?
REM
REM  Double-click it. About twenty seconds.
REM
REM  The recorder says "Not live yet" about a quiet channel and about a handle
REM  that does not exist, in the same words. Those mean opposite things - one
REM  is a normal Tuesday, the other is a stream that will never be recorded no
REM  matter how long it waits. This tells them apart:
REM
REM    LIVE        streaming right now
REM    offline     real channel, quiet - nothing to do
REM    NOT FOUND   the handle does not resolve. This is the one to act on
REM    blocked     the site refused this machine - Cloudflare or network,
REM                not your settings
REM
REM  Add a real capture test - records a few seconds from whatever is live,
REM  checks the file has picture and sound in it, then DELETES it:
REM
REM      LINKS.bat --record-test
REM
REM  It writes into a temp folder and removes it, so nothing it makes can end
REM  up in the watch folder or get uploaded, and it never touches config.json.
REM ============================================================================

setlocal

cd /d "%~dp0"

python "%~dp0tools\check_links.py" %*
set "CODE=%ERRORLEVEL%"

echo.
if "%CODE%"=="0" (
  echo ============================================================
  echo  Every link resolves.
  echo ============================================================
) else (
  echo ============================================================
  echo  Something above needs looking at.
  echo ============================================================
)

pause
