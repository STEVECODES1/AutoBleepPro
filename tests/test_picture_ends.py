"""No clip from where the recording has sound but no picture.

10/8: the video track stopped at 1:58:03, the audio ran to 2:16:31, and
two clips (125 and 127 min) came out audio-only and were queued for
every platform.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from autoreel.clip_maker import ClipSpec, keep_within_picture, video_end_seconds  # noqa: E402

VIDEO_ENDS = 7083.3


def test_clips_past_the_picture_are_dropped():
    specs = [ClipSpec(639, 664, 1), ClipSpec(7505, 7561, 11), ClipSpec(7655, 7692, 12)]
    kept = keep_within_picture(specs, VIDEO_ENDS, 20)
    assert [s.index for s in kept] == [1]


def test_a_clip_running_over_the_end_is_shortened():
    kept = keep_within_picture([ClipSpec(7040, 7100, 3)], VIDEO_ENDS, 20)
    assert kept[0].end == VIDEO_ENDS


def test_a_clip_with_too_little_picture_left_is_dropped():
    assert keep_within_picture([ClipSpec(7070, 7120, 4)], VIDEO_ENDS, 20) == []


def test_unknown_picture_length_changes_nothing():
    specs = [ClipSpec(7505, 7561, 11)]
    assert keep_within_picture(specs, 0.0, 20) == specs


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="needs ffmpeg")
def test_video_end_is_read_from_the_video_stream(tmp_path):
    path = str(tmp_path / "short_video_long_audio.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=c=black:s=64x64:r=10:d=2",
                    "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
                    "-t", "6", "-map", "0:v", "-map", "1:a",
                    "-c:v", "libx264", "-c:a", "aac", path], check=True)
    assert 1.5 < video_end_seconds(path) < 2.6


def test_no_file_reads_as_unknown(tmp_path):
    assert video_end_seconds(str(tmp_path / "missing.mp4")) == 0.0
