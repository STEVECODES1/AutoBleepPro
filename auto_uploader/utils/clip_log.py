"""
One line per clip, per platform. What happened, and why if it didn't.

WHY A SEPARATE LOG
------------------
The story of a clip is spread across four places right now: the console
scrollback (gone when the window closes), publishers.log (every HTTP
detail), posting_state.json (counters, no names) and clip_jobs.json
(machine state). Answering "did clip 7 go to Instagram, and if not why"
means reading all four.

This is the one file that answers it. Fixed columns, one line each, no
stack traces - the detail is the SENTENCE a person needs, not the
exception that produced it:

  08-11 19:42  cut     ok    Stream: shadows 8/4/26          20 clips
  08-11 19:45  rumble  ok    Clip 01 He walked into the water
  08-11 19:46  ig      ok    Clip 01 He walked into the water
  08-11 19:46  fb      wait  Clip 01 He walked into the water  spacing 80 min
  08-11 20:10  ig      FAIL  Clip 02 The whole lobby turned    token expired

It is trimmed to the last few hundred lines on every write, because a log
nobody opens is a log that is too long to open.
"""

from __future__ import annotations

import os
import time
from typing import Optional

LOG_NAME = "clips.log"

# Old enough to be history, short enough to read in one screen-scroll.
MAX_LINES = 400

# The four things that can happen. Uppercase FAIL on purpose: it is the
# only one worth spotting while scrolling.
OK = "ok"
WAIT = "wait"
SKIP = "skip"
FAIL = "FAIL"

_NAME_WIDTH = 34
_DETAIL_WIDTH = 60


def log_path(logs_folder: str) -> str:
    return os.path.join(logs_folder or ".", LOG_NAME)


def _line(stage: str, status: str, name: str, detail: str) -> str:
    name = " ".join(str(name or "").split())
    if len(name) > _NAME_WIDTH:
        name = name[:_NAME_WIDTH - 1] + "…"
    detail = " ".join(str(detail or "").split())
    if len(detail) > _DETAIL_WIDTH:
        detail = detail[:_DETAIL_WIDTH - 1] + "…"
    return (f"{time.strftime('%m-%d %H:%M')}  {stage:<7} {status:<5} "
            f"{name:<{_NAME_WIDTH}}  {detail}".rstrip())


def record(logs_folder: str, stage: str, status: str, name: str,
           detail: str = "") -> str:
    """Append one line. Returns it, so a caller can print the same thing.

    Never raises: a journal that can break the run it is describing is
    worse than no journal.
    """
    line = _line(stage, status, name, detail)
    try:
        os.makedirs(logs_folder or ".", exist_ok=True)
        path = log_path(logs_folder)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        _trim(path)
    except OSError:
        pass
    return line


def _trim(path: str) -> None:
    """Keep the tail. Rewritten via a temp file so a crash mid-trim
    cannot leave the log half-written."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return
    if len(lines) <= MAX_LINES + 100:
        # Slack above the limit so this rewrites occasionally rather than
        # on every single line.
        return
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(lines[-MAX_LINES:])
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def tail(logs_folder: str, limit: int = 40) -> list:
    """The last `limit` lines, oldest first."""
    try:
        with open(log_path(logs_folder), "r", encoding="utf-8") as f:
            lines = [l.rstrip("\n") for l in f if l.strip()]
    except OSError:
        return []
    return lines[-limit:] if limit else lines


def counts(logs_folder: str, day: str = "") -> dict:
    """{status: n} for one day, today by default."""
    day = day or time.strftime("%m-%d")
    tally: dict = {}
    for line in tail(logs_folder, 0):
        if not line.startswith(day):
            continue
        parts = line.split()
        if len(parts) >= 4:
            tally[parts[3]] = tally.get(parts[3], 0) + 1
    return tally


def report(logs_folder: str, limit: int = 40) -> str:
    """The whole answer to "how are the clips doing", as text."""
    lines = tail(logs_folder, limit)
    if not lines:
        return ("No clips logged yet. This fills in as clips are cut and "
                "posted.")
    tally = counts(logs_folder)
    header = "  ".join(f"{status} {n}" for status, n in sorted(tally.items()))
    return ("\n".join(lines)
            + f"\n\nToday: {header or 'nothing yet'}"
            + f"\nFull log: {log_path(logs_folder)}")
