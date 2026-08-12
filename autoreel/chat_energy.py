"""
When chat went off. Read, counted, and deleted.

WHY CHAT IS THE BEST SIGNAL THERE IS
------------------------------------
Everything else guessing at clips is inference. The scorer infers from
word choice, the loudness pass infers from volume, the model infers from
a transcript. Chat is not inference - it is a few hundred people saying,
at a timestamp, that something just happened. A message-rate spike is as
close to ground truth for "that was funny" as this pipeline can get, and
it costs nothing but a download.

NOTHING IS KEPT
---------------
A stream's chat log is tens of thousands of messages and tens of
megabytes, it is full of other people's usernames and words, and none of
that is wanted here. The file is downloaded to a temp path, reduced to
one number per second - how many messages arrived - and deleted in a
`finally` before this function returns. What survives is a list of
integers with no text, no names and no IDs in it. There is no setting to
keep the file, because there is no reason to have one.

WHERE IT WORKS
--------------
YouTube serves chat replay on a finished stream and yt-dlp can fetch it.
Twitch, Kick and Rumble either do not expose it the same way or need a
separate tool, so those return nothing and the clips are chosen by the
other signals exactly as before. No chat is a normal answer, not a
failure.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Sequence

# One count per second, matching audio_energy so the two can be reasoned
# about together.
WINDOW_SECONDS = 1.0

_TIMEOUT = 60 * 20


def ytdlp_command() -> list:
    try:
        import yt_dlp  # noqa: F401
    except Exception:
        return ["yt-dlp"]
    return [sys.executable, "-m", "yt_dlp"]


def download_args(url: str, output_stem: str) -> list:
    """Chat replay only - no video, no audio, no thumbnail."""
    return ytdlp_command() + [
        "--skip-download",
        "--write-subs",
        "--sub-langs", "live_chat",
        "--no-warnings",
        "--ignore-errors",
        "--socket-timeout", "30",
        "-o", output_stem,
        url,
    ]


def _timestamps(path: str) -> list:
    """Seconds-from-start for every message. Text is never read.

    yt-dlp writes one JSON object per line. Only the offset is taken from
    each; the message body, the author and the IDs are stepped over and
    never enter a variable.
    """
    offsets = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                micros = entry.get("videoOffsetTimeMsec") if isinstance(entry, dict) else None
                if micros is None and isinstance(entry, dict):
                    micros = entry.get("replayChatItemAction", {}).get(
                        "videoOffsetTimeMsec")
                try:
                    offsets.append(float(micros) / 1000.0)
                except (TypeError, ValueError):
                    continue
    except OSError:
        return []
    return offsets


def _rates(offsets: Sequence[float]) -> list:
    if not offsets:
        return []
    last = int(max(offsets) // WINDOW_SECONDS) + 1
    counts = [0] * last
    for seconds in offsets:
        index = int(seconds // WINDOW_SECONDS)
        if 0 <= index < last:
            counts[index] += 1
    return counts


def rates_for_url(url: str) -> list:
    """[messages per second] for this video, or [] if there is no chat.

    The downloaded log never outlives this call.
    """
    if not url:
        return []
    workspace = tempfile.mkdtemp(prefix="chat_")
    stem = os.path.join(workspace, "chat")
    try:
        try:
            subprocess.run(download_args(url, stem), timeout=_TIMEOUT,
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            return []

        found = ""
        for name in os.listdir(workspace):
            if "live_chat" in name:
                found = os.path.join(workspace, name)
                break
        if not found:
            return []
        return _rates(_timestamps(found))
    finally:
        # Before returning, always. The log is tens of megabytes of other
        # people's words and there is no reason to keep any of it.
        shutil.rmtree(workspace, ignore_errors=True)


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2


def spike_over(rates: Sequence[int], start: float, end: float) -> float:
    """How many times the usual rate the busiest second in here reached.

    Relative to the stream's own median, because a channel with four
    hundred viewers and one with forty have completely different normal
    rates and the question is "busier than usual for THIS stream".
    """
    if not rates or end <= start:
        return 0.0
    active = [n for n in rates if n > 0]
    if not active:
        return 0.0
    baseline = max(1.0, _median(active))

    first = max(0, int(start // WINDOW_SECONDS))
    last = min(len(rates), int(end // WINDOW_SECONDS) + 1)
    inside = rates[first:last]
    if not inside:
        return 0.0
    return max(inside) / baseline


def chat_bonus(rates: Sequence[int], start: float, end: float,
               cap: float = 0.5) -> float:
    """A multiplier for a window's score, 1.0 when there is no chat.

    Allowed a bigger cap than loudness, because chat is an opinion and
    volume is a measurement: a hundred people typing at once is a much
    better reason to clip something than a loud noise is. It still only
    adjusts windows the words already put forward.
    """
    spike = spike_over(rates, start, end)
    if spike <= 1.0:
        return 1.0
    return 1.0 + min(cap, (spike - 1.0) * 0.25)
