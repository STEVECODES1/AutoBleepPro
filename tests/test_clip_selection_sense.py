"""Clips that are chosen because they are good, not because it is time.

From a real 112-minute run:

    [Clips] Spacing clips 9 min apart to cover the whole 112 min.
    [Clips] The vision pass failed (The read operation timed out
            (48 images, 0.6 MB)) - going on the words instead.
    [Clips] No model opinion - picking on the transcript score alone.
     ... Clip 01 (from 6m23s)   Clip 02 (from 10m56s)  Clip 03 (from 14m41s)
     ... Clip 04 (from 21m35s)  Clip 05 (from 38m24s)  Clip 06 (from 43m37s)
     ... Clip 07 (from 61m12s)  Clip 08 (from 77m04s)  Clip 09 (from 97m13s)
     ... Clip 10 (from 100m16s)

Ten clips, evenly spread, "most of these don't make no sense".

Three separate things made that happen and each has its own test below:
the gap became a coverage rule, the vision pass got a text request's
timeout and no retry, and the framing profile only ever looked at the
first ten minutes of a two-hour stream.
"""

from __future__ import annotations

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from autoreel.clip_maker import MAX_SPREAD_GAP, spread_gap_for  # noqa: E402
from autoreel.content_kind import sample_points  # noqa: E402


# ── the gap is de-duplication, not coverage ──────────────────────────

def test_ten_clips_over_two_hours_are_not_forced_nine_minutes_apart():
    """The exact run. A nine-minute gap means one clip per nine-minute
    bucket whether or not anything happened in it."""
    gap = spread_gap_for(span=112 * 60, count=10, configured=90.0)

    assert gap <= MAX_SPREAD_GAP
    assert gap < 9 * 60


def test_a_short_stream_still_spreads_normally():
    """The ceiling must not disturb the case the scaling was written
    for - six clips out of twenty minutes."""
    assert spread_gap_for(span=20 * 60, count=6, configured=90.0) == 160.0


def test_clips_still_cannot_be_the_same_moment_twice():
    """What the gap actually protects against."""
    assert spread_gap_for(span=112 * 60, count=10, configured=90.0) >= 90.0


def test_a_configured_floor_is_never_lowered_by_the_ceiling():
    """A caller asking for ten minutes apart means it."""
    assert spread_gap_for(span=112 * 60, count=10, configured=600.0) == 600.0


def test_no_span_and_no_count_fall_back_to_what_was_asked_for():
    assert spread_gap_for(span=0, count=10, configured=90.0) == 90.0
    assert spread_gap_for(span=600, count=0, configured=90.0) == 90.0


# ── the picture is sampled across the whole video ────────────────────

def test_the_samples_cover_the_whole_stream_not_the_first_ten_minutes():
    """A 112-minute stream that opened on a Monkey call and then played
    GTA for a hundred minutes was filed as `monkey`, and every clip got a
    face-tracking crop of gameplay."""
    points = sample_points(112 * 60, samples=5, spacing=300.0)

    assert len(points) == 5
    assert max(points) > 60 * 60, "it never looked past the first hour"
    assert min(points) > 0, "the very start is the least representative part"
    assert max(points) < 112 * 60


def test_the_samples_are_spread_evenly():
    points = sample_points(1000.0, samples=4, spacing=300.0)

    assert points == [200.0, 400.0, 600.0, 800.0]


def test_an_unreadable_length_still_gets_looked_at():
    """ffprobe can fail; a profile guess from fixed offsets beats none."""
    points = sample_points(0.0, samples=3, spacing=300.0)

    assert points == [0.0, 300.0, 600.0]
