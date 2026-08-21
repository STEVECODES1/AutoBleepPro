"""One stream is often two shows. Upload it as two.

A four-hour stream that opens on Monkey and then plays GTA went up as a
single four-hour video. An account reposting the same stream split it -
the GTA run as its own upload, the Monkey run as another - and the GTA
one took 1.21K views while the buried Monkey hour would have taken none,
because nobody looking for Monkey content scrolls to hour three of a GTA
VOD.

Nothing here is clever. The detector that already tells Monkey from GTA
for FRAMING is pointed along the timeline instead of at one sample, and
runs of the same kind become segments.

What it will NOT do is chop a stream into confetti. A stretch has to be
long enough to be worth its own upload and its own title, and a stream
that was one thing throughout comes back as one segment - which is the
correct answer, and the common one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

# How often to look. A minute is far finer than the thing being measured
# - somebody does not switch from GTA to Monkey for ninety seconds - and
# a look costs one small still, so an hour of video is sixty stills.
SAMPLE_EVERY = 60.0

# Below this, a stretch is not its own show. Twelve minutes is about the
# shortest thing worth a title, a thumbnail and a slot on a channel.
MIN_SEGMENT_SECONDS = 12 * 60.0

# A single odd reading inside a long run is a menu, a loading screen or
# somebody alt-tabbing - not a change of show. A kind has to hold for
# this many samples in a row before it counts as a change.
SETTLE_SAMPLES = 3


@dataclass
class Segment:
    """One stretch of a VOD that is its own thing."""
    start: float
    end: float
    kind: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def _seconds_long(source: str) -> float:
    from autoreel.content_kind import _seconds_long as measured

    return measured(source)


def read_kinds(source: str, span: float, every: float = SAMPLE_EVERY,
               look=None) -> list:
    """[(at_seconds, kind)] along the whole video."""
    from autoreel.content_kind import kind_for_video

    look = look or kind_for_video
    if span <= 0:
        return []
    marks = []
    at = 0.0
    while at < span:
        try:
            marks.append((at, look(source, start=at)))
        except Exception:
            marks.append((at, ""))
        at += max(1.0, every)
    return marks


def _settled(marks: list, settle: int = SETTLE_SAMPLES) -> list:
    """The kind at each mark, with one-off readings smoothed away.

    A menu between two fights reads as neither, and a loading screen in a
    Monkey call reads as gameplay. Neither is a change of show.
    """
    kinds = [kind for _at, kind in marks]
    out = list(kinds)
    for index, kind in enumerate(kinds):
        window = kinds[index:index + settle]
        if len(window) == settle and len(set(window)) == 1:
            continue
        # Not settled here: keep whatever was settled most recently.
        out[index] = out[index - 1] if index else kind
    return out


def segments_for(source: str, span: Optional[float] = None,
                 every: float = SAMPLE_EVERY,
                 min_seconds: float = MIN_SEGMENT_SECONDS,
                 look=None) -> list:
    """The stretches this VOD is made of, in order.

    One segment covering the whole file means it was one show - which is
    the common answer and not a failure. An empty list means nothing
    could be read at all, and the caller should upload the VOD whole.
    """
    if not source or not os.path.isfile(source):
        return []
    span = _seconds_long(source) if span is None else span
    if span <= 0:
        return []

    marks = read_kinds(source, span, every, look)
    if not marks:
        return []
    kinds = _settled(marks)

    runs = []
    start_at = marks[0][0]
    current = kinds[0]
    for index in range(1, len(marks)):
        if kinds[index] == current:
            continue
        runs.append(Segment(start_at, marks[index][0], current))
        start_at = marks[index][0]
        current = kinds[index]
    runs.append(Segment(start_at, span, current))

    return _merge_short(runs, min_seconds)


def _merge_short(runs: list, min_seconds: float) -> list:
    """Fold anything too short to be its own upload into its neighbour.

    Into the LONGER neighbour, not simply the previous one: a ten-minute
    stretch between four hours of GTA and forty minutes of Monkey belongs
    with the four hours.
    """
    runs = [r for r in runs if r.duration > 0]
    if not runs:
        return []
    while len(runs) > 1:
        shortest = min(range(len(runs)), key=lambda i: runs[i].duration)
        if runs[shortest].duration >= min_seconds:
            break
        before = runs[shortest - 1] if shortest > 0 else None
        after = runs[shortest + 1] if shortest + 1 < len(runs) else None
        if before and (not after or before.duration >= after.duration):
            before.end = runs[shortest].end
        else:
            after.start = runs[shortest].start
        runs.pop(shortest)
        runs = _coalesce(runs)
    return _coalesce(runs)


def _coalesce(runs: list) -> list:
    """Join neighbours that are the same thing.

    Absorbing a short stretch leaves the two sides of it as separate
    runs of the same kind - two GTA segments with nothing between them,
    which would upload as "part 1" and "part 2" of one continuous show.
    """
    if not runs:
        return []
    joined = [runs[0]]
    for part in runs[1:]:
        if part.kind == joined[-1].kind:
            joined[-1].end = part.end
        else:
            joined.append(part)
    return joined


def worth_splitting(segments: list) -> bool:
    """True when this VOD is genuinely more than one show."""
    kinds = {s.kind for s in segments if s.kind}
    return len(segments) > 1 and len(kinds) > 1


def describe(segments: list) -> str:
    """One line per segment, for the run log."""
    lines = []
    for index, part in enumerate(segments, 1):
        lines.append(
            f"  part {index}: {part.start / 60:.0f}m to {part.end / 60:.0f}m "
            f"({part.duration / 60:.0f} min) - {part.kind or 'unknown'}")
    return "\n".join(lines)
