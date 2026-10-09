"""A capped platform posts the clipper's best clips first, not the first cut."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from job_queue import JobQueue  # noqa: E402


def test_higher_score_is_claimed_first(tmp_path):
    q = JobQueue(path=str(tmp_path / "jobs.json"))
    q.enqueue("instagram", "a.mp4", extra={"score": 0.40})
    q.enqueue("instagram", "b.mp4", extra={"score": 0.92})
    q.enqueue("instagram", "c.mp4")                       # unknown score
    order = [q.claim().clip_path for _ in range(3)]
    assert order == ["b.mp4", "a.mp4", "c.mp4"]


def test_equal_scores_keep_their_order(tmp_path):
    q = JobQueue(path=str(tmp_path / "jobs.json"))
    for name in ("1.mp4", "2.mp4", "3.mp4"):
        q.enqueue("facebook", name)
    assert [q.claim().clip_path for _ in range(3)] == ["1.mp4", "2.mp4", "3.mp4"]


def test_the_score_is_read_from_the_clip_note(tmp_path):
    from utils.clip_queue import _clip_score
    clip = tmp_path / "Stream - Clip 03.mp4"
    clip.write_bytes(b"x")
    (tmp_path / "Stream - Clip 03_source.json").write_text(
        '{"stream_title": "x", "stream_date": "10-08-26", "score": 0.88}', encoding="utf-8")
    assert _clip_score(str(clip)) == 0.88
    assert _clip_score(str(tmp_path / "nothing.mp4")) == 0.0
