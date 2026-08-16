"""
Measure whether a rendered clip actually starts where it was asked to.

WHY THIS EXISTS
---------------
Burned-in captions came out not matching the audio, and there was no way
to tell which of three things was doing it - they all look identical from
the outside.

  (a) THE TWO CLOCKS DISAGREE. The transcript is made by decoding the
      source's AUDIO stream to a wav (utils/ffmpeg_tools.extract_audio),
      so Whisper's timestamps are on the audio's clock. The clip is cut
      with `-ss` on the CONTAINER's clock. A non-zero audio start_time,
      an mp4 edit list or AAC priming delay makes those differ by a fixed
      amount, and every clip is off by exactly that much.

  (b) A VARIABLE FRAME RATE SOURCE FORCED TO 30. Frames get stretched
      relative to the sound, and captions are burned into the picture -
      so they drift with it. This one gets WORSE through the clip.

  (c) THE SEEK LANDS SOMEWHERE ELSE on this container.

Reading the code cannot separate them. Measuring can, and the separation
is simple: (a) and (c) are the SAME offset wherever you sample the video;
(b) grows. So this samples twice, early and late, and compares.

HOW THE MEASUREMENT WORKS
-------------------------
Take the loudness envelope of the source across a window - decoded from
the start, never seeked, so it is on the same clock the transcript used.
Render a clip over that same window through the real render path. Take
the rendered clip's envelope. Slide one against the other and find the
shift where they line up best.

That shift IS the caption error. If the clip's audio sits 800ms later
than the transcript thinks it does, every caption in it is 800ms early.

Loudness rather than samples because it is robust to the re-encode: the
clip has been through aac at 128k and a resample, so the waveform is not
the same waveform, but the shape of where it got loud is.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Optional, Sequence

# 50ms buckets. Fine enough to see an error a viewer would notice - a
# caption a third of a second out is visibly wrong - and coarse enough
# that speech, which is not loud continuously, still correlates.
WINDOW = 0.05

# How far to look either way. Bigger than any plausible seek error, and
# small enough that the search cannot find a spurious match against a
# different sentence.
MAX_SHIFT = 3.0

SAMPLE_RATE = 8000
FLOOR_DB = -90.0

_RMS_LINE = re.compile(r"lavfi\.astats\.Overall\.RMS_level=(-?\d+(?:\.\d+)?)")
_TIMEOUT = 60 * 30


def have_tools() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


# ── what the file says about itself ──────────────────────────────────

def probe_streams(source: str) -> dict:
    """start_time, frame rates and durations, per stream.

    Everything here is a claim the CONTAINER makes. It is what `-ss` is
    measured against, which is exactly why it matters.
    """
    try:
        done = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", source],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    try:
        data = json.loads(done.stdout.decode("utf-8", "replace") or "{}")
    except ValueError:
        return {}

    out: dict = {"container_start": _float(
        (data.get("format") or {}).get("start_time"))}
    for stream in data.get("streams") or []:
        kind = stream.get("codec_type")
        if kind not in ("video", "audio") or kind in out:
            continue
        entry = {
            "start_time": _float(stream.get("start_time")),
            "duration": _float(stream.get("duration")),
            "codec": stream.get("codec_name", ""),
        }
        if kind == "video":
            entry["r_frame_rate"] = _rate(stream.get("r_frame_rate"))
            entry["avg_frame_rate"] = _rate(stream.get("avg_frame_rate"))
        out[kind] = entry
    return out


def _float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rate(value) -> Optional[float]:
    """ffprobe writes frame rates as "60000/1001"."""
    try:
        top, _, bottom = str(value or "").partition("/")
        divisor = float(bottom or 1)
        return float(top) / divisor if divisor else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def clock_gap(streams: dict) -> float:
    """How far the audio clock sits from the video clock, in seconds.

    This is mechanism (a) expressed as one number. Non-zero means the
    transcript and the seek are not counting from the same instant.
    """
    audio = (streams.get("audio") or {}).get("start_time")
    video = (streams.get("video") or {}).get("start_time")
    if audio is None or video is None:
        return 0.0
    return float(audio) - float(video)


def is_variable_rate(streams: dict) -> bool:
    """True when the container's two frame-rate claims disagree.

    r_frame_rate is the finest rate that can express every frame;
    avg_frame_rate is frames divided by duration. On constant-rate video
    they match. A meaningful gap is the signature of VFR, which is what
    makes forcing 30fps on the output risky.
    """
    video = streams.get("video") or {}
    listed, average = video.get("r_frame_rate"), video.get("avg_frame_rate")
    if not listed or not average:
        return False
    return abs(listed - average) / max(listed, average) > 0.02


# ── loudness, on a clock we choose ───────────────────────────────────

def envelope_args(source: str, start: float = 0.0, duration: float = 0.0,
                  window: float = WINDOW, fast: bool = False) -> list:
    """ffmpeg printing RMS once per `window`, over [start, start+duration].

    Two ways to reach the window, and the choice matters.

    `atrim` (fast=False) runs on DECODED audio, so it cuts on exactly the
    clock the transcript was made on - but it decodes from the start of
    the file to get there. That is the honest way to MEASURE a seek,
    because using -ss to check -ss would hide the error being looked for.

    `-ss` (fast=True) is O(clip) instead of O(everything before it). A
    clip two hours into a stream costs two hours of audio decoding the
    other way, per clip, which is not payable inside a render loop. Only
    for callers that have already established the seek is sound.
    """
    chain = []
    seek = []
    if fast and duration > 0:
        seek = ["-accurate_seek", "-ss", f"{start:.3f}"]
    elif duration > 0:
        chain.append(f"atrim=start={start:.3f}:end={start + duration:.3f},"
                     "asetpts=PTS-STARTPTS")
    chain.append(f"aresample={SAMPLE_RATE}")
    chain.append(f"asetnsamples={max(1, int(SAMPLE_RATE * window))}")
    chain.append("astats=metadata=1:reset=1")
    chain.append("ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-")
    args = ["ffmpeg", "-hide_banner", "-nostats"] + seek + \
           ["-i", source, "-vn"]
    if fast and duration > 0:
        args += ["-t", f"{duration:.3f}"]
    return args + ["-af", ",".join(chain), "-f", "null", "-"]


def envelope(source: str, start: float = 0.0, duration: float = 0.0,
             window: float = WINDOW, fast: bool = False) -> list:
    """[dB per `window` seconds], or [] when it cannot be read."""
    if not have_tools():
        return []
    try:
        done = subprocess.run(envelope_args(source, start, duration, window,
                                            fast),
                              stdout=subprocess.PIPE,
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


# ── lining the two up ────────────────────────────────────────────────

def _correlation(left: Sequence[float], right: Sequence[float]) -> float:
    """Pearson, on whatever length both share. 0.0 when undefined."""
    size = min(len(left), len(right))
    if size < 4:
        return 0.0
    a, b = list(left[:size]), list(right[:size])
    mean_a, mean_b = sum(a) / size, sum(b) / size
    top = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    left_sq = sum((x - mean_a) ** 2 for x in a)
    right_sq = sum((y - mean_b) ** 2 for y in b)
    bottom = (left_sq * right_sq) ** 0.5
    return top / bottom if bottom else 0.0


def best_offset(reference: Sequence[float], probe: Sequence[float],
                window: float = WINDOW,
                max_shift: float = MAX_SHIFT) -> tuple:
    """(seconds, confidence) - how far `probe` sits from `reference`.

    POSITIVE means `probe` runs EARLIER than `reference`, and in both
    places this is used that comes out as "the captions are late by this
    much". Negative is the mirror: captions early.

    Worked through for the clip case, because the double negative is easy
    to get backwards. A clip asked for at 10.0 but actually cut at 10.75
    contains a word that lives at source-time 12.0. The caption file was
    written assuming the clip starts at 10.0, so it puts that word at
    t=2.0. The word is really at t=1.25. The caption arrives 0.75s after
    the word is said - late - and this returns +0.75.

    Confidence is the correlation at the winning shift. Below about 0.5
    the two are not the same audio and the number means nothing - a VOD
    of steady room tone correlates with itself at every shift.
    """
    if not reference or not probe:
        return 0.0, 0.0
    steps = int(max_shift / window) if window > 0 else 0
    best, score = 0, -2.0
    for shift in range(-steps, steps + 1):
        if shift >= 0:
            pair = _correlation(reference[shift:], probe)
        else:
            pair = _correlation(reference, probe[-shift:])
        if pair > score:
            best, score = shift, pair
    return round(best * window, 3), round(score, 3)


# ── does the transcript agree with the sound? ────────────────────────
#
# The cut being exact does not make the captions right. The other half
# of the sum is the transcript: Whisper says a word happened at 412.6s,
# and if the audio says it happened at 413.4s then the caption is wrong
# by 800ms no matter how perfectly the clip is cut.
#
# This is measurable the same way, because a transcript IS an envelope -
# loud where there are words, quiet in the gaps between them.

def speech_shape(segments, start: float, duration: float,
                 window: float = WINDOW) -> list:
    """The transcript drawn as an envelope: 1.0 in a word, 0.0 between.

    Deliberately crude. It is not trying to predict how LOUD a word was,
    only when there was one and when there was not - which is the only
    thing that has to line up.
    """
    buckets = max(1, int(round(duration / window)))
    shape = [0.0] * buckets
    for segment in segments or ():
        for word in (segment.get("words") or ()):
            try:
                begins = float(word["start"]) - start
                ends = float(word["end"]) - start
            except (KeyError, TypeError, ValueError):
                continue
            first = int(max(0.0, begins) / window)
            last = int(min(duration, max(0.0, ends)) / window)
            for index in range(first, min(last + 1, buckets)):
                shape[index] = 1.0
    return shape


def transcript_offset(source: str, segments, start: float, duration: float,
                      window: float = WINDOW,
                      max_shift: float = MAX_SHIFT,
                      fast: bool = False) -> tuple:
    """(seconds, confidence) - how far the WORDS sit from the sound.

    Same sign as best_offset: positive means the captions are late,
    negative means they are early. A transcript whose words all sit
    600ms before the sound of them returns -0.6, and every caption made
    from it comes up 600ms early.

    Independent of how the clip is cut. This is wrong in the SOURCE, so
    every clip inherits it and re-cutting cannot help.
    """
    heard = envelope(source, start, duration, window, fast)
    if not heard:
        return 0.0, 0.0
    return best_offset(speech_shape(segments, start, duration, window),
                       heard, window, max_shift)


# ── correcting it, per clip ──────────────────────────────────────────

# Never move a caption further than this. Beyond a second the two things
# being lined up are probably not the same speech, and a confident-
# looking match on the wrong sentence would make every caption worse.
MAX_CORRECTION = 1.0

# Under a frame nobody sees it, and shifting costs a decode.
WORTH_FIXING = 0.08


def alignment_for(source: str, segments, start: float, duration: float) -> float:
    """Seconds to ADD to this clip's word times so they land on the sound.

    0.0 when there is nothing worth fixing, nothing measurable, or a
    match too weak to act on. Silence is the right answer far more often
    than a correction is: this runs on every clip, and a wrong shift
    makes a caption worse in a way the viewer notices immediately.

    The sign: transcript_offset returns positive when the captions are
    LATE, so the correction is its negative.
    """
    offset, score = transcript_offset(source, segments, start, duration,
                                      fast=True)
    if score < MIN_CONFIDENCE:
        return 0.0
    if abs(offset) < WORTH_FIXING or abs(offset) > MAX_CORRECTION:
        return 0.0
    return round(-offset, 3)


# ── the verdict ──────────────────────────────────────────────────────

# Under this and nobody would ever see it: a frame at 30fps is 33ms.
NEGLIGIBLE = 0.08

# Below this the two envelopes are not the same audio and the offset is
# noise, not a measurement.
MIN_CONFIDENCE = 0.5


def verdict(streams: dict, cuts: Sequence[tuple],
            words: tuple = (0.0, 0.0)) -> str:
    """Plain English naming which mechanism the numbers point at.

    `cuts` is (offset, confidence) per sample point through the video,
    earliest first - the error in WHERE THE CLIP STARTS. `words` is the
    error in the TRANSCRIPT ITSELF, which is a different problem with the
    same symptom and needs saying separately, because re-cutting cannot
    fix it.

    Everything hangs on whether the cut samples agree with each other. A
    fixed offset is a clock or a seek problem; one that grows through the
    video is the frame rate.
    """
    measured = [(off, score) for off, score in cuts
                if score >= MIN_CONFIDENCE]
    word_offset, word_score = words

    lines = []

    # The transcript first: it is the half that a better cut cannot save.
    if word_score >= MIN_CONFIDENCE and abs(word_offset) > NEGLIGIBLE:
        direction = "late" if word_offset > 0 else "early"
        lines.append(
            f"THE TRANSCRIPT IS OFF by {word_offset:+.2f}s - the words are "
            f"written down {abs(word_offset):.2f}s from where they are "
            f"actually said, so every caption comes up {direction}. This is "
            f"wrong in the source, so every clip inherits it and cutting "
            f"differently cannot help. Fix: re-transcribe this video with a "
            f"larger censor_model, or shift the cached word times by "
            f"{-word_offset:+.2f}s.")

    if not measured:
        lines.append(
            "Could not measure the cut. No sample matched well enough to "
            "trust, which usually means the test points landed on silence "
            "or steady background noise rather than on speech.")
        return "\n\n".join(lines) if lines else "Nothing measurable."

    worst = max(measured, key=lambda pair: abs(pair[0]))[0]
    first, last = measured[0][0], measured[-1][0]
    grew = len(measured) > 1 and abs(last) - abs(first) > 0.15

    if grew and abs(last) > NEGLIGIBLE:
        rate = ("variable frame rate" if is_variable_rate(streams)
                else "a frame rate the container describes two ways")
        lines.append(
            f"THE CUT DRIFTS, worse the further into the video it goes: "
            f"{first:+.2f}s early on, {last:+.2f}s later. The source has "
            f"{rate}, and the render forces 30fps as an output option, which "
            f"stretches the picture against the sound. Captions are burned "
            f"into the picture, so they drift with it. Fix: set the frame "
            f"rate inside the filter chain, where it is driven by "
            f"timestamps.")
    elif abs(worst) > NEGLIGIBLE:
        gap = clock_gap(streams)
        if abs(gap) > NEGLIGIBLE and abs(abs(gap) - abs(worst)) < 0.25:
            lines.append(
                f"THE CUT IS OFF BY A FIXED {worst:+.2f}s, and that matches "
                f"this file's audio/video clock gap of {gap:+.2f}s. The "
                f"transcript is made from the decoded audio; the clip is cut "
                f"on the container's clock. Fix: seek relative to the "
                f"source's own start_time so both count from the same "
                f"instant.")
        else:
            lines.append(
                f"THE CUT IS OFF BY A FIXED {worst:+.2f}s wherever it is "
                f"sampled, and that does NOT match the file's clock gap "
                f"({gap:+.2f}s) - so it is the seek landing off target on "
                f"this container. Fix: normalise the seek and start the "
                f"clip's audio explicitly at zero.")
    else:
        lines.append(
            "The cut is exact - the clip starts where it was asked to, "
            "within a frame.")

    return "\n\n".join(lines)
