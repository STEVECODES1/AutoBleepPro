"""Uploads wait for a lagging recording; the VOD's picture is graded
while Whisper listens instead of after."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from utils import live_clipper as lc  # noqa: E402


def _frags(folder, fmt, upto):
    for n in range(1, upto + 1):
        (folder / f"Stackswopo 2026-10-09 18_00.part01.ts.f{fmt}.mp4.part-Frag{n}").write_bytes(b"x")


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(lc, "_probe", lambda p: (0.0, "video" if ".f299." in p else "audio"))
    lc._KINDS.clear()
    lc._HOLDING["on"] = False
    return SimpleNamespace(general=SimpleNamespace(recording_folder=str(tmp_path)),
                           project_root=str(tmp_path))


def test_nothing_recording_means_no_hold(cfg):
    assert lc.recording_lag(cfg) == 0.0
    assert lc.uploads_on_hold(cfg) is False


def test_a_lagging_picture_holds_uploads_until_it_catches_up(cfg, tmp_path):
    _frags(tmp_path, 140, 300)
    _frags(tmp_path, 299, 100)
    assert lc.recording_lag(cfg) == 200
    assert lc.uploads_on_hold(cfg) is True
    _frags(tmp_path, 299, 250)          # better, but not caught up yet
    assert lc.uploads_on_hold(cfg) is True
    _frags(tmp_path, 299, 290)
    assert lc.uploads_on_hold(cfg) is False


def test_a_small_lag_never_holds(cfg, tmp_path):
    _frags(tmp_path, 140, 300)
    _frags(tmp_path, 299, 260)
    assert lc.uploads_on_hold(cfg) is False


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_the_picture_graded_early_gets_the_censored_sound(tmp_path):
    from utils import censor
    from utils.ffmpeg_tools import start_video_render, stream_durations

    src, wav = str(tmp_path / "src.mp4"), str(tmp_path / "clean.wav")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc=s=320x240:r=30:d=3", "-f", "lavfi", "-i",
                    "sine=d=3", "-c:v", "libx264", "-c:a", "aac", src], check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "anullsrc=r=48000:cl=stereo", "-t", "3", wav], check=True)
    picture = str(tmp_path / "out.picture.partial.mp4")
    proc, encoder = start_video_render(src, picture, "libx264", "ultrafast",
                                       "eq=saturation=1.3,unsharp=5:5:1.2:5:5:0.0")
    out = str(tmp_path / "out.mp4")
    pre = {"proc": proc, "encoder": encoder, "path": picture}
    assert censor._finish_prerender(pre, wav, out) == f"{encoder}, graded while transcribing"
    video_s, audio_s = stream_durations(out)
    assert video_s and audio_s and abs(video_s - audio_s) < 0.2
    censor._drop_prerender(pre)
    assert not os.path.exists(picture)


def test_no_grade_means_no_early_render(tmp_path):
    from utils import censor
    assert censor._start_prerender("x.mp4", str(tmp_path / "o.mp4"),
                                   {"stream_copy_video": False, "video_filter": ""}) is None
    assert censor._start_prerender("x.mp4", str(tmp_path / "o.mp4"),
                                   {"stream_copy_video": True, "video_filter": "eq"}) is None
