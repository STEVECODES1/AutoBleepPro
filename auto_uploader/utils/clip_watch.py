"""
VODs sitting in a folder become clips on their own.

WHY
---
`--clips-from FOLDER` already cuts clips from a library of VODs, but it
has to be remembered and run. A VOD that lands in downloaded_vods at 4am
sits there until someone types a command.

WHAT THIS IS NOT
----------------
It is not a second watcher. The folder is a LIBRARY: nothing in it is
moved, renamed or deleted, which is the promise --clips-from already
makes and the reason it is safe to point at a drive full of recordings.
Instead of watching for filesystem events, this asks a plain question on
a timer - "which of these has not been clipped yet" - and answers it from
a small archive file beside the folder.

ONE AT A TIME
-------------
Cutting clips means transcribing the whole VOD, which took 663 seconds
for a 116-minute file on a 4060. The watch loop it runs inside is also
what posts the queue and uploads finished videos, so this hands back one
VOD per pass. Ten new VODs become ten passes, not one twenty-minute
freeze during which nothing else posts.

IDENTITY WITHOUT HASHING
------------------------
A VOD is remembered by name and byte size. Hashing would be surer and
costs minutes per file on an external drive, which is the whole reason
the uploader's own dedup avoids it where it can. Name-and-size is wrong
only if a file is replaced by a different one of exactly the same length
under exactly the same name, and the cost of being wrong is one
duplicate clip run - not a lost video.
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

ARCHIVE_NAME = ".clipped.json"


def _key(path: str) -> str:
    try:
        return f"{os.path.basename(path).lower()}:{os.path.getsize(path)}"
    except OSError:
        return os.path.basename(path).lower()


def archive_path(folder: str) -> str:
    return os.path.join(folder, ARCHIVE_NAME)


def load_archive(folder: str) -> dict:
    """What has been clipped already. An unreadable archive reads as
    empty, which risks re-cutting rather than skipping forever."""
    try:
        with open(archive_path(folder), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def remember(folder: str, path: str, clips: int) -> None:
    """Record that this VOD has been through the clipper.

    Written even when the run produced NO clips. A VOD with nothing
    clip-worthy in it would otherwise be transcribed again on every
    single pass, forever, and transcription is the expensive part.
    """
    data = load_archive(folder)
    data[_key(path)] = {"name": os.path.basename(path),
                        "clips": int(clips), "when": time.time()}
    try:
        os.makedirs(folder, exist_ok=True)
        temporary = archive_path(folder) + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=1)
        os.replace(temporary, archive_path(folder))
    except OSError:
        pass


def is_settled(path: str, quiet_seconds: float = 90.0,
               now: Optional[float] = None) -> bool:
    """True when the file has stopped being written to.

    A download in progress is a real file of the wrong length, and
    transcribing half a VOD wastes the expensive pass and produces clips
    from a video that no longer exists in that form.
    """
    now = time.time() if now is None else now
    try:
        return (now - os.path.getmtime(path)) >= quiet_seconds
    except OSError:
        return False


def pending(folder: str, formats, quiet_seconds: float = 90.0,
            now: Optional[float] = None) -> list:
    """VODs in `folder` that have not been clipped yet, oldest first."""
    if not folder or not os.path.isdir(folder):
        return []
    done = load_archive(folder)
    found = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        if os.path.splitext(name)[1].lower() not in tuple(formats or ()):
            continue
        if _key(path) in done:
            continue
        if not is_settled(path, quiet_seconds, now):
            continue
        found.append(path)
    return found


def next_vod(folder: str, formats, quiet_seconds: float = 90.0) -> str:
    """The one VOD to clip on this pass, or "" for nothing to do."""
    waiting = pending(folder, formats, quiet_seconds)
    return waiting[0] if waiting else ""
