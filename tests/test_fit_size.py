"""A clip over a platform's size limit is re-encoded under it, not lost."""
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "auto_uploader"))

from utils import fit_size  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None,
                                  reason="needs ffmpeg")


def _clip(path, seconds=4):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         f"testsrc2=s=1080x1920:r=30:d={seconds}", "-f", "lavfi", "-i",
         f"sine=frequency=440:duration={seconds}", "-c:v", "libx264",
         "-preset", "ultrafast", "-b:v", "20M", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)], check=True)
    return str(path)


def test_the_bitrate_is_worked_out_from_the_length():
    # 95 MB over 90 s leaves about 7.6 Mbit/s for the picture.
    kbps = fit_size.target_video_kbps(95 * 1024 * 1024, 90)
    assert 7000 < kbps < 8500


@needs_ffmpeg
def test_a_clip_that_fits_is_left_alone(tmp_path):
    clip = _clip(tmp_path / "small.mp4")
    assert fit_size.fit(clip, max_bytes=10 ** 9) == (clip, "")


@needs_ffmpeg
def test_a_clip_over_the_limit_comes_back_under_it(tmp_path):
    clip = _clip(tmp_path / "big.mp4")
    limit = os.path.getsize(clip) // 3
    out, temporary = fit_size.fit(clip, max_bytes=limit)
    assert temporary and out == temporary
    assert os.path.getsize(out) <= limit
    os.remove(temporary)


def test_an_unreadable_clip_is_passed_through_not_failed(tmp_path):
    bogus = tmp_path / "bad.mp4"
    bogus.write_bytes(b"x" * 100)
    assert fit_size.fit(str(bogus), max_bytes=10) == (str(bogus), "")
