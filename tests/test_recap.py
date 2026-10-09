"""The stream recap for the second channel."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from utils.recap import build, pick_moments  # noqa: E402


def test_moments_are_widened_merged_and_in_stream_order():
    ranges = [(600, 640, 0.9), (100, 130, 0.4), (636, 660, 0.5)]
    got = pick_moments(ranges, 7200, max_s=900)
    assert got[0][0] == pytest.approx(94)          # 6 s of context before
    assert len(got) == 2                            # 600-640 and 636-660 merged
    assert got[1] == (594.0, 663.0)


def test_the_best_moments_win_when_there_is_too_much():
    ranges = [(i * 200, i * 200 + 60, i / 10) for i in range(10)]
    got = pick_moments(ranges, 3000, max_s=200)
    assert len(got) == 2                            # 69 s each, 200 s cap
    assert got[-1][0] == pytest.approx(9 * 200 - 6)  # the top score is kept


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_segments_are_joined_into_one_video(tmp_path):
    src, out = str(tmp_path / "s.mp4"), str(tmp_path / "recap.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc=s=640x360:r=30:d=20", "-f", "lavfi", "-i", "sine=d=20",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", src], check=True)
    assert build(src, [(1, 4), (10, 14)], out, "eq=saturation=1.2")
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", out], capture_output=True, text=True).stdout)
    assert 6.5 < dur < 7.6
