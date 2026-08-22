"""What people went back and watched again.

Chat is the audience reacting. The most-replayed heatmap is the audience
returning - going back, on purpose, to watch a stretch a second time.
Rewatching costs effort in a way that typing does not, and YouTube
publishes it per video.

This is the strongest audience signal available here, and it needs
nothing new from the recorder: every VOD from this channel is already on
YouTube, so the heatmap for it already exists.

Shaped exactly like chat_energy on purpose - a value per time window, a
"how far over normal is this stretch" reading, and a bounded multiplier -
so it composes with what is already there instead of being a second way
of doing the same thing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Sequence

# One value per second, to match chat_energy.WINDOW_SECONDS. YouTube's
# own buckets are coarser than this and get spread across it.
WINDOW_SECONDS = 1.0

_TIMEOUT = 120


def yt_dlp_command() -> list:
    """How to invoke yt-dlp - the same reasoning as record_stream."""
    try:
        import yt_dlp  # noqa: F401
    except Exception:
        return ["yt-dlp"]
    return [sys.executable, "-m", "yt_dlp"]


def dump_args(url: str) -> list:
    """Metadata only. No video is downloaded."""
    return yt_dlp_command() + [
        "--skip-download", "--no-warnings", "--dump-single-json", url]


def _spread(markers: Sequence[dict], window: float = WINDOW_SECONDS) -> list:
    """YouTube's buckets, laid out one value per window.

    Each marker is {start_time, end_time, value} with value roughly 0..1.
    Held flat across the marker's span rather than interpolated: the
    marker IS the resolution YouTube measured at, and inventing a curve
    between two of them would be inventing data.
    """
    usable = []
    for marker in markers or ():
        if not isinstance(marker, dict):
            continue
        try:
            start = float(marker["start_time"])
            end = float(marker["end_time"])
            value = float(marker["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if end > start and value >= 0:
            usable.append((start, end, value))
    if not usable:
        return []

    span = max(end for _s, end, _v in usable)
    out = [0.0] * (int(span // window) + 1)
    for start, end, value in usable:
        first = max(0, int(start // window))
        last = min(len(out), int(end // window) + 1)
        for index in range(first, last):
            out[index] = max(out[index], value)
    return out


def heat_for_url(url: str, runner=None) -> list:
    """[replay value per second] for this video, or [] when there is none.

    Empty is the normal answer for a video too new or too small to have
    one - YouTube only publishes a heatmap once enough people have
    watched. The caller carries on with the other signals.
    """
    if not url:
        return []
    runner = runner or subprocess.run
    try:
        done = runner(dump_args(url), capture_output=True, text=True,
                      timeout=_TIMEOUT)
    except Exception:
        return []
    if getattr(done, "returncode", 1) != 0:
        return []
    try:
        data = json.loads(getattr(done, "stdout", "") or "")
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    return _spread(data.get("heatmap") or [])


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2


def heat_over(values: Sequence[float], start: float, end: float) -> float:
    """How many times the usual replay level the busiest second here hits.

    Against the video's own median, for the same reason chat is: a
    heatmap is normalised per video, so "high" only means anything
    relative to the rest of THIS video.
    """
    if not values or end <= start:
        return 0.0
    active = [v for v in values if v > 0]
    if not active:
        return 0.0
    baseline = max(1e-6, _median(active))

    first = max(0, int(start // WINDOW_SECONDS))
    last = min(len(values), int(end // WINDOW_SECONDS) + 1)
    inside = values[first:last]
    if not inside:
        return 0.0
    return max(inside) / baseline


def heat_bonus(values: Sequence[float], start: float, end: float,
               cap: float = 0.5) -> float:
    """A multiplier for a window's score, 1.0 when there is no heatmap.

    The same cap as chat. Rewatching is a stronger signal than typing,
    but this is a heatmap for the WHOLE video including its intro and its
    best-known moments, and letting it dominate would just re-cut
    whatever was already popular. It adjusts; it does not decide.
    """
    over = heat_over(values, start, end)
    if over <= 1.0:
        return 1.0
    return 1.0 + min(cap, (over - 1.0) * 0.25)
