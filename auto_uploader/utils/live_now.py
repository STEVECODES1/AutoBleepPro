"""Is he live right now? Read from the recorder's heartbeat.

10-29: uploads (clips to six platforms, Rumble, recaps, story episodes)
ran while he streamed and shared the PC's ~3 Mbps upload with OBS. Work
that can wait now asks this first and waits until the stream ends.

The recorder writes <recording>/.heartbeat/<name>.json every 30 s with
"state": "recording" while a stream is being recorded. A heartbeat older
than STALE_S counts as not live, so a dead recorder never blocks uploads
forever.
"""
from __future__ import annotations

import glob
import json
import os
import time

RECORDING_DIR = os.environ.get("AUTOBLEEP_RECORDING_DIR", r"C:\AutoBleep\recording")
STALE_S = 180
MAX_WAIT_S = 10 * 3600     # never hold work longer than this in one go


def _beats(folder: str = ""):
    folder = folder or RECORDING_DIR
    for path in glob.glob(os.path.join(folder, ".heartbeat", "*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                beat = json.load(f)
            if isinstance(beat, dict):
                yield beat
        except (OSError, ValueError):
            continue


def is_live(folder: str = "", now: float = 0.0) -> bool:
    """True while any recorder says it is recording, freshly."""
    if os.environ.get("AUTOBLEEP_IGNORE_LIVE") == "1" or os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    now = now or time.time()
    for beat in _beats(folder):
        try:
            fresh = now - float(beat.get("time") or 0) < STALE_S
        except (TypeError, ValueError):
            fresh = False
        if fresh and beat.get("state") == "recording":
            return True
    return False


def wait_until_off_air(what: str, poll_s: int = 60, max_wait_s: int = MAX_WAIT_S,
                       sleep=time.sleep, live=is_live) -> float:
    """Block while he is live (up to max_wait_s). Seconds waited."""
    waited = 0.0
    said = False
    while live() and waited < max_wait_s:
        if not said:
            print(f"[Live] He is live - {what} waits until the stream ends, "
                  f"so OBS keeps the whole upload.")
            said = True
        sleep(poll_s)
        waited += poll_s
    if said:
        print(f"[Live] Stream over - {what} goes ahead.")
    return waited
