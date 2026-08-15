"""
Where the action is, second by second, so a gameplay crop can follow it.

WHAT THIS IS FOR
----------------
A centre crop on GTA keeps the crosshair and the HUD and misses the
thing that made the clip: a fight, a car, a person walking up on the
left. The ask was a crop that sits centred on the action and "kinda
moves slightly" when something happens elsewhere - not a camera that
chases, a camera that drifts.

WHY MOTION AND NOT FACES
------------------------
Face detection is exactly wrong here. GTA is full of NPC faces, none of
which are the point, and a detector locks onto whichever one is nearest
the lens. Frame-to-frame CHANGE has no such opinion: a car chase, a
punch and a muzzle flash all register, and a man standing still does
not. That is also why face tracking stays off for gameplay everywhere
else in this project.

HOW IT STAYS WATCHABLE
----------------------
Three limits, and they matter more than the detection does:

  * a DEADZONE, so a crop that is roughly right does not twitch,
  * a SPEED CAP in fractions of frame width per second, so the crop
    drifts rather than snaps,
  * heavy smoothing before either, so one explosion does not yank it.

Without these, following motion produces something genuinely unpleasant
to watch - the crop jumps every time a car passes, and a viewer feels
it even when they cannot say what is wrong.

FAILURE IS CENTRED
------------------
No numpy, no ffmpeg, a static scene, a scene lit by strobing lights -
all return nothing, and the caller uses a plain centre crop, which is
what it would have done anyway.
"""

from __future__ import annotations

import os
import subprocess
from typing import Optional, Sequence

# Frames per second sampled for the motion read. Four is enough to see a
# fight start and cheap enough to run on every clip; the crop is
# smoothed far below this rate anyway.
SAMPLE_FPS = 4.0

# Sampling resolution. Motion at 160x90 is the same motion as at 1080p
# for this purpose, and it is 70x less data to move.
SAMPLE_WIDTH = 160
SAMPLE_HEIGHT = 90

# Below this, the frame did not change enough to mean anything - a menu,
# a still shot, someone standing in a garage - and the crop holds.
MIN_ACTIVITY = 2.0

# How far the target must be from where the crop is before it moves at
# all, as a fraction of frame width. Under this it holds still.
DEADZONE = 0.045

# Fractions of frame width per second. This is what makes it a drift
# instead of a chase; at 0.08 a crop takes about four seconds to cross a
# third of the screen.
MAX_PAN_PER_S = 0.08

# Weight of each new reading against the running average. Low, because
# the input is noisy and the eye notices jitter long before it notices
# lag.
SMOOTHING = 0.15

_TIMEOUT = 180


def have_numpy() -> bool:
    try:
        import numpy  # noqa: F401

        return True
    except Exception:
        return False


def sample_args(source: str, start: float, duration: float) -> list:
    """ffmpeg piping small grayscale frames to stdout.

    Raw gray rather than PNG: no encoder, no temp files, no decode on
    this side - just bytes that reshape into an array.
    """
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.2f}", "-t", f"{duration:.2f}", "-i", source,
        "-vf", f"fps={SAMPLE_FPS},scale={SAMPLE_WIDTH}:{SAMPLE_HEIGHT}",
        "-pix_fmt", "gray", "-f", "rawvideo", "-",
    ]


def activity_centers(frames) -> list:
    """(index, x_fraction, activity) per frame pair, from raw gray frames.

    The x is the centre of mass of everything that CHANGED between two
    consecutive frames, which is where the action is. Frames whose total
    change is below the floor are reported with activity 0 so the caller
    can hold position through them.
    """
    import numpy as np

    readings = []
    for index in range(1, len(frames)):
        difference = np.abs(frames[index].astype(np.int16)
                            - frames[index - 1].astype(np.int16))
        columns = difference.sum(axis=0).astype(np.float64)
        total = float(columns.sum())
        activity = total / (SAMPLE_WIDTH * SAMPLE_HEIGHT)
        if activity < MIN_ACTIVITY or total <= 0:
            readings.append((index, 0.5, 0.0))
            continue
        positions = np.arange(len(columns), dtype=np.float64)
        centre = float((columns * positions).sum() / total)
        readings.append((index, centre / max(1, len(columns) - 1), activity))
    return readings


def _smooth(readings: Sequence, start_at: float = 0.5) -> list:
    """A running average that ignores frames with nothing happening."""
    smoothed = []
    current = start_at
    for index, x, activity in readings:
        if activity > 0:
            current = current * (1 - SMOOTHING) + x * SMOOTHING
        smoothed.append((index, current))
    return smoothed


def pan_path(readings: Sequence, fps: float = SAMPLE_FPS) -> list:
    """[(seconds, x_fraction)] the crop CENTRE should travel through.

    Deadzone then speed cap, in that order: the deadzone decides whether
    to move at all and the cap decides how fast, so a crop that is
    roughly right stays perfectly still instead of creeping.
    """
    if not readings:
        return []

    per_frame = MAX_PAN_PER_S / max(0.001, fps)
    path = []
    position = 0.5
    for index, target in _smooth(readings):
        gap = target - position
        if abs(gap) > DEADZONE:
            # Move toward the target, but never faster than the cap.
            step = max(-per_frame, min(per_frame, gap))
            position += step
        path.append((index / fps, round(max(0.0, min(1.0, position)), 4)))
    return path


def read_frames(source: str, start: float, duration: float) -> list:
    """Sampled grayscale frames, or [] if they cannot be read."""
    import numpy as np

    try:
        done = subprocess.run(sample_args(source, start, duration),
                              stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if done.returncode != 0 or not done.stdout:
        return []

    size = SAMPLE_WIDTH * SAMPLE_HEIGHT
    count = len(done.stdout) // size
    if count < 2:
        return []
    flat = np.frombuffer(done.stdout[:count * size], dtype=np.uint8)
    return list(flat.reshape(count, SAMPLE_HEIGHT, SAMPLE_WIDTH))


def path_for(source: str, start: float, duration: float) -> list:
    """The pan path for one clip, or [] to mean "just centre it"."""
    if not have_numpy():
        return []
    frames = read_frames(source, start, duration)
    if not frames:
        return []
    return pan_path(activity_centers(frames))


def is_worth_moving(path: Sequence) -> bool:
    """False when the path never really leaves the middle.

    A path that sits at 0.5 the whole way is a centre crop written the
    expensive way, and rendering it through sendcmd adds a filter, a
    temp file and a way to fail for no visible difference.
    """
    if len(path) < 2:
        return False
    positions = [x for _, x in path]
    return (max(positions) - min(positions)) > DEADZONE


def commands_file(path: Sequence, crop_width_expr: str = "",
                  crop_height_expr: str = "") -> str:
    """The sendcmd script that walks the crop across the frame.

    ffmpeg's crop filter accepts `x` as a runtime command, so the pan is
    a list of timestamped values rather than one enormous expression
    with a hundred nested if()s in it - which is the other way to do
    this, and is unreadable and slow to parse.

    Values are fractions of input width, written as an expression so the
    same file is correct at 1080p and 4K.
    """
    lines = []
    for entry in path:
        # A gameplay path is (t, x): a 16:9 source cut to 9:16 has no
        # vertical slack to pan into. A face path inside the call pane
        # is (t, x, y) - that pane is 0.90 of the frame tall, so there
        # is real room to move, and a face that stands up leaves a crop
        # that only tracks sideways.
        seconds, centre = entry[0], entry[1]
        vertical = entry[2] if len(entry) > 2 else None
        # x is the LEFT edge; centre minus half the crop width, clamped
        # inside the frame by the expression itself so a value near an
        # edge cannot produce an invalid crop.
        left = f"clip(iw*{centre:.4f}-{crop_width_expr}/2,0,iw-{crop_width_expr})"
        lines.append(f"{seconds:.2f} crop x '{left}';")
        if vertical is not None and crop_height_expr:
            top = (f"clip(ih*{vertical:.4f}-{crop_height_expr}/2,0,"
                   f"ih-{crop_height_expr})")
            lines.append(f"{seconds:.2f} crop y '{top}';")
    return "\n".join(lines) + ("\n" if lines else "")
