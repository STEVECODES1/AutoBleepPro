@echo off
REM ============================================================================
REM  CHECK-LLM.bat - is the smart clip-picker actually running?
REM
REM  Double-click it. No typing needed.
REM
REM  Clips are picked by a model (Gemini/OpenAI/Claude) reading the transcript
REM  and the video frames - that is the part that tells funny from just loud.
REM  If the key in .env is missing, wrong, or the model name is retired, that
REM  pass fails SILENTLY and every clip falls back to a dumb loudness scorer
REM  that cannot tell whether anything was funny. This is the one command that
REM  says which of those two is actually happening.
REM
REM  Whatever it prints, screenshot it and send it back.
REM ============================================================================

cd /d "%~dp0"

echo ============================================================
echo  Checking the clip-picker's API key(s)
echo ============================================================
echo.

python "%~dp0auto_uploader\main.py" --check-llm

echo.
echo ============================================================
echo  Done. Screenshot everything above and send it back.
echo ============================================================
pause
