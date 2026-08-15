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

WHEN A FILE IS FINISHED
-----------------------
By its SIZE holding still, not by its modification time. Copying a file
preserves the original's mtime, so a VOD copied in from another drive
looks hours old the instant it appears and would be transcribed while
the copy was still running. Two observations of the same size, far
enough apart, is the thing that actually means "nobody is writing to
this".

WHEN A RUN FAILS
----------------
Producing no clips is an answer: that VOD had nothing clip-worthy in it,
and asking again would transcribe it from scratch every pass forever.
FAILING is not an answer. The last real run hit an HTTP 503 and a
timeout, and under a rule that treats those the same as "no clips" both
VODs would have been skipped permanently. A failed run is retried, up to
MAX_ATTEMPTS, and only then set aside.
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

ARCHIVE_NAME = ".clipped.json"

# How many times a VOD that FAILED is allowed to come round again. Three
# is enough to ride out a busy model or a dropped connection, and small
# enough that a genuinely broken file stops costing a transcription pass
# every five minutes.
MAX_ATTEMPTS = 3


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


def remember(folder: str, path: str, clips: int, failed: bool = False,
             attempts: int = 0) -> None:
    """Record what happened to this VOD.

    A run that produced NO clips is still done: that VOD had nothing
    clip-worthy in it, and asking again would transcribe it from scratch
    on every pass forever.

    A run that FAILED is a different thing, and `failed=True` keeps it
    eligible until MAX_ATTEMPTS. An HTTP 503 from the model is not a
    verdict about the video.
    """
    data = load_archive(folder)
    data[_key(path)] = {"name": os.path.basename(path),
                        "clips": int(clips), "when": time.time(),
                        "failed": bool(failed),
                        "attempts": int(attempts)}
    try:
        os.makedirs(folder, exist_ok=True)
        temporary = archive_path(folder) + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=1)
        os.replace(temporary, archive_path(folder))
    except OSError:
        pass


def was_clipped(archive_folder: str, path: str) -> bool:
    """Has this video already been through the clipper?

    `archive_folder` is only where the record LIVES - the key is the
    video's own name and size, so a VOD that has since moved from
    watch_folder to uploaded/ is still recognised.
    """
    return is_done(load_archive(archive_folder).get(_key(path)))


def attempts_for(folder: str, path: str) -> int:
    entry = load_archive(folder).get(_key(path)) or {}
    return int(entry.get("attempts", 0) or 0)


def is_done(entry: dict) -> bool:
    """True when this VOD needs no further passes."""
    if not entry:
        return False
    if not entry.get("failed"):
        return True
    return int(entry.get("attempts", 0) or 0) >= MAX_ATTEMPTS


def is_settled(path: str, quiet_seconds: float = 90.0,
               now: Optional[float] = None, seen: Optional[dict] = None) -> bool:
    """True when the file has stopped growing.

    Measured on SIZE across two observations, not on modification time.
    A copy preserves the original's mtime, so a VOD copied in from
    another drive reads as hours old the moment it appears - and
    transcribing half a VOD wastes the expensive pass and produces clips
    from a video that will not exist in that form.

    `seen` carries the previous observation between calls: {path: (size,
    when)}. Without it this can only answer for a file that is already
    old by mtime too, which is the conservative direction.
    """
    now = time.time() if now is None else now
    try:
        size = os.path.getsize(path)
        modified = os.path.getmtime(path)
    except OSError:
        return False

    if seen is None:
        return (now - modified) >= quiet_seconds

    previous = seen.get(path)
    seen[path] = (size, now)
    if previous is None:
        # First sighting. An untouched file that is also old by mtime is
        # safe to take now; anything else waits for a second look.
        return (now - modified) >= quiet_seconds
    last_size, first_seen = previous
    if size != last_size:
        seen[path] = (size, now)      # still growing - restart the clock
        return False
    return (now - first_seen) >= quiet_seconds


def pending(folder: str, formats, quiet_seconds: float = 90.0,
            now: Optional[float] = None, seen: Optional[dict] = None) -> list:
    """VODs in `folder` still wanting a clip run, oldest first."""
    if not folder or not os.path.isdir(folder):
        return []
    archive = load_archive(folder)
    found = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        if os.path.splitext(name)[1].lower() not in tuple(formats or ()):
            continue
        if is_done(archive.get(_key(path))):
            continue
        if not is_settled(path, quiet_seconds, now, seen):
            continue
        found.append(path)
    return found


def next_vod(folder: str, formats, quiet_seconds: float = 90.0,
             seen: Optional[dict] = None) -> str:
    """The one VOD to clip on this pass, or "" for nothing to do."""
    waiting = pending(folder, formats, quiet_seconds, seen=seen)
    return waiting[0] if waiting else ""
