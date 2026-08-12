"""
See the crop before spending ten renders on it.

Framing is the one setting that cannot be reasoned about from a config
file. Where the call window sits depends on how the windows were arranged
that day, and the only way to know a rectangle is right is to look at it.
Waiting for a full clip run to find out costs half an hour and ten wrong
clips.

This pulls three stills out of the source - one near the start, one in the
middle, one near the end, so a layout that shifts mid-stream is visible -
and writes each one twice: the source frame with the rectangle drawn on
it, and the finished 9:16 result. Compare the pair and nudge the numbers.
"""

from __future__ import annotations

import os
import subprocess
from typing import Optional

from .clip_maker import (
    VERTICAL_HEIGHT,
    VERTICAL_WIDTH,
    crop_filter,
    have_ffmpeg,
)
from .crop_strategy import CROP_REGION, resolve_region

_TIMEOUT = 120

# Where in the video to sample, as fractions of its length. Not 0.0 and
# 1.0: a stream opens on a waiting screen and closes on a goodbye, and
# neither shows the layout the clips will actually use.
SAMPLE_POINTS = (0.2, 0.5, 0.8)

# The drawn rectangle. Thick and bright because it is being looked at on
# a scaled-down still.
_BOX_COLOUR = "red@0.9"
_BOX_THICKNESS = 6


def outline_filter(region: dict) -> str:
    """Return an ffmpeg drawbox filter string that outlines the crop rectangle."""
    return (
        f"drawbox=x=iw*{region['x']:.4f}:y=ih*{region['y']:.4f}:"
        f"w=iw*{region['width']:.4f}:h=ih*{region['height']:.4f}:"
        f"color={_BOX_COLOUR}:t={_BOX_THICKNESS}"
    )


def _duration(source: str) -> float:
    """Length in seconds, or 0. Best-effort: the samples fall back to
    fixed intervals when this cannot be answered."""
    try:
        from .utils import media_duration
        return float(media_duration(source) or 0.0)
    except Exception:
        pass
    try:
        done = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", source],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        return float(done.stdout.decode().strip() or 0)
    except Exception:
        return 0.0


def _still(source: str, at_seconds: float, output: str, chain: str) -> bool:
    """Extract one JPEG frame from *source* at *at_seconds*, applying *chain*.

    Returns True only when ffmpeg exits cleanly and the output file exists.
    Silent on errors — the caller decides what missing stills mean.
    """
    args = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{at_seconds:.3f}", "-i", source,
        "-vframes", "1", "-vf", chain, output,
    ]
    try:
        done = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0 and os.path.isfile(output)


def preview(
    source: str,
    output_dir: str,
    region: Optional[dict] = None,
    duration: Optional[float] = None,
    strategy: str = CROP_REGION,
) -> list:
    """Write before/after stills for every sample point.

    For each sample point two files are written:
      ``crop{n}_source.jpg``  — the original frame with the crop box drawn on it
      ``crop{n}_result.jpg``  — the cropped, 9:16-scaled frame

    Parameters
    ----------
    source:
        Path to the input video file.
    output_dir:
        Directory where the stills are written (created if absent).
    region:
        Crop region dict with keys ``x``, ``y``, ``width``, ``height`` as
        fractions of the frame.  Falls back to ``resolve_region({})`` when
        omitted.
    duration:
        Video length in seconds.  Probed automatically when omitted.
    strategy:
        Crop strategy name passed to ``crop_filter``; defaults to
        ``CROP_REGION``.

    Returns
    -------
    list
        Paths of every file that was successfully written (0–6 items).
    """
    if not have_ffmpeg():
        return []
    if not os.path.isfile(source):
        return []

    region = region or resolve_region({})

    if duration is None:
        duration = _duration(source)

    os.makedirs(output_dir, exist_ok=True)

    written: list[str] = []
    for index, fraction in enumerate(SAMPLE_POINTS, start=1):
        # Fall back to fixed intervals when duration is unknown.
        at = duration * fraction if duration else index * 30.0

        # --- source frame with box overlay ---
        marked = os.path.join(output_dir, f"crop{index}_source.jpg")
        if _still(source, at, marked, outline_filter(region) + ",scale=1280:-2"):
            written.append(marked)

        # --- finished 9:16 result ---
        result = os.path.join(output_dir, f"crop{index}_result.jpg")
        result_filter = (
            crop_filter(strategy, region)
            + f",scale={VERTICAL_WIDTH // 2}:{VERTICAL_HEIGHT // 2}"
        )
        if _still(source, at, result, result_filter):
            written.append(result)

    return written


def describe(region: dict) -> str:
    """Return the crop rectangle as a human-readable string.

    Example output::

        x 25% to 75% across, y 10% to 95% down

    Useful for printing alongside the preview images so the user knows
    exactly which numbers to adjust.
    """
    right = region["x"] + region["width"]
    bottom = region["y"] + region["height"]
    return (
        f"x {region['x']:.0%} to {right:.0%} across, "
        f"y {region['y']:.0%} to {bottom:.0%} down"
    )
