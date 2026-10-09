"""keep_uploaded_videos must not delete a stream because its clips are newer.

10/8: keep_uploaded_videos=3 counted the stream and its clips together,
three clips were newer, and the only copy of the stream went an hour
after it landed - on the night its Rumble upload had to be redone.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from utils import cleanup  # noqa: E402

DAY = 86400
NOW = 1_800_000_000.0


def _cfg(folder):
    return SimpleNamespace(general=SimpleNamespace(
        uploaded_folder=str(folder), supported_formats=(".mp4", ".ts")))


def _video(folder, name, size, age_days):
    path = folder / name
    path.write_bytes(b"x" * size)
    when = NOW - age_days * DAY
    os.utime(path, (when, when))
    return path


def test_newer_clips_do_not_push_the_stream_out(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup, "STREAM_MIN_BYTES", 1000)
    _video(tmp_path, "stream.mp4", 5000, age_days=4)
    for i in range(5):
        _video(tmp_path, f"clip{i}.mp4", 10, age_days=4 - i * 0.1)
    cleanup.prune_uploaded_folder(_cfg(tmp_path), 3, now=NOW)
    left = sorted(os.listdir(tmp_path))
    assert "stream.mp4" in left
    assert len([n for n in left if n.startswith("clip")]) == 3


def test_nothing_under_three_days_old_is_pruned(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup, "STREAM_MIN_BYTES", 1000)
    for i in range(5):
        _video(tmp_path, f"stream{i}.mp4", 5000, age_days=1 + i * 0.1)
    cleanup.prune_uploaded_folder(_cfg(tmp_path), 1, now=NOW)
    assert len(os.listdir(tmp_path)) == 5


def test_old_streams_past_the_count_still_go(tmp_path, monkeypatch):
    monkeypatch.setattr(cleanup, "STREAM_MIN_BYTES", 1000)
    for i in range(4):
        _video(tmp_path, f"stream{i}.mp4", 5000, age_days=10 - i)
    cleanup.prune_uploaded_folder(_cfg(tmp_path), 2, now=NOW)
    assert sorted(os.listdir(tmp_path)) == ["stream2.mp4", "stream3.mp4"]
