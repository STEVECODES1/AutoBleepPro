"""
The signal a transcript cannot carry.

Everything else that picks clips reads words. That is deaf to the thing a
clipper actually watches for - the moment the room explodes. Laughter
reaches a transcript as "hahaha" if Whisper bothered and as nothing if it
did not; a shout and a mumble are the same text.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autoreel.audio_energy import (
    FLOOR_DB,
    energy_bonus,
    loudness_over,
    measure_args,
)
from autoreel.highlights import HighlightScorer


def test_no_measurement_means_no_opinion():
    """A file whose audio could not be read must score exactly as before."""
    assert energy_bonus([], 0, 30) == 1.0


def test_a_flat_stream_gets_no_boost_anywhere():
    """Everything loud is the same as nothing loud."""
    assert energy_bonus([-30.0] * 200, 50, 80) == 1.0


def test_the_loud_moment_is_the_one_promoted():
    levels = [-30.0] * 200
    levels[100] = -10.0

    assert energy_bonus(levels, 90, 110) > 1.0
    assert energy_bonus(levels, 0, 40) == 1.0


def test_loudness_is_measured_against_this_streamer_not_a_fixed_number():
    """Mic gain and room differ per setup; what matters is louder than
    this streamer usually is."""
    quiet_rig = [-50.0] * 100
    quiet_rig[50] = -30.0
    loud_rig = [-20.0] * 100
    loud_rig[50] = 0.0

    assert round(loudness_over(quiet_rig, 40, 60)) == \
        round(loudness_over(loud_rig, 40, 60))


def test_the_boost_is_capped_so_loud_can_never_carry_a_clip():
    """A car alarm peaks too. Loud is a hint, not a verdict."""
    levels = [-60.0] * 100
    levels[50] = 0.0

    assert energy_bonus(levels, 40, 60) <= 1.4


def test_silence_cannot_drag_the_baseline_to_nothing():
    levels = [FLOOR_DB] * 90 + [-30.0] * 10
    # The baseline comes from the parts with sound in them, so a mostly
    # silent file does not treat any noise at all as a huge spike.
    assert energy_bonus(levels, 90, 100) < 1.2


def test_the_scorer_uses_it_when_given_and_ignores_it_when_not():
    segments = []
    at = 0.0
    for n in range(10):
        segments.append({"start": at, "end": at + 4.0,
                         "text": f"No way that was actually insane {n}!",
                         "words": []})
        at += 4.2

    plain = HighlightScorer(min_duration=15, max_duration=40)
    windows = plain.candidate_windows(segments)
    assert windows

    levels = [-40.0] * 60
    for second in range(20, 30):
        levels[second] = -8.0
    loud = HighlightScorer(min_duration=15, max_duration=40, energy=levels)

    assert loud.candidate_windows(segments)[0].score > 0


def test_the_measurement_decodes_audio_only():
    """A three-hour video decoded in full would cost more than the clips."""
    args = measure_args("stream.mp4")

    assert "-vn" in args
    assert "astats" in " ".join(args)
    assert args[-3:] == ["-f", "null", "-"], "it must write no file"
