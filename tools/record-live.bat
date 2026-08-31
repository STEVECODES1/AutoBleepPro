@echo off
REM ============================================================================
REM  record-live.bat - record YouTube AND Twitch into the watch folder.
REM
REM  Double-click this file. It does not matter what folder you are in: the
REM  cd below jumps to wherever this .bat lives, which is why running
REM  record_stream.py by hand from C:\Users\<you> fails and this does not.
REM
REM  Two sources, one window:
REM    - the Stackswopo YouTube livestream
REM    - the Twitch livestream
REM
REM  The OnlyThaGuys YouTube livestream, the Kick livestream and the
REM  Twitch clips page all used to be here too. Dropped 2026-08-31: four
REM  simultaneous recordings plus a VOD download plus GPU transcription
REM  overloaded the drive's write throughput on a real night and cost
REM  real recording time. Add any back only as a deliberate decision,
REM  not by copying this file forward unchanged.
REM
REM  Everything lands in auto_uploader\watch_folder, so a finished stream
REM  flows straight into the normal upload path with nothing to move by hand.
REM
REM  The recording itself is done by record_stream.py, not by this file. A
REM  plain yt-dlp command stops early on a long stream - the defaults give up
REM  after ten failed fragments, and over four hours of home wifi that is not
REM  a question of if. The Python recorder retries fragments indefinitely,
REM  writes MPEG-TS so an interrupted file still plays, reconnects and resumes
REM  if it drops mid-stream, and falls back to the published VOD if the
REM  recording still came up short. See the comments at the top of that file.
REM
REM  Needs:  pip install -U yt-dlp     (ffmpeg is already required by this project)
REM ============================================================================

cd /d "%~dp0"

start "Stackswopo (YouTube + Twitch)" python record_stream.py ^
    "https://www.youtube.com/@stackswopo_/live" ^
    "https://www.twitch.tv/stackswopo" ^
    --name "Stackswopo"

REM Every source delivers to the same watch_folder, and the uploader
REM processes them one at a time - one video through the GPU at once, so
REM two channels going live together costs nothing but a queue.

echo.
echo Recorder started in its own window.
echo It waits for either channel to go live, records the full stream (retrying
echo through network drops), then delivers the finished file to
echo auto_uploader\watch_folder.
echo.
echo While it is waiting it stays quiet on purpose - one line every 30 minutes
echo rather than a countdown every minute. The full yt-dlp output is still
echo written to the .log beside the recording.
echo.
echo Run the uploader alongside it so the files get picked up automatically:
echo     cd /d "%~dp0..\auto_uploader"
echo     python main.py --watch
echo.
