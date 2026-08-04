"""
Post-upload disk cleanup.

Uploading one stream leaves several files behind, in wildly different size
classes. Worth knowing which is which before deciding what to delete:

  censored copy      ~= the size of the source video (a full re-encode).
                        This is the one that actually costs gigabytes.
  source video       ~= the same again, sitting in uploaded/ forever.
  transcript cache   a few hundred KB of JSON.
  Rumble page dumps  a few MB each, written only when a run fails.
  logs               kilobytes. Not the reason you're out of space.

Everything here only ever touches files this pipeline created and named -
never arbitrary contents of a folder.
"""

import glob
import os
import time
from typing import Optional

from utils.censor import transcript_cache_path

# What `source_video` may be set to.
SOURCE_MOVE = "move"      # default: keep it, in uploaded/
SOURCE_DELETE = "delete"  # opt-in: free the space, original is gone
SOURCE_KEEP = "keep"      # leave it exactly where it was


def _size_mb(path: str) -> float:
    try:
        return os.path.getsize(path) / (1024 ** 2)
    except OSError:
        return 0.0


def _remove(path: Optional[str]) -> float:
    """Delete `path`, returning the MB freed (0 if it wasn't there)."""
    if not path or not os.path.exists(path):
        return 0.0
    freed = _size_mb(path)
    try:
        os.remove(path)
        return freed
    except OSError:
        return 0.0


def trim_log(path: str, max_mb: float) -> float:
    """Keep only the last `max_mb` of a log file, cut at a line boundary.

    Trimmed rather than deleted on purpose: the logs are how a failed
    upload gets diagnosed, and they're kilobytes - deleting them saves
    nothing and costs the audit trail.
    """
    if max_mb <= 0 or not os.path.isfile(path):
        return 0.0
    before = _size_mb(path)
    if before <= max_mb:
        return 0.0
    keep = int(max_mb * 1024 * 1024)
    try:
        with open(path, "rb") as f:
            f.seek(-keep, os.SEEK_END)
            tail = f.read()
        # Drop the partial first line so the file stays parseable.
        newline = tail.find(b"\n")
        if newline != -1:
            tail = tail[newline + 1:]
        with open(path, "wb") as f:
            f.write(b"[log trimmed to the most recent entries]\n")
            f.write(tail)
    except OSError:
        return 0.0
    return before - _size_mb(path)


def cleanup_after_upload(
    cfg,
    video_path: str,
    censored_path: Optional[str] = None,
    fully_uploaded: bool = True,
) -> float:
    """Free the working files for one processed video. Returns MB freed.

    `censored_path` is the censored re-encode, if one was made.
    `fully_uploaded` gates the destructive options: a partial upload will
    be retried, so its inputs must survive.
    """
    settings = getattr(cfg.general, "cleanup", None) or {}
    if not settings.get("enabled", True):
        return 0.0

    freed = 0.0
    basename = os.path.splitext(os.path.basename(video_path))[0]

    # 1. The censored re-encode - the big one, and always regenerable.
    if settings.get("censored_copy", True) and censored_path != video_path:
        freed += _remove(censored_path)

    # 2. Whisper's cached transcript. Only after the optimizer has read it.
    if settings.get("transcript_cache", True):
        freed += _remove(transcript_cache_path(cfg.general.censored_folder, basename))

    # 3. Leftover extracted audio from the censor pass.
    for wav in glob.glob(os.path.join(cfg.general.censored_folder, f"*{basename}*.wav")):
        freed += _remove(wav)

    # 4. Rumble debug dumps - only useful while diagnosing a failure.
    if settings.get("page_dumps", True) and fully_uploaded:
        for dump in glob.glob(os.path.join(cfg.general.logs_folder,
                                           "rumble_page_dump_*.html")):
            freed += _remove(dump)

    # 5. Trim, don't delete, the logs.
    max_mb = float(settings.get("trim_logs_mb", 5) or 0)
    for name in ("youtube.log", "rumble.log"):
        freed += trim_log(os.path.join(cfg.general.logs_folder, name), max_mb)

    return freed


def resolve_source_action(cfg) -> str:
    """How to treat the source video once every platform has it."""
    settings = getattr(cfg.general, "cleanup", None) or {}
    action = str(settings.get("source_video", SOURCE_MOVE)).strip().lower()
    return action if action in (SOURCE_MOVE, SOURCE_DELETE, SOURCE_KEEP) else SOURCE_MOVE


def prune_uploaded_folder(cfg, keep_newest: int = 0) -> float:
    """Delete all but the `keep_newest` most recent videos in uploaded/.

    Off unless `cleanup.keep_uploaded_videos` is set. The folder is where
    every source video lands after a successful upload, so on a machine
    that processes streams regularly it grows by the size of a full VOD
    every time and is usually the real reason the disk filled up.
    """
    if keep_newest is None or keep_newest < 0:
        return 0.0
    folder = cfg.general.uploaded_folder
    if not os.path.isdir(folder):
        return 0.0

    videos = [
        os.path.join(folder, name) for name in os.listdir(folder)
        if os.path.splitext(name)[1].lower() in cfg.general.supported_formats
    ]
    videos = [v for v in videos if os.path.isfile(v)]
    videos.sort(key=lambda p: os.path.getmtime(p), reverse=True)

    freed = 0.0
    for path in videos[keep_newest:]:
        freed += _remove(path)
    return freed
