"""Clips get jump cuts: pauses with no words AND no sound are cut out."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from autoreel.clip_maker import ClipSpec, clip_words, jump_cut  # noqa: E402


def test_words_are_moved_to_the_clip_timeline():
    segs = [{"words": [{"start": 99.0, "end": 99.5, "word": "a"},
                       {"start": 100.2, "end": 100.6, "word": "b"},
                       {"start": 131.0, "end": 131.4, "word": "c"}]}]
    words = clip_words(segs, 100.0, 130.0)
    assert [w["word"] for w in words] == ["b"]
    assert words[0]["start"] == pytest.approx(0.2)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_a_long_silent_pause_is_cut_and_speech_kept(tmp_path):
    clip = str(tmp_path / "clip.mp4")
    # 8 s of tone, 6 s of silence, 8 s of tone = 22 s.
    subprocess.run(["ffmpeg", "-y", "-v", "error",
                    "-f", "lavfi", "-i", "testsrc=s=160x120:r=30:d=22",
                    "-f", "lavfi", "-i",
                    "aevalsrc='if(between(t,8,14),0,0.5*sin(2*PI*440*t))':s=48000:d=22",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", clip], check=True)
    words = [{"start": s, "end": s + 0.5, "word": "w"} for s in
             (0.5, 2, 3.5, 5, 6.5, 14.5, 16, 17.5, 19, 20.5)]
    spec = ClipSpec(start=0.0, end=22.0, index=1)
    removed = jump_cut(clip, [{"words": words}], spec, min_silence_s=0.9, min_seconds=5)
    assert removed > 3
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", clip], capture_output=True, text=True).stdout
    assert float(out) < 19


def test_too_few_words_leaves_the_clip_alone(tmp_path):
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"x")
    assert jump_cut(str(clip), [], ClipSpec(0, 30, 1)) == 0.0
    assert clip.read_bytes() == b"x"
