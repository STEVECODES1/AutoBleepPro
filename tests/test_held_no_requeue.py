import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "auto_uploader"))

from job_queue import JobQueue  # noqa: E402


def test_held_clip_is_not_queued_again(tmp_path):
    q = JobQueue(path=str(tmp_path / "jobs.json"))
    first = q.add("buffer_x", r"C:\w\Clip 03.mp4")
    q.abandon(first.id, "held: 7 slurs in 18s (limit 3)")
    again = q.add("buffer_x", r"C:\other\Clip 03.mp4")
    assert again.id == first.id
    assert len(q.list_jobs()) == 1


def test_other_failures_still_allow_a_repost(tmp_path):
    q = JobQueue(path=str(tmp_path / "jobs.json"))
    first = q.add("buffer_x", "Clip 04.mp4")
    q.abandon(first.id, "the clip is no longer on disk")
    again = q.add("buffer_x", "Clip 04.mp4")
    assert again.id != first.id
