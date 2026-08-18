"""
Telling Monkey App content from GTA gameplay by looking at it.

WHY
---
The framing is chosen from the stream TITLE, and a title is often silent
about which kind it is. "yoo_howl" and "culture" are both real recordings
here; neither says "monkey" or "gta". A silent title falls back to
`whole`, which wastes most of a vertical frame on a blurred background
when a real rectangle existed.

WHAT SEPARATES THEM
-------------------
Not colour, not faces - motion.

  GTA is a camera moving through a 3D world. When it pans, nearly every
  pixel changes at once.

  Monkey App content is a desktop: a call pane where one person moves a
  little, a slots page that is still between spins, and a taskbar that
  never moves at all. Most of the frame is identical from second to
  second.

So: measure what FRACTION of the frame changes between samples, and
check whether the bottom strip - where a taskbar lives - ever moves.

WHEN IT REFUSES
---------------
`kind_for_frames` returns "" whenever the two signals disagree or land
in the middle, and "" means the caller keeps its existing fallback. A
wrong crop is unrecoverable once the clip is posted, while an
un-cropped clip is merely plain, so being unsure has to cost nothing.
That is also why nothing here ever returns "gta" off a single signal.
"""

from __future__ import annotations

from typing import Optional

# A pixel has "changed" past this much difference in 0-255 grey. Below
# it is video noise and compression, which is present in every frame of
# every recording and means nothing.
PIXEL_CHANGE = 14

# Above this fraction changing, the picture is not a desktop holding
# still. Measured rather than guessed, because the first value here was
# 0.45 and would never once have fired:
#
#   a still desktop with one live pane      0.005
#   a zooming mandelbrot (dense detail)     0.187
#   a panning camera over flat colour       0.298
#   chaotic full-frame motion               0.411
#
# So the honest reading is that the moving fraction cannot pick gameplay
# out of a line-up - everything that is not a desktop sits in a wide
# band. What it CAN do is confirm the frame is alive. The signal that
# actually separates the two is the taskbar below.
GAMEPLAY_MOVING = 0.15

# Below this, most of the screen is holding still - windows, a page
# between spins, a taskbar. No 3D camera produces this for long.
DESKTOP_MOVING = 0.18

# The bottom of the frame, where a Windows taskbar sits.
TASKBAR_STRIP = 0.06

# A taskbar is not merely quiet, it is inert. This is deliberately near
# zero: the test is "did anything at all happen down there".
#
# This is the load-bearing signal. Across every sample measured, a
# desktop capture read 0.00 here and everything else read above 0.10 -
# a gap far wider and far cleaner than anything the overall motion
# offers.
TASKBAR_MOVING = 0.02


def _changed_fraction(before, after, threshold: int = PIXEL_CHANGE) -> float:
    """Fraction of pixels that differ by more than `threshold`."""
    import numpy as np

    diff = np.abs(before.astype(np.int16) - after.astype(np.int16))
    return float((diff > threshold).mean())


def moving_fraction(frames) -> Optional[float]:
    """How much of the frame changes between samples, averaged.

    None when there is nothing to compare - one frame is a photograph
    and says nothing about motion.
    """
    if frames is None or len(frames) < 2:
        return None
    scores = [_changed_fraction(frames[i], frames[i + 1])
              for i in range(len(frames) - 1)]
    return sum(scores) / len(scores) if scores else None


def taskbar_fraction(frames) -> Optional[float]:
    """The same measure, over the bottom strip only."""
    if frames is None or len(frames) < 2:
        return None
    height = frames[0].shape[0]
    start = max(0, height - max(1, int(round(height * TASKBAR_STRIP))))
    strips = [frame[start:, :] for frame in frames]
    scores = [_changed_fraction(strips[i], strips[i + 1])
              for i in range(len(strips) - 1)]
    return sum(scores) / len(scores) if scores else None


def kind_for_frames(frames) -> str:
    """"monkey", "gta", or "" for not sure.

    The question actually being answered is "is this a desktop capture",
    because that is the one thing the pixels say clearly. Monkey App
    content IS a desktop; GTA is not. Trying to recognise gameplay
    directly does not work - see GAMEPLAY_MOVING.

    Both signals must agree in both directions. A game can hold still -
    a menu, a loading screen, standing in a lobby - so stillness alone
    cannot mean desktop; the inert taskbar has to be there too. And a
    frozen strip under a moving picture is contradictory, so that says
    nothing rather than guessing.
    """
    overall = moving_fraction(frames)
    bottom = taskbar_fraction(frames)
    if overall is None or bottom is None:
        return ""

    pinned = bottom <= TASKBAR_MOVING
    if pinned and overall <= DESKTOP_MOVING:
        return "monkey"
    if not pinned and overall >= GAMEPLAY_MOVING:
        return "gta"
    return ""


def kind_for_video(source: str, start: float = 0.0,
                   duration: float = 6.0) -> str:
    """Look at a few seconds of the file and say which kind it is.

    Returns "" on anything unexpected - no numpy, no ffmpeg, an
    unreadable file. This is an improvement on a guess, never a
    requirement.
    """
    try:
        from autoreel.motion_region import have_numpy, read_frames
    except ImportError:
        return ""
    if not have_numpy():
        return ""
    try:
        frames = read_frames(source, start, duration)
    except Exception:
        return ""
    return kind_for_frames(frames)


def _seconds_long(source: str) -> float:
    """The video's duration, or 0.0 when it cannot be read."""
    import subprocess

    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", source],
            capture_output=True, text=True, timeout=30)
        return max(0.0, float((out.stdout or "0").strip()))
    except Exception:
        return 0.0


def sample_points(span: float, samples: int, spacing: float) -> list:
    """Where to look, in seconds.

    Spread across the WHOLE video when its length is known. Fixed
    offsets of 0s / 300s / 600s only ever saw the first ten minutes, so a
    112-minute stream that opened on a Monkey call and then played GTA
    for a hundred minutes was filed as `monkey` and every clip in it got
    a face-tracking crop - a narrow strip of gameplay with the game
    itself cut off either side.

    A stream is not one thing for two hours. Looking only at the start
    cannot tell what it mostly was.

    The offsets avoid the very beginning and the very end, which are the
    two parts least likely to be representative: streams open on a
    waiting screen and close on an outro.
    """
    if span <= 0:
        return [index * spacing for index in range(max(1, samples))]
    samples = max(1, samples)
    step = span / (samples + 1)
    return [step * (index + 1) for index in range(samples)]


def profile_for(source: str, title: str = "", fallback: str = "whole",
                samples: int = 5, spacing: float = 300.0) -> str:
    """The framing for this video: the title first, then the picture.

    The title wins when it says anything, because the streamer naming
    their own stream is better evidence than six seconds of pixels.

    Several samples spread through the WHOLE file, not one and not the
    first ten minutes: a Monkey stream opens on a waiting screen and a
    GTA stream has menus, and either would answer for the whole video
    from one look. The samples vote, and a tie is "not sure".
    """
    from autoreel.crop_strategy import profile_for_title

    named = profile_for_title(title, fallback="")
    if named:
        return named

    votes: dict = {}
    for at in sample_points(_seconds_long(source), samples, spacing):
        kind = kind_for_video(source, start=at)
        if kind:
            votes[kind] = votes.get(kind, 0) + 1
    if not votes:
        return fallback
    best = max(votes.values())
    winners = [k for k, v in votes.items() if v == best]
    return winners[0] if len(winners) == 1 else fallback
