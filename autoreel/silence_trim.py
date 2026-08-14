"""
Cut the dead air out of a censored export.

WHAT THIS IS FOR
----------------
A stream has long stretches where nothing is said - a loading screen, a
menu, someone reading chat, the gap between one bit and the next. They
are not mistakes and they are fine live, but in an export they are the
reason a forty-minute video is watched for four. Removing them is the
single cheapest thing that can be done to pacing.

WHY TWO SIGNALS AND NOT ONE
---------------------------
A gap in the TRANSCRIPT is not silence. Whisper drops laughter,
shouting, a controller being thrown and most non-speech noise, so a
stretch with no words in it is frequently the best part of the stream.
Cutting on the transcript alone removes exactly the moments a clipper
would keep.

Loudness alone is no better: a stream with music or game audio under it
never drops below a fixed threshold, so nothing is ever cut.

So a range has to be BOTH: no words AND quiet relative to this video's
own normal. That intersection is dead air. Either signal on its own is
a different thing wearing its name.

WHY ONE FFMPEG PASS
-------------------
The obvious implementation cuts the file into segments and concatenates
them. On a three-hour stream with two hundred gaps that is two hundred
encodes plus a concat, it is quadratic in the wrong place, and every
segment boundary is another chance to lose sync between audio and video.

This builds a single `select`/`aselect` expression naming the ranges to
KEEP and hands it to ffmpeg once. One decode, one encode, one output,
and the timestamps are rebuilt by `setpts`/`asetpts` so the result plays
with no gaps and no drift.

NOTHING IS CUT BY DEFAULT
-------------------------
Off unless asked for. A trim is destructive in the sense that matters -
the export is what gets published - and a threshold that suits one
streamer's pacing ruins another's.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
from typing import Iterable, Optional, Sequence

# A pause under this is punctuation, not dead air. Speech naturally has
# gaps of half a second to a second and a half; cutting those makes a
# person sound like they are being interrupted by an editor.
DEFAULT_MIN_SILENCE_S = 2.5

# Left at each edge of a cut, so a trim never clips the breath before a
# word or the tail of the one before it. Without this the joins are
# audible even when the timing is right.
DEFAULT_PAD_S = 0.25

# How far below this video's OWN median loudness counts as quiet. A
# fixed dB threshold cannot work across a stream with game audio, a
# stream with music and a stream with neither.
DEFAULT_QUIET_BELOW_DB = 12.0

# Never remove so much that the result stops resembling the stream. A
# trim taking more than this is a sign the thresholds are wrong - most
# likely a video whose speech was never transcribed - and it stops
# rather than silently returning something unrecognisable.
MAX_REMOVED_FRACTION = 0.6

_TIMEOUT = 60 * 90


class TrimError(RuntimeError):
    """The trim could not be applied. The uncut file is still valid."""


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


# ── Finding the dead air ────────────────────────────────────────────────

def _spoken_spans(words: Iterable[dict]) -> list:
    """(start, end) for every word that has usable timings."""
    spans = []
    for word in words or ():
        try:
            start = float(word["start"])
            end = float(word["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end > start:
            spans.append((start, end))
    spans.sort()
    return spans


def _merge(spans: Sequence, join_gap: float = 0.0) -> list:
    """Overlapping or near-touching spans, combined."""
    merged: list = []
    for start, end in spans:
        if merged and start - merged[-1][1] <= join_gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def wordless_gaps(words: Iterable[dict], duration: float,
                  min_silence_s: float = DEFAULT_MIN_SILENCE_S) -> list:
    """Stretches with no transcribed word in them, long enough to matter.

    Includes the head and tail of the video: a stream that opens on two
    minutes of waiting screen has its longest gap before the first word,
    and a run that only looked between words would never find it.
    """
    spoken = _merge(_spoken_spans(words), join_gap=0.0)
    gaps = []
    cursor = 0.0
    for start, end in spoken:
        if start - cursor >= min_silence_s:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if duration - cursor >= min_silence_s:
        gaps.append((cursor, duration))
    return gaps


def speech_baseline(levels: Sequence, words: Iterable[dict],
                    window_seconds: float = 1.0) -> Optional[float]:
    """This video's normal loudness, measured over the SPOKEN parts only.

    Taking the median of the whole file is the obvious version and it is
    wrong: on a stream that is half dead air, half the samples ARE the
    dead air, so the median lands in the silence and nothing is ever
    quiet relative to it. The more silence a video has, the less the
    check works - exactly backwards.

    Measuring only where words were said gives a stable reference no
    matter how much silence surrounds it.
    """
    if not levels:
        return None
    spoken_windows = set()
    for start, end in _spoken_spans(words):
        for index in range(int(start // window_seconds),
                           int(end // window_seconds) + 1):
            if 0 <= index < len(levels):
                spoken_windows.add(index)

    sampled = sorted(levels[i] for i in spoken_windows if levels[i] > -80.0)
    if not sampled:
        # No speech windows to measure - fall back to the whole file
        # rather than refusing to work at all.
        sampled = sorted(db for db in levels if db > -80.0)
    if not sampled:
        return None

    middle = len(sampled) // 2
    return (sampled[middle] if len(sampled) % 2
            else (sampled[middle - 1] + sampled[middle]) / 2)


def quiet_enough(levels: Sequence, start: float, end: float,
                 quiet_below_db: float = DEFAULT_QUIET_BELOW_DB,
                 window_seconds: float = 1.0,
                 baseline: Optional[float] = None) -> bool:
    """Whether this range is quiet relative to the video's own normal.

    `baseline` comes from speech_baseline(); when it is not supplied the
    whole file is used, which is only correct for a video that is mostly
    speech.
    """
    if not levels:
        # No measurement: fall back to the transcript alone rather than
        # refusing to trim. Said differently - an unreadable audio track
        # should not disable a feature that was asked for.
        return True

    if baseline is None:
        sampled = sorted(db for db in levels if db > -80.0)
        if not sampled:
            return True
        middle = len(sampled) // 2
        baseline = (sampled[middle] if len(sampled) % 2
                    else (sampled[middle - 1] + sampled[middle]) / 2)

    # Both edges round INWARD. A gap runs from the end of one word to
    # the start of the next, so the partial window at each edge contains
    # speech - and including either one made every gap measure as loud,
    # which is all of them, so nothing was ever cut. Ceil the start,
    # floor the end, and only whole quiet windows are measured.
    first = max(0, math.ceil(start / window_seconds))
    last = min(len(levels), int(end // window_seconds))
    inside = levels[first:last]
    if not inside:
        return True
    return max(inside) <= baseline - quiet_below_db


def find_dead_air(words: Iterable[dict], duration: float,
                  levels: Optional[Sequence] = None,
                  min_silence_s: float = DEFAULT_MIN_SILENCE_S,
                  pad_s: float = DEFAULT_PAD_S,
                  quiet_below_db: float = DEFAULT_QUIET_BELOW_DB) -> list:
    """[(start, end)] to REMOVE. Empty when there is nothing worth cutting.

    Both conditions, always: no words AND quiet for this video. See the
    module docstring for why either alone is the wrong tool.
    """
    if duration <= 0:
        return []

    words = list(words or ())
    baseline = speech_baseline(levels or [], words)

    cuts = []
    for start, end in wordless_gaps(words, duration, min_silence_s):
        if not quiet_enough(levels or [], start, end, quiet_below_db,
                            baseline=baseline):
            continue
        padded_start = start + pad_s
        padded_end = end - pad_s
        # Padding can eat a gap that only just qualified; that is the
        # correct outcome rather than an edge case.
        if padded_end - padded_start >= min_silence_s / 2:
            cuts.append((round(padded_start, 3), round(padded_end, 3)))
    return _merge(cuts, join_gap=0.05)


def removed_seconds(cuts: Sequence) -> float:
    return sum(max(0.0, end - start) for start, end in cuts)


# ── Turning that into one ffmpeg pass ───────────────────────────────────

def keep_ranges(cuts: Sequence, duration: float) -> list:
    """The inverse of `cuts` - what survives, in order."""
    keeps = []
    cursor = 0.0
    for start, end in sorted(cuts):
        if start > cursor:
            keeps.append((cursor, start))
        cursor = max(cursor, end)
    if duration > cursor:
        keeps.append((cursor, duration))
    return [(a, b) for a, b in keeps if b - a > 0.01]


def select_expression(keeps: Sequence) -> str:
    """An ffmpeg select expression matching every kept range.

    `between(t,a,b)` per range, OR-ed. Written once and used for both
    the video and the audio filter so the two cannot disagree about
    where the cuts are - which is the failure that produces a file that
    drifts out of sync halfway through.
    """
    if not keeps:
        return "1"
    return "+".join(f"between(t,{start:.3f},{end:.3f})" for start, end in keeps)


def trim_filter(keeps: Sequence) -> str:
    """The full -filter_complex for one pass.

    setpts/asetpts rebuild the timestamps from zero after selecting, so
    the output has no gaps where the removed ranges were. Without them
    ffmpeg keeps the original timestamps and the player sits still
    through every cut.
    """
    expression = select_expression(keeps)
    return (f"[0:v]select='{expression}',setpts=N/FRAME_RATE/TB[v];"
            f"[0:a]aselect='{expression}',asetpts=N/SR/TB[a]")


def trim_args(source: str, out_path: str, keeps: Sequence,
              encoder: str = "libx264", preset: str = "veryfast",
              crf: int = 20) -> list:
    """One ffmpeg invocation. One decode, one encode, one output."""
    quality = (["-c:v", "h264_nvenc", "-preset", "p4", "-cq", str(crf)]
               if encoder == "h264_nvenc"
               else ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)])
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", source,
        "-filter_complex", trim_filter(keeps),
        "-map", "[v]", "-map", "[a]",
        *quality,
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        out_path,
    ]


def apply_trim(source: str, out_path: str, cuts: Sequence, duration: float,
               encoder: str = "libx264", preset: str = "veryfast",
               crf: int = 20) -> str:
    """Write `source` minus `cuts`. Returns the path actually written.

    Returns the SOURCE path unchanged when there is nothing to cut, so a
    caller can use the return value without checking whether a trim
    happened.

    Raises TrimError rather than leaving a half-written file: the export
    is the thing being published, and a truncated one that looks
    finished is worse than no trim at all.
    """
    if not cuts:
        return source
    if not have_ffmpeg():
        raise TrimError("ffmpeg is not on PATH - silence trimming needs it")

    removed = removed_seconds(cuts)
    if duration > 0 and removed / duration > MAX_REMOVED_FRACTION:
        raise TrimError(
            f"the trim would remove {removed / duration:.0%} of the video, "
            f"which almost always means the speech was never transcribed "
            f"rather than that the stream was that quiet. Nothing was cut.")

    keeps = keep_ranges(cuts, duration)
    if not keeps:
        raise TrimError("trimming would leave nothing behind")

    partial = f"{out_path}.partial.mp4"
    try:
        done = subprocess.run(
            trim_args(source, partial, keeps, encoder, preset, crf),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=_TIMEOUT)
    except subprocess.TimeoutExpired:
        _remove(partial)
        raise TrimError("the trim took too long and was stopped")
    except OSError as exc:
        _remove(partial)
        raise TrimError(str(exc))

    if done.returncode != 0 or not os.path.exists(partial):
        detail = (done.stderr or b"").decode("utf-8", "replace").strip()
        _remove(partial)
        raise TrimError(f"ffmpeg failed (exit {done.returncode}): "
                        f"{detail[-400:] or 'no output'}")

    os.replace(partial, out_path)
    return out_path


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def describe(cuts: Sequence, duration: float) -> str:
    """One line for a log or a status bar."""
    if not cuts:
        return "No dead air worth cutting."
    removed = removed_seconds(cuts)
    share = f" ({removed / duration:.0%})" if duration > 0 else ""
    return (f"Trimming {len(cuts)} silent stretch(es), "
            f"{removed / 60:.1f} min{share}.")
