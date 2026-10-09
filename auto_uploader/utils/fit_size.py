"""
utils/fit_size.py - a clip small enough for where it is going.

The clips are cut at high quality, and a long one passes 100 MB. Two
places refuse that: Cloudinary's free plan (the hand-off X and TikTok go
through - "is 102 MB; Cloudinary's free plan takes up to 100 MB") and
Facebook's Reel upload (HTTP 413). Both lost real clips on 2026-10-08.

So a clip over the limit is re-encoded once, to a bitrate worked out from
its length so the result lands under the limit. The picture size and
frame rate stay; only the bitrate drops, which at phone size is not
something anyone sees.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Tuple

# Under every limit above, with room for the container.
SOCIAL_MAX_BYTES = 95 * 1024 * 1024
AUDIO_KBPS = 128
_TIMEOUT = 20 * 60


def _duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True, timeout=60).stdout.strip()
    return float(out)


def target_video_kbps(max_bytes: int, seconds: float,
                      safety: float = 0.92) -> int:
    """The video bitrate that brings `seconds` of clip under `max_bytes`."""
    total_kbps = max_bytes * 8 / 1000 / max(1.0, seconds) * safety
    return max(600, int(total_kbps - AUDIO_KBPS))


def fit(path: str, max_bytes: int = SOCIAL_MAX_BYTES) -> Tuple[str, str]:
    """(path to upload, temporary file to delete or "").

    The original when it already fits or anything goes wrong - a clip that
    is too big fails where it always did; it is never made to fail here.
    """
    try:
        if os.path.getsize(path) <= max_bytes:
            return path, ""
        seconds = _duration(path)
    except (OSError, ValueError, subprocess.SubprocessError):
        return path, ""
    folder = tempfile.mkdtemp(prefix="fit_")
    out = os.path.join(folder, os.path.basename(path))
    safety = 0.92
    # The GPU's encoder when it really works (an RTX here: several times
    # faster than libx264 on the CPU, and the CPU stays free for the rest
    # of the pipeline); libx264 if it does not, or if it fails on this
    # file.
    try:
        from utils.ffmpeg_tools import nvenc_works

        encoders = ["h264_nvenc", "libx264"] if nvenc_works() else ["libx264"]
    except Exception:
        encoders = ["libx264"]
    for _attempt in range(2):
        kbps = target_video_kbps(max_bytes, seconds, safety)
        # GPU first; the CPU encoder if the GPU failed OR overshot (its
        # rate control is looser on very short clips).
        for encoder in encoders:
            codec = (["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr"]
                     if encoder == "h264_nvenc"
                     else ["-c:v", "libx264", "-preset", "veryfast"])
            args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", path, "-map", "0:v:0", "-map", "0:a:0?", *codec,
                    "-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.25)}k",
                    "-bufsize", f"{kbps * 2}k", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k", "-ar", "48000",
                    "-movflags", "+faststart", out]
            try:
                done = subprocess.run(args, capture_output=True, text=True,
                                      timeout=_TIMEOUT)
            except (OSError, subprocess.SubprocessError):
                continue
            if done.returncode == 0 and os.path.isfile(out) \
                    and os.path.getsize(out) <= max_bytes:
                print(f"[Clips] {os.path.basename(path)} was "
                      f"{os.path.getsize(path) >> 20} MB - re-encoded to "
                      f"{os.path.getsize(out) >> 20} MB to fit ({encoder}).")
                return out, out
        safety *= 0.85
    try:
        if os.path.isfile(out):
            os.remove(out)
        os.rmdir(folder)
    except OSError:
        pass
    return path, ""
