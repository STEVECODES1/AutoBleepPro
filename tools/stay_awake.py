"""Keep the PC from sleeping while AutoBleep is working, and only then.

Owner, 10-19: "if something is happening the pc shouldn't go to sleep until
it's done". Every 30 s this asks Windows to treat the machine as in use
(SetThreadExecutionState(ES_SYSTEM_REQUIRED) - the same call a video player
makes) while any of these is true:
  - ffmpeg is running (a live recording or a render),
  - an upload heartbeat (logs/uploading.json) is under 10 minutes old,
  - the uploader's processing marker (logs/processing.json) is there,
  - a recorder heartbeat says "recording".
When none is, it stops asking and the normal sleep timer applies again.
No Windows setting is changed. Started once by _AUTOSTART.ps1.

  python -X utf8 tools/stay_awake.py [--once]
"""
import ctypes
import glob
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ES_SYSTEM_REQUIRED = 0x00000001
HEARTBEATS = (r"C:\AutoBleep\recording\.heartbeat\*.json",
              os.path.join(ROOT, "auto_uploader", "recording", ".heartbeat", "*.json"))


def _fresh(path, seconds):
    try:
        return time.time() - os.path.getmtime(path) < seconds
    except OSError:
        return False


def _ffmpeg_running():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ffmpeg.exe", "/NH"],
                             capture_output=True, text=True, timeout=20).stdout
        return "ffmpeg.exe" in out.lower()
    except Exception:
        return False


def _recording():
    for pattern in HEARTBEATS:
        for path in glob.glob(pattern):
            if not _fresh(path, 180):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    if json.load(f).get("state") == "recording":
                        return True
            except Exception:
                continue
    return False


def busy_reason(ffmpeg=_ffmpeg_running, recording=_recording):
    if _fresh(os.path.join(ROOT, "logs", "uploading.json"), 600):
        return "uploading"
    if os.path.exists(os.path.join(ROOT, "logs", "processing.json")):
        return "processing"
    if recording():
        return "recording"
    if ffmpeg():
        return "ffmpeg"
    return ""


def hold_awake():
    if sys.platform == "win32":
        ctypes.windll.kernel32.SetThreadExecutionState(ES_SYSTEM_REQUIRED)


def main():
    last = None
    while True:
        why = busy_reason()
        if why:
            hold_awake()
        if why != last:
            print(time.strftime("%m-%d %H:%M"), f"busy ({why}) - staying awake" if why
                  else "idle - normal sleep timer", flush=True)
            last = why
        if "--once" in sys.argv:
            return
        time.sleep(30)


if __name__ == "__main__":
    main()
