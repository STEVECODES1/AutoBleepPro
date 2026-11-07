"""From OUTSIDE the uploader: notice a hung main.py --watch (V4, 11-07).

main.py writes logs/uploader_beat.json every 30 s. The brain runs inside
that same loop, so it can never report its own hang; this thread runs in
tools/rumble_watch.py (a separate process that is always up) and, when the
beat is older than STALE_MIN while the uploader process still exists and no
upload is running, runs tools/quiet_restart.ps1 - which does the actual
restart under its own safety rules (busy marker, upload beat, children).
Never raises; a missing beat file (old uploader) does nothing.
"""

import json
import os
import subprocess
import threading
import time

_APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BEAT = os.path.join(_APP, "logs", "uploader_beat.json")
UPLOADING = os.path.join(_APP, "logs", "uploading.json")
RESTART = os.path.join(os.path.dirname(_APP), "tools", "quiet_restart.ps1")
STALE_MIN = 10
CHECK_S = 120
_last_kick = 0.0


def _pid_alive(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid) and "python" in psutil.Process(pid).name().lower()
    except Exception:
        return False


def check(say=print, now: float = 0.0) -> bool:
    """True when a restart was requested."""
    global _last_kick
    now = now or time.time()
    try:
        with open(BEAT, encoding="utf-8") as f:
            beat = json.load(f)
        age = now - float(beat.get("at", 0))
        if age < STALE_MIN * 60 or not _pid_alive(int(beat.get("pid", 0))):
            return False
        if os.path.exists(UPLOADING) and now - os.path.getmtime(UPLOADING) < 600:
            return False      # an upload is running; the loop is just blocked on it
        if now - _last_kick < 3600:
            return False
        _last_kick = now
        say(f"[HangWatch] the uploader's heartbeat is {age / 60:.0f} min old - "
            f"asking quiet_restart to restart it.")
        subprocess.Popen(["powershell", "-ExecutionPolicy", "Bypass", "-File", RESTART,
                          "-Minutes", "30"], creationflags=0x08000000)
        try:
            from utils.brain import send_alert
            send_alert("Uploader was hung - restarted",
                       f"No heartbeat for {age / 60:.0f} min; quiet_restart kicked it.", urgent=False)
        except Exception:
            pass
        return True
    except Exception:
        return False


def start(say=print) -> None:
    def loop():
        while True:
            time.sleep(CHECK_S)
            check(say)
    threading.Thread(target=loop, name="hang-watch", daemon=True).start()
