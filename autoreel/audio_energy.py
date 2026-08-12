"""
How loud the room got, second by second.

WHY THE TRANSCRIPT IS NOT ENOUGH
--------------------------------
Everything that chooses clips here reads WORDS. That catches an argument
and a story landing, and it is completely deaf to the thing a clipper
actually watches for: the moment the room explodes. Laughter arrives in a
transcript as "hahaha" if Whisper bothered, and as nothing at all if it
did not. A shout and a mumble are the same text. The single funniest
second of a stream can be silent on the page.

This measures the audio itself. One RMS reading per second across the
whole file, from ffmpeg, which is cheap - it decodes audio only, at
8 kHz mono, and a three-hour stream takes under a minute.

WHAT IT IS AND IS NOT
---------------------
It is a signal, not a verdict. Loud is not the same as good: a game's
explosion, a music sting and a genuine reaction all peak. So this never
selects a clip on its own - it adjusts the score of windows the words
already put forward, which means a moment has to be BOTH talked over and
loud to win on it. That ordering is deliberate; the other way round would
clip every gunshot.

The measure is loudness RELATIVE to the rest of the stream, not absolute
dB. Setups differ, mic gain differs, and what matters is "louder than
this streamer usually is", not a number.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Optional, Sequence

# One reading per second. Finer resolution measures syllables rather than
# moments, and a clip is fifteen seconds long.
WINDOW_SECONDS = 1.0

# Decode audio only, mono, low sample rate: none of this needs fidelity,
# and it is what keeps a three-hour file under a minute.
SAMPLE_RATE = 8000

_TIMEOUT = 60 * 20
_RMS_LINE = re.compile(r"lavfi\.astats\.Overall\.RMS_level=(-?\d+(?:\.\d+)?)")

# Silence reads as -inf or a very large negative; clamped so one silent
# second cannot drag an average to nothing.
FLOOR_DB = -90.0


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def measure_args(source: str) -> list:
    """ffmpeg reading RMS once per second, printing nothing but metadata."""
    return [
        "ffmpeg", "-hide_banner", "-nostats", "-i", source,
        "-vn",
        "-af", (f"aresample={SAMPLE_RATE},"
                f"asetnsamples={int(SAMPLE_RATE * WINDOW_SECONDS)},"
                "astats=metadata=1:reset=1,"
                "ametadata=print:key=lavfi.astats.Overall.RMS_level:"
                "file=-"),
        "-f", "null", "-",
    ]


def measure(source: str) -> list:
    """[dB per second] for the whole file, or [] if it cannot be read."""
    if not have_ffmpeg():
        return []
    try:
        done = subprocess.run(measure_args(source), stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if done.returncode != 0:
        return []

    levels = []
    for match in _RMS_LINE.finditer(done.stdout.decode("utf-8", "replace")):
        try:
            levels.append(max(FLOOR_DB, float(match.group(1))))
        except ValueError:
            levels.append(FLOOR_DB)
    return levels


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if not ordered:
        return 0.0
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def loudness_over(levels: Sequence[float], start: float, end: float) -> float:
    """How far above this stream's normal the loudest second in here is.

    Relative to the median rather than a fixed dB, because mic gain and
    room differ per setup and what matters is "louder than this streamer
    usually is". Returns dB above median; 0 when there is nothing to say.
    """
    if not levels or end <= start:
        return 0.0
    spoken = [db for db in levels if db > FLOOR_DB]
    if not spoken:
        return 0.0
    baseline = _median(spoken)

    first = max(0, int(start // WINDOW_SECONDS))
    last = min(len(levels), int(end // WINDOW_SECONDS) + 1)
    inside = levels[first:last]
    if not inside:
        return 0.0
    return max(inside) - baseline


def energy_bonus(levels: Sequence[float], start: float, end: float,
                 per_db: float = 0.06, cap: float = 0.35) -> float:
    """A multiplier for a window's score, 1.0 when there is no signal.

    Capped low on purpose. Loud is a hint that something happened, not
    proof it was good - a car alarm peaks too - so this can promote a
    moment the words already liked above another the words also liked,
    and it can never carry a window on its own.
    """
    above = loudness_over(levels, start, end)
    if above <= 0:
        return 1.0
    return 1.0 + min(cap, above * per_db)
