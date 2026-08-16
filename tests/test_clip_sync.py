"""
Do the captions land on the words?

Captions came out not matching the audio, and there was no way to tell
which of three things was doing it - a clip cut in the wrong place, a
frame rate stretching the picture against the sound, or a transcript
that writes words down where they were not said. All three look
identical from the outside: text that is out by some amount.

So the measurement came first and the fix second. These tests are what
say the measurement is worth trusting: a KNOWN error is planted, and the
number that comes back has to be that error.

The real-ffmpeg cases are the ones that matter. A sync bug is not
visible in a filter string - the gameplay crop once shipped fully
unit-tested and unable to render a single frame - and the whole point of
this module is to measure something a string cannot express.
"""

import os
import subprocess
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from autoreel.clip_maker import ClipSpec, have_ffmpeg, render_clip
from autoreel.clip_sync import (MIN_CONFIDENCE, best_offset, clock_gap,
                                envelope, have_tools, is_variable_rate,
                                probe_streams, speech_shape,
                                transcript_offset, verdict)

needs_ffmpeg = pytest.mark.skipif(not (have_ffmpeg() and have_tools()),
                                  reason="ffmpeg/ffprobe not installed")

# Where the beeps are in the fixture below, and how long each lasts.
BEEPS = (5.0, 12.0, 20.0)
BEEP_LENGTH = 0.4


@pytest.fixture(scope="module")
def beeps(tmp_path_factory):
    """30s of quiet with three loud beeps at known times.

    Beeps rather than speech because the whole measurement is "where did
    it get loud" - a signal with three unmistakable landmarks is what
    makes a planted error provable rather than plausible.
    """
    path = str(tmp_path_factory.mktemp("sync") / "beeps.mp4")
    windows = "+".join(f"between(t,{at},{at + BEEP_LENGTH})" for at in BEEPS)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=30",
        "-f", "lavfi", "-i", "sine=frequency=800:duration=30",
        "-filter_complex",
        f"[1:a]volume=enable='{windows}':volume=1,"
        f"volume=enable='not({windows})':volume=0.02[a]",
        "-map", "0:v", "-map", "[a]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        path], check=True)
    return path


def _transcript(shift: float = 0.0) -> list:
    """A word per beep, optionally moved off where the sound really is."""
    return [{"words": [{"word": "beep",
                        "start": at + shift,
                        "end": at + BEEP_LENGTH + shift}
                       for at in BEEPS]}]


# ── the sign convention, which is easy to get backwards ──────────────

def test_a_probe_that_runs_early_reads_positive():
    """Positive means "the captions are late by this much", and every
    caller depends on that reading the same way round."""
    reference = [0.0] * 10 + [1.0] * 4 + [0.0] * 10
    probe = [0.0] * 6 + [1.0] * 4 + [0.0] * 10   # the event arrives sooner

    offset, score = best_offset(reference, probe, window=0.05, max_shift=1.0)

    assert offset > 0
    assert offset == pytest.approx(0.20, abs=0.01)
    assert score > 0.9


def test_a_probe_that_runs_late_reads_negative():
    reference = [0.0] * 6 + [1.0] * 4 + [0.0] * 14
    probe = [0.0] * 10 + [1.0] * 4 + [0.0] * 10

    offset, _score = best_offset(reference, probe, window=0.05, max_shift=1.0)

    assert offset == pytest.approx(-0.20, abs=0.01)


def test_two_things_that_are_not_the_same_audio_report_no_confidence():
    """Room tone correlates with itself at every shift. Without this the
    tool would report a confident number for a meaningless match."""
    _offset, score = best_offset([1.0] * 40, [1.0] * 40, window=0.05)

    assert score < MIN_CONFIDENCE


def test_nothing_to_compare_is_not_an_error():
    assert best_offset([], [1.0, 2.0]) == (0.0, 0.0)
    assert best_offset([1.0, 2.0], []) == (0.0, 0.0)


# ── the transcript drawn as a shape ──────────────────────────────────

def test_the_shape_is_loud_in_a_word_and_quiet_between():
    shape = speech_shape([{"words": [{"word": "a", "start": 1.0, "end": 1.2}]}],
                         start=0.0, duration=2.0, window=0.1)

    assert shape[0] == 0.0
    # 1.0s and 1.1s are inside the word; 1.2s is where it ends.
    assert shape[10] == 1.0 and shape[11] == 1.0
    assert shape[15] == 0.0


def test_words_outside_the_window_do_not_appear():
    shape = speech_shape([{"words": [{"word": "a", "start": 90.0, "end": 90.5}]}],
                         start=0.0, duration=2.0, window=0.1)

    assert set(shape) == {0.0}


def test_a_broken_word_entry_is_skipped_not_fatal():
    """Transcripts come from a model and occasionally carry junk. One bad
    word must not cost the whole measurement."""
    shape = speech_shape(
        [{"words": [{"word": "a"},                       # no times at all
                    {"word": "b", "start": "x", "end": 1},
                    {"word": "c", "start": 0.5, "end": 0.7}]}],
        start=0.0, duration=1.0, window=0.1)

    assert shape[5] == 1.0


# ── what the container claims about itself ───────────────────────────

def test_a_clock_gap_is_the_audio_minus_the_video():
    streams = {"audio": {"start_time": 1.5}, "video": {"start_time": 0.5}}

    assert clock_gap(streams) == pytest.approx(1.0)


def test_a_missing_start_time_is_not_a_gap():
    """Plenty of containers simply do not say. Guessing would invent an
    offset that is not there."""
    assert clock_gap({"audio": {}, "video": {"start_time": 0.0}}) == 0.0
    assert clock_gap({}) == 0.0


def test_matching_frame_rates_are_not_called_variable():
    assert not is_variable_rate(
        {"video": {"r_frame_rate": 30.0, "avg_frame_rate": 30.0}})
    assert not is_variable_rate({"video": {}})


def test_frame_rates_that_disagree_are_called_variable():
    assert is_variable_rate(
        {"video": {"r_frame_rate": 60.0, "avg_frame_rate": 41.0}})


# ── the verdict ──────────────────────────────────────────────────────

def test_a_growing_error_is_called_drift():
    said = verdict({"video": {"r_frame_rate": 60.0, "avg_frame_rate": 41.0}},
                   cuts=[(0.05, 0.9), (0.90, 0.9)])

    assert "DRIFT" in said
    assert "frame rate" in said


def test_the_same_error_at_both_points_is_not_called_drift():
    said = verdict({}, cuts=[(0.60, 0.9), (0.62, 0.9)])

    assert "DRIFT" not in said
    assert "FIXED" in said


def test_a_fixed_error_matching_the_clock_gap_names_the_clock():
    said = verdict({"audio": {"start_time": 0.8}, "video": {"start_time": 0.0}},
                   cuts=[(0.80, 0.9), (0.79, 0.9)])

    assert "clock" in said.lower()
    assert "start_time" in said


def test_an_exact_cut_says_so_plainly():
    said = verdict({}, cuts=[(0.0, 1.0), (0.01, 1.0)])

    assert "exact" in said.lower()


def test_a_bad_transcript_is_reported_even_when_the_cut_is_perfect():
    """The half a better cut cannot save. Saying only "the cut is exact"
    would send someone looking in the one place that is fine."""
    said = verdict({}, cuts=[(0.0, 1.0)], words=(-0.60, 0.9))

    assert "TRANSCRIPT" in said
    assert "early" in said
    assert "exact" in said.lower(), "it should still report on the cut too"


def test_low_confidence_is_reported_as_not_knowing():
    """A number nobody can trust is worse than an admission."""
    said = verdict({}, cuts=[(2.0, 0.1)])

    assert "Could not measure" in said


# ── measured against real ffmpeg ─────────────────────────────────────

@needs_ffmpeg
def test_the_measurement_finds_a_cut_that_is_deliberately_late(beeps, tmp_path):
    """A clip cut 0.75s after it was asked for. The captions for it were
    written for the requested time, so they arrive 0.75s late - and the
    tool has to say +0.75."""
    reference = envelope(beeps, 10.0, 8.0)
    late = str(tmp_path / "late.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-accurate_seek", "-ss", "10.750", "-i", beeps,
                    "-t", "8.000", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-ac", "2", late], check=True)

    offset, score = best_offset(reference, envelope(late))

    assert score > 0.8, "the two envelopes did not match at all"
    assert offset == pytest.approx(0.75, abs=0.10)


@needs_ffmpeg
def test_the_real_render_path_cuts_where_it_was_asked_to(beeps, tmp_path):
    """THE regression guard. render_clip seeks with -ss before -i and
    forces 30fps on the output; both are things that can move a cut. If
    a change to that argument list ever starts landing somewhere else,
    this is what says so."""
    reference = envelope(beeps, 10.0, 8.0)
    out = str(tmp_path / "rendered.mp4")

    render_clip(beeps, ClipSpec(10.0, 18.0, 1), out, "center", None,
                "libx264", "ultrafast", 30, watermark=False)

    offset, score = best_offset(reference, envelope(out))

    assert score > 0.8
    assert abs(offset) <= 0.08, \
        f"the render moved the cut by {offset:+.3f}s - one frame is 0.033s"


@needs_ffmpeg
def test_a_transcript_that_agrees_with_the_sound_measures_zero(beeps):
    offset, score = transcript_offset(beeps, _transcript(), 3.0, 20.0)

    assert score > MIN_CONFIDENCE
    assert abs(offset) <= 0.08


@needs_ffmpeg
def test_a_transcript_written_early_is_caught(beeps):
    """Whisper putting every word 600ms before the sound of it. The cut
    is irrelevant here - this is wrong in the source, and it is the case
    that would otherwise have sent someone rewriting the renderer."""
    offset, score = transcript_offset(beeps, _transcript(shift=-0.6),
                                      3.0, 20.0)

    assert score > MIN_CONFIDENCE
    assert offset == pytest.approx(-0.6, abs=0.10)


@needs_ffmpeg
def test_a_transcript_written_late_is_caught(beeps):
    offset, score = transcript_offset(beeps, _transcript(shift=0.5),
                                      3.0, 20.0)

    assert score > MIN_CONFIDENCE
    assert offset == pytest.approx(0.5, abs=0.10)


@needs_ffmpeg
def test_a_file_whose_streams_start_together_reports_no_gap(beeps):
    streams = probe_streams(beeps)

    assert streams["video"]["start_time"] is not None
    assert abs(clock_gap(streams)) <= 0.08
    assert not is_variable_rate(streams)


@needs_ffmpeg
def test_a_skewed_container_is_still_cut_correctly(beeps, tmp_path):
    """Worth knowing rather than assuming: a file whose audio stream
    starts a second after its video is NOT mis-cut, because ffmpeg pads
    the decode to match. The clock gap is real and ffprobe reports it,
    but it does not move the captions - so the tool must not blame it
    when the measurement says the cut is fine."""
    skewed = str(tmp_path / "skewed.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", beeps,
                    "-itsoffset", "1.0", "-i", beeps,
                    "-map", "0:v", "-map", "1:a", "-c", "copy", skewed],
                   check=True)

    assert clock_gap(probe_streams(skewed)) > 0.5, "the skew did not take"

    out = str(tmp_path / "skewed_clip.mp4")
    render_clip(skewed, ClipSpec(10.0, 18.0, 1), out, "center", None,
                "libx264", "ultrafast", 30, watermark=False)

    offset, score = best_offset(envelope(skewed, 10.0, 8.0), envelope(out))

    assert score > 0.8
    assert abs(offset) <= 0.08
