@echo off
REM ============================================================================
REM  record-live.bat - record the YouTube livestream into the watch folder.
REM
REM  YouTube only, on purpose. The original record-all.bat opened four windows
REM  (Stackswopo/YouTube, OnlyThaGuys/YouTube, Twitch, Kick) all writing into
REM  D:\videos. Only the YouTube channel is wanted now, and it delivers into
REM  auto_uploader\watch_folder instead, so a finished stream flows straight
REM  into the normal upload path with nothing to move by hand.
REM
REM  The recording itself is done by record_stream.py, not by this file. A
REM  plain yt-dlp command stops early on a long stream - the defaults give up
REM  after ten failed fragments, and over four hours of home wifi that is not
REM  a question of if. The Python recorder retries fragments indefinitely,
REM  writes MPEG-TS so an interrupted file still plays, and reconnects and
REM  resumes if it drops mid-stream. See the comments at the top of that file.
REM
REM  Needs:  pip install -U yt-dlp     (ffmpeg is already required by this project)
REM ============================================================================

cd /d "%~dp0"

start "Stackswopo (YouTube)" python record_stream.py "https://www.youtube.com/@stackswopo_/live" --name "Stackswopo"

REM Other channels, kept for reference - uncomment to bring one back. Each
REM gets its own window and records independently; they all deliver to the
REM same watch_folder and the uploader processes them one at a time.
REM start "OnlyThaGuys (YouTube)" python record_stream.py "https://www.youtube.com/@OnlyThaGuys26/live" --name "OnlyThaGuys"
REM start "Stackswopo (Twitch)"   python record_stream.py "https://www.twitch.tv/stackswopo" --name "Stackswopo Twitch"
REM start "Stackswopo (Kick)"     python record_stream.py "https://kick.com/stackswopo1k" --name "Stackswopo Kick"

echo.
echo Recorder started in its own window.
echo It waits for the channel to go live, records the full stream (retrying
echo through network drops), then delivers the finished file to
echo auto_uploader\watch_folder.
echo.
echo Run the uploader alongside it so the file gets picked up automatically:
echo     cd /d "%~dp0..\auto_uploader"
echo     python main.py --watch
echo.
