"""
Stills from a candidate window, small enough to send to a model.

WHY THIS EXISTS
---------------
Everything that picks clips here reads WORDS, and on this channel the
words are the wrong input. A Monkey-app clip lands on a face reaction and
the transcript says "...what". Someone gets run over in GTA and the
transcript says nothing at all - there is no line to score, so the moment
never becomes a candidate's strength. A better language model reading the
same blind transcript still cannot see the joke. Frames are the missing
input, not a bigger model.

WHY IT IS CHEAP
---------------
Small JPEGs. A model reads an image at a fixed token cost regardless of
how large it was sent, so a 1080p still costs the same as a 512px one and
takes twenty times longer to upload. Two frames per candidate, 512 wide,
quality 60 - a whole batch is a couple of megabytes.

WHERE IN THE WINDOW
-------------------
Not the first frame. A clip's opening is usually the tail of whatever
came before, and the payoff sits about two thirds through - the same
place PEAK_POSITION puts it in the scorer. One frame early to establish
what is on screen, one late to catch the reaction.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile
from typing import Sequence

# Two per candidate: one to establish the scene, one for the payoff. A
# third adds cost and rarely changes the answer.
FRAMES_PER_CANDIDATE = 2

# Fractions through the window to sample at. Never 0.0 - a clip's first
# frame belongs to whatever came before it.
SAMPLE_POINTS = (0.25, 0.7)

# A model reads an image at a fixed token cost whatever its size, so
# anything past this is upload time bought for nothing.
FRAME_WIDTH = 512
JPEG_QUALITY = 60

_TIMEOUT = 60


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def still_args(source: str, at_seconds: float, out_path: str) -> list:
    """One JPEG at one timestamp, downscaled.

    -ss before -i seeks by keyframe rather than decoding up to the mark,
    which on a three-hour VOD is the difference between instant and
    minutes - and forty candidates means eighty of these.
    """
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, at_seconds):.2f}", "-i", source,
        "-frames:v", "1",
        "-vf", f"scale={FRAME_WIDTH}:-2",
        "-q:v", str(int(JPEG_QUALITY / 10)),
        out_path,
    ]


def sample_points(start: float, end: float,
                  points: Sequence = SAMPLE_POINTS) -> list:
    """Absolute timestamps to grab, for one window."""
    span = max(0.0, end - start)
    return [start + span * fraction for fraction in points]


def frames_for(source: str, start: float, end: float) -> list:
    """[jpeg_bytes] for one candidate window. [] if they cannot be read.

    An empty list is a normal answer - an unreadable stretch, no ffmpeg -
    and the caller falls back to judging that candidate on its words, as
    it did before this existed.
    """
    if not have_ffmpeg() or end <= start:
        return []

    workspace = tempfile.mkdtemp(prefix="frames_")
    try:
        grabbed = []
        for number, at in enumerate(sample_points(start, end)):
            path = os.path.join(workspace, f"still_{number}.jpg")
            try:
                subprocess.run(still_args(source, at, path), timeout=_TIMEOUT,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            except (OSError, subprocess.TimeoutExpired):
                continue
            try:
                with open(path, "rb") as handle:
                    data = handle.read()
            except OSError:
                continue
            if data:
                grabbed.append(data)
        return grabbed
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def as_inline_data(jpeg: bytes) -> dict:
    """One image as Gemini's inline_data part."""
    return {"inline_data": {"mime_type": "image/jpeg",
                            "data": base64.b64encode(jpeg).decode("ascii")}}
