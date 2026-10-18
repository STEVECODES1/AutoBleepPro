"""
utils/upload_beat.py - "an upload is moving right now", for anything that
restarts the uploader.

10-17 22:43: a restart judged the uploader idle and killed it 2.5 minutes
into the YouTube upload of the 4.4 GB full stream. YouTube kept the broken
half as "Processing will begin shortly", the next run took it for the
finished video, and the stream was missing from wopovod until it was
uploaded again on 10-18. The busy marker (logs/processing.json) did not
stop it, so the uploads now say so themselves: every chunk touches
logs/uploading.json (at most every 15 s), and tools/quiet_restart.ps1 and
tools/restart_when_idle.ps1 treat one touched in the last 10 minutes as
busy.
"""

from __future__ import annotations

import json
import os
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(_ROOT, "logs", "uploading.json")
EVERY_S = 15.0
_LAST = {"at": 0.0}
_LOCK = threading.Lock()


def beat(what: str, pct=None, path: str = "") -> bool:
    """Note that `what` is uploading. Cheap: writes at most every EVERY_S."""
    if os.environ.get("PYTEST_CURRENT_TEST") and not path:
        return False
    now = time.time()
    with _LOCK:
        if now - _LAST["at"] < EVERY_S:
            return False
        _LAST["at"] = now
    try:
        target = path or PATH
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid(), "what": what, "pct": pct, "at": now}, handle)
        return True
    except OSError:
        return False
