import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "auto_uploader"))

from job_queue import JobQueue  # noqa: E402


def test_held_clip_is_not_queued_again(tmp_path):
    q = JobQueue(path=str(tmp_path / "jobs.json"))
    first = q.add("instagram", r"C:\w\Clip 03.mp4")
    q.abandon(first.id, "held: 7 slurs in 18s (limit 3)")
    again = q.add("instagram", r"C:\other\Clip 03.mp4")
    assert again.id == first.id
    assert len(q.list_jobs()) == 1


def test_x_old_slur_count_hold_is_void(tmp_path):
    q = JobQueue(path=str(tmp_path / "jobs.json"))
    first = q.add("buffer_x", "Clip 05.mp4")
    q.abandon(first.id, "held: 7 slurs in 18s (limit 3)")
    assert q.add("buffer_x", "Clip 05.mp4").id != first.id
    ig = q.add("instagram", "Clip 05.mp4")
    q.abandon(ig.id, "held: 7 slurs in 18s (limit 3)")
    assert q.add("instagram", "Clip 05.mp4").id == ig.id


def test_x_skips_count_but_not_severe(monkeypatch):
    from utils import clip_queue as cq

    class Hit:
        def __init__(self, w):
            self.word = w

    class Engine:
        words = []

        def __init__(self, **_):
            pass

        def scan_segments(self, _):
            return [Hit(w) for w in Engine.words]

    import autoreel.compliance as comp
    monkeypatch.setattr(comp, "ComplianceEngine", Engine)
    monkeypatch.setattr(cq, "_clip_segments", lambda *a: [{"end": 20}])
    Engine.words = ["nigga"] * 6
    assert cq._slur_heavy("c.mp4", {}, "instagram")
    assert cq._slur_heavy("c.mp4", {}, "buffer_x") == ""
    Engine.words = ["faggot"]
    assert cq._slur_heavy("c.mp4", {}, "buffer_x")


def test_other_failures_still_allow_a_repost(tmp_path):
    q = JobQueue(path=str(tmp_path / "jobs.json"))
    first = q.add("buffer_x", "Clip 04.mp4")
    q.abandon(first.id, "the clip is no longer on disk")
    again = q.add("buffer_x", "Clip 04.mp4")
    assert again.id != first.id
