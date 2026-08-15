"""Audio must stay on the picture's clock.

A live recording loses fragments - hours of home wifi guarantee it.
Joined with `-c copy`, missing audio is simply ABSENT: the audio track
comes out shorter than the video, so everything after the gap plays
early, and every resume adds more. On the uploaded VOD that is a visible
delay between the voice and the mouth.

These build real media and run real ffmpeg, because the bug is entirely
in what ffmpeg is asked to do - a mock would have happily agreed with
the broken command line.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import record_stream as rs  # noqa: E402

pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="needs ffmpeg and ffprobe")


def _segment(path, seconds, audio_seconds, tone=440, offset=0):
    """A TS whose audio is deliberately shorter than its video."""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=25:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency={tone}:duration={audio_seconds}",
         "-c:v", "libx264", "-c:a", "aac",
         "-output_ts_offset", str(offset), str(path)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return str(path)


class _Recorder:
    """The real methods, with just enough around them to run."""

    def __init__(self, staging):
        self.staging = str(staging)
        self.said = []

    def say(self, message):
        self.said.append(message)

    _ffmpeg = rs.Recorder._ffmpeg
    _remux = rs.Recorder._remux
    _normalise = rs.Recorder._normalise
    _concat = rs.Recorder._concat


@pytest.fixture
def rec(tmp_path):
    return _Recorder(tmp_path)


# ── the measurement ──────────────────────────────────────────────────

def test_a_short_audio_track_is_measured(tmp_path):
    source = _segment(tmp_path / "s.ts", 10, 9.6)
    assert rs.av_offset(source) > 0.3


def test_a_file_with_no_audio_cannot_be_judged(tmp_path):
    out = tmp_path / "silent.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc=size=320x240:rate=25:duration=2", str(out)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert rs.av_offset(str(out)) is None
    assert "could not measure" in rs.sync_report(str(out))


def test_a_missing_file_is_not_a_crash(tmp_path):
    assert rs.av_offset(str(tmp_path / "nope.mp4")) is None


def test_the_report_says_so_when_it_is_bad(tmp_path):
    source = _segment(tmp_path / "s.ts", 10, 9.0)
    assert "WARNING" in rs.sync_report(source)


# ── the fix ──────────────────────────────────────────────────────────

def test_remux_puts_lost_audio_back_on_the_clock(rec, tmp_path):
    source = _segment(tmp_path / "s.ts", 10, 9.6)
    target = str(tmp_path / "out.mp4")
    assert rec._remux(source, target)
    assert abs(rs.av_offset(target)) <= rs.SYNC_TOLERANCE_S


def test_joining_resumed_segments_does_not_accumulate_drift(rec, tmp_path):
    """The failure that reached YouTube: each resume added its own gap."""
    segments = [
        _segment(tmp_path / "a.ts", 10, 9.6, 440, 1000),
        _segment(tmp_path / "b.ts", 10, 9.6, 880, 55000),
        _segment(tmp_path / "c.ts", 10, 9.6, 660, 99000),
    ]
    target = str(tmp_path / "joined.mp4")
    assert rec._concat(segments, target)
    assert abs(rs.av_offset(target)) <= rs.SYNC_TOLERANCE_S


def test_the_old_copy_only_join_really_did_drift(tmp_path):
    """Proves these tests would have caught it, not just that they pass."""
    segments = [
        _segment(tmp_path / "a.ts", 10, 9.6, 440, 1000),
        _segment(tmp_path / "b.ts", 10, 9.6, 880, 55000),
    ]
    listing = tmp_path / "l.txt"
    listing.write_text("".join(f"file '{s}'\n" for s in segments))
    target = str(tmp_path / "old.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(listing), "-c", "copy", "-movflags", "+faststart", target],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert rs.av_offset(target) > rs.SYNC_TOLERANCE_S


def test_the_join_still_holds_every_segment(rec, tmp_path):
    """Realigning must not cost material."""
    segments = [_segment(tmp_path / f"{n}.ts", 10, 9.6, 440, n * 5000)
                for n in range(1, 4)]
    target = str(tmp_path / "joined.mp4")
    assert rec._concat(segments, target)
    assert rs.probe_duration(target) >= 29.0


def test_video_is_never_re_encoded(rec, tmp_path):
    """Re-encoding 2.5 hours of gameplay to fix audio would be a bad trade."""
    assert "-c:v" in rs._SYNC_AUDIO
    assert rs._SYNC_AUDIO[rs._SYNC_AUDIO.index("-c:v") + 1] == "copy"


def test_the_scratch_segments_are_cleaned_up(rec, tmp_path):
    segments = [_segment(tmp_path / "a.ts", 6, 5.6, 440, 1000),
                _segment(tmp_path / "b.ts", 6, 5.6, 880, 22000)]
    assert rec._concat(segments, str(tmp_path / "j.mp4"))
    assert not [f for f in os.listdir(tmp_path) if f.startswith("_sync")]
    assert not os.path.exists(os.path.join(str(tmp_path), "_concat.txt"))


def test_a_segment_that_cannot_be_realigned_is_still_included(rec, tmp_path):
    """Better a segment with drift than a hole in the stream."""
    good = _segment(tmp_path / "a.ts", 6, 6)
    broken = str(tmp_path / "broken.ts")
    open(broken, "wb").write(b"not media")
    target = str(tmp_path / "j.mp4")
    rec._concat([good, broken], target)
    assert any("as it is" in m for m in rec.said)
