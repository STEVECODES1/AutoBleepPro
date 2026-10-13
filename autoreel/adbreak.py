"""Cut Twitch "Commercial break in progress" slates out of an export.

WHY
---
When the streamer runs ads, Twitch replaces the stream with a static
purple-to-blue gradient slate for the length of the break - typically 30
seconds to 3 minutes. There is nothing to "bypass": that slate IS the
stream while it is up, subscribers see it too, and the recorder captures
it like any other segment. What can be done is removing it afterwards,
so the export does not sit on a dead gradient and no clip is ever cut
from one.

WHY PICTURE FIRST, AUDIO SECOND
--------------------------------
The slate is identified by what it looks like - a still frame whose
colour sits in the purple-blue band - not by loudness. Loudness alone
cannot tell a break from a quiet game menu, and the transcript cannot
tell it from anything at all (Whisper transcribes the slate as nothing,
which is exactly what dead air looks like too).

So the picture decides and the audio confirms: a run of still,
purple-blue frames is a candidate; it is only cut when the audio under
it is also quiet for this video. Either signal on its own is a different
thing wearing its name.

WHY A MINIMUM LENGTH
--------------------
A purple game menu, a blue loading screen or a colour wash in an overlay
can each pass a single frame. None of them holds perfectly still for
twenty seconds with quiet audio. The minimum break length is what keeps
a colourful moment in a game from being mistaken for a commercial.

NOTHING IS CUT BY DEFAULT
--------------------------
Off unless asked for. The export is what gets published, and only the
uploader's YouTube copy is ever cut - Rumble takes the untouched source
by design, slate and all.

The actual cutting reuses autoreel.silence_trim's single-pass
select/aselect machinery rather than growing a second ffmpeg path.
"""

from __future__ import annotations

import math
import shutil
import subprocess
from typing import Iterable, Optional, Sequence, Tuple

# A commercial break is measured in tens of seconds. Anything shorter
# that looks like a slate is a menu, an overlay or a transition - all
# things that must survive.
MIN_BREAK_S = 20.0

# One look per second. The slate never moves, so denser sampling buys
# nothing and only costs decode time on multi-hour streams.
STEP_S = 1.0

# Frames are shrunk to this before measuring. Colour statistics do not
# need pixels, and a 3-hour stream decoded at full HD just to average
# its colours would be decode time spent on nothing.
SAMPLE_W = 160
SAMPLE_H = 90

# One noisy sample must not split a break in two: compression shimmer
# around the twitch logo can flip a single frame.
MERGE_GAP_S = 3.0

# Between two samples a second apart, this fraction of pixels changing
# means the picture is alive. The slate is a still image: the only
# differences are compression noise. Measured against content_kind's
# PIXEL_CHANGE=14, where a held desktop reads ~0.005 and anything
# moving reads far above this.
STILL_FRACTION = 0.02
PIXEL_CHANGE = 14

# The slate is mid-brightness: brighter than any black loading screen,
# darker than any white flash. Average grey, 0-255.
SLATE_BRIGHT_LO = 45.0
SLATE_BRIGHT_HI = 190.0

# Spread of grey across the frame. A gradient is not perfectly flat,
# but it is smooth - far below gameplay or a detailed page.
SLATE_FLAT_STD = 60.0

# How far below this video's own normal the audio under a candidate
# must sit. Same scale as silence_trim's default, for the same reason:
# a fixed dB threshold cannot work across streams with and without
# game audio underneath.
QUIET_BELOW_DB = 12.0

_TIMEOUT = 60 * 90


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def have_numpy() -> bool:
    try:
        import numpy  # noqa: F401
        return True
    except ImportError:
        return False


# ── What a slate looks like ──────────────────────────────────────────

def _downscale(frame):
    """Thin striding to ~32x18 for statistics. No information the
    classifier uses lives at higher resolution."""
    import numpy as np

    arr = np.asarray(frame)
    if arr.ndim != 3 or arr.shape[2] < 3:
        return None
    step_y = max(1, arr.shape[0] // 18)
    step_x = max(1, arr.shape[1] // 32)
    return arr[::step_y, ::step_x, :3].astype(np.float32)


def is_slate_frame(frame) -> bool:
    """Whether one RGB frame looks like the commercial-break slate.

    Purple-to-blue, mid-brightness, smooth. Says nothing about motion
    or sound - the caller combines those. Never raises: an unreadable
    frame is not a slate.
    """
    try:
        small = _downscale(frame)
        if small is None:
            return False
        import numpy as np

        red = float(small[:, :, 0].mean())
        green = float(small[:, :, 1].mean())
        blue = float(small[:, :, 2].mean())
        grey = small.mean(axis=2)
        brightness = float(grey.mean())
        spread = float(grey.std())
    except Exception:
        return False

    if not (SLATE_BRIGHT_LO <= brightness <= SLATE_BRIGHT_HI):
        return False
    if spread > SLATE_FLAT_STD:
        return False
    # Blue dominates green and red is clearly present: the average of
    # a purple-to-cyan gradient. Flat grey fails here (channels equal),
    # flat red fails harder (no blue at all), and dark blue menus fail
    # on red. The red bar is deliberately a floor, not a balance ratio:
    # compression and gradient position move the exact purple around,
    # and duration + stillness + quiet carry the real decision.
    if not (blue >= green + 8.0 and red >= 60.0 and blue >= 70.0):
        return False
    return True


def _still(before, after) -> bool:
    """Whether two consecutive 1-second samples are the same picture."""
    import numpy as np

    try:
        first = np.asarray(before).astype(np.int16)
        second = np.asarray(after).astype(np.int16)
        if first.shape != second.shape:
            return False
        # Luminance only: chroma shimmer in a gradient is noise,
        # and measuring it would call a still slate alive.
        if first.ndim == 3:
            first = first.mean(axis=2)
            second = second.mean(axis=2)
        diff = np.abs(first - second)
        return bool((diff > PIXEL_CHANGE).mean() <= STILL_FRACTION)
    except Exception:
        return False


# ── Sampling the video ───────────────────────────────────────────────

def _probe_duration(source: str) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", source],
            capture_output=True, text=True, timeout=30)
        return max(0.0, float((out.stdout or "0").strip()))
    except Exception:
        return 0.0


def sample_times(duration: float, step_s: float = STEP_S) -> list:
    """One timestamp per `step_s`, centres of each window."""
    if duration <= 0 or step_s <= 0:
        return []
    count = int(duration // step_s)
    return [step_s * (i + 0.5) for i in range(max(0, count))]


def iter_frames(source: str, times: Sequence[float]):
    """(t, RGB frame) decoded at SAMPLE_W x SAMPLE_H, streamed.

    Frames are yielded one at a time and only ever held two-deep by
    the caller, so a 3-hour stream costs one decode pass and no RAM
    worth naming. Yields nothing when ffmpeg or numpy is missing.
    """
    if not times or not have_ffmpeg() or not have_numpy():
        return
    import numpy as np

    frame_bytes = SAMPLE_W * SAMPLE_H * 3
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", source,
           "-vf", f"fps=1/{STEP_S:.3f},scale={SAMPLE_W}:{SAMPLE_H}",
           "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
    except OSError:
        return
    try:
        # fps= runs from the stream start, so sample i covers
        # [i*step, (i+1)*step); report the window centre to match
        # sample_times().
        index = 0
        while True:
            chunk = proc.stdout.read(frame_bytes) if proc.stdout else b""
            if not chunk or len(chunk) < frame_bytes:
                break
            if index < len(times):
                frame = np.frombuffer(chunk, dtype=np.uint8).reshape(
                    SAMPLE_H, SAMPLE_W, 3)
                yield times[index], frame
            index += 1
    finally:
        try:
            proc.kill()
        except Exception:
            pass


# ── Finding the breaks ───────────────────────────────────────────────

def _runs(flags: Sequence[Tuple[float, bool]],
          step_s: float = STEP_S,
          min_break_s: float = MIN_BREAK_S) -> list:
    """Positive runs long enough to be a break, as (start, end).

    A single negative sample inside MERGE_GAP_S does not split a run:
    compression shimmer around the logo flips one frame without the
    break going anywhere.
    """
    runs: list = []
    start: Optional[float] = None
    last: float = 0.0
    gap = 0.0
    for at, positive in flags:
        if positive:
            if start is None:
                start = at - step_s / 2
            gap = 0.0
            last = at + step_s / 2
        elif start is not None:
            gap += step_s
            if gap > MERGE_GAP_S:
                if last - start >= min_break_s:
                    runs.append((round(start, 3), round(last, 3)))
                start = None
                gap = 0.0
    if start is not None and last - start >= min_break_s:
        runs.append((round(start, 3), round(last, 3)))
    return runs


def _outside_median(levels: Sequence, start: float, end: float,
                    window_seconds: float = 1.0) -> Optional[float]:
    """Median loudness of everything OUTSIDE this run.

    The whole-file median is the wrong reference here: a stream that is
    half commercial break is half quiet, so the median lands in the
    quiet and nothing is ever quiet relative to it - the more breaks a
    video has, the less the check works, exactly backwards. (This is
    the same trap silence_trim.speech_baseline avoids by measuring
    over spoken parts; with no words available, everything outside
    the candidate is the closest equivalent.)
    """
    outside = [db for i, db in enumerate(levels)
               if i * window_seconds + window_seconds <= start
               or i * window_seconds >= end]
    audible = sorted(db for db in outside if db > -80.0)
    if not audible:
        return None
    middle = len(audible) // 2
    return (audible[middle] if len(audible) % 2
            else (audible[middle - 1] + audible[middle]) / 2)


def _quiet(levels: Sequence, start: float, end: float,
           words: Iterable[dict] = (),
           quiet_below_db: float = QUIET_BELOW_DB,
           window_s: float = 1.0) -> bool:
    """The audio under this run, measured against the video's normal."""
    from autoreel import silence_trim

    words = list(words or ())
    try:
        if words:
            baseline = silence_trim.speech_baseline(
                levels or [], words, window_seconds=window_s)
        else:
            baseline = _outside_median(levels or [], start, end,
                                       window_seconds=window_s)
    except Exception:
        baseline = None
    try:
        return silence_trim.quiet_enough(
            levels or [], start, end, quiet_below_db,
            window_seconds=window_s, baseline=baseline)
    except Exception:
        # An unreadable audio track must not disable the picture
        # signal - return True the way quiet_enough does on no data.
        return True


def find_adbreaks(source: str, levels: Optional[Sequence] = None,
                  words: Iterable[dict] = (),
                  duration: float = 0.0,
                  step_s: float = STEP_S,
                  min_break_s: float = MIN_BREAK_S) -> list:
    """[(start, end)] of commercial-break slate to REMOVE.

    Empty when there is nothing worth cutting - including when ffmpeg
    or numpy is missing, which is a refusal rather than a failure: no
    measurement, no cut. Callers treat [] as "keep the full length".
    """
    if duration <= 0:
        duration = _probe_duration(source)
    if duration <= 0:
        return []
    words = list(words or ())

    # The loudness series is SUPPOSED to be one reading per second,
    # but the only thing guaranteed is one reading per window in
    # order - so derive the window from the series itself rather
    # than assuming. (Observed 2026-10-13: measure() yielding one
    # reading per ~2s on this machine's ffmpeg; assuming 1.0 put
    # every run's audio gate on the wrong seconds.)
    levels = list(levels or [])
    window_s = duration / len(levels) if levels else step_s

    flags: list = []
    prev = None
    for at, frame in iter_frames(source, sample_times(duration, step_s)):
        slate = is_slate_frame(frame)
        if slate and prev is not None:
            slate = slate and _still(prev, frame)
        flags.append((at, slate))
        prev = frame
    if not flags:
        return []

    cuts = []
    for start, end in _runs(flags, step_s, min_break_s):
        if _quiet(levels, start, end, words, window_s=window_s):
            cuts.append((start, end))
    return cuts


def classify_samples(frames: Sequence) -> list:
    """is_slate_frame + stillness over an in-memory frame list.

    The ffmpeg-free half of find_adbreaks, and the half the tests
    exercise: sampling needs a real video file, classifying does not.
    """
    out: list = []
    prev = None
    for frame in frames or ():
        slate = is_slate_frame(frame)
        if slate and prev is not None:
            slate = slate and _still(prev, frame)
        out.append(bool(slate))
        prev = frame
    return out


def removed_seconds(cuts: Sequence) -> float:
    return sum(max(0.0, end - start) for start, end in cuts)


def describe(cuts: Sequence, duration: float) -> str:
    """One line for a log or a status bar."""
    if not cuts:
        return "No commercial breaks worth cutting."
    removed = removed_seconds(cuts)
    share = f" ({removed / duration:.0%})" if duration > 0 else ""
    return (f"Cutting {len(cuts)} commercial break(s), "
            f"{removed / 60:.1f} min{share}.")
