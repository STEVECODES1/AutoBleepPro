"""
tests/test_job_queue.py
"""
import pytest
from auto_uploader.job_queue import JobQueue, MAX_ATTEMPTS


def _queue(tmp_path):
    return JobQueue(tmp_path / "queue.json")


def test_enqueue_and_acquire(tmp_path):
    q = _queue(tmp_path)
    jid = q.enqueue("instagram", "clip.mp4", caption="test")
    job = q.acquire()
    assert job is not None
    assert job["id"] == jid
    assert job["status"] == "leased"


def test_acquire_returns_none_when_empty(tmp_path):
    q = _queue(tmp_path)
    assert q.acquire() is None


def test_complete_removes_job(tmp_path):
    q = _queue(tmp_path)
    jid = q.enqueue("x", "clip.mp4")
    q.acquire()
    q.complete(jid)
    assert q.list_jobs() == []


def test_fail_increments_attempts(tmp_path):
    q = _queue(tmp_path)
    jid = q.enqueue("instagram", "clip.mp4")
    q.acquire()
    q.fail(jid)
    jobs = q.list_jobs()
    assert jobs[0]["attempts"] == 1
    assert jobs[0]["status"] == "pending"


def test_fail_marks_dead_at_max_attempts(tmp_path):
    q = _queue(tmp_path)
    jid = q.enqueue("instagram", "clip.mp4")
    for _ in range(MAX_ATTEMPTS):
        q.acquire()
        q.fail(jid)
    jobs = q.list_jobs()
    assert jobs[0]["status"] == "dead"


def test_block_does_not_increment_attempts(tmp_path):
    q = _queue(tmp_path)
    jid = q.enqueue("instagram", "clip.mp4")
    q.acquire()
    q.block(jid, reason="cap hit")
    jobs = q.list_jobs()
    assert jobs[0]["attempts"] == 0
    assert jobs[0]["status"] == "blocked"
    assert jobs[0]["blocked_reason"] == "cap hit"


def test_unblock_all_resets_to_pending(tmp_path):
    q = _queue(tmp_path)
    jid = q.enqueue("instagram", "clip.mp4")
    q.acquire()
    q.block(jid, reason="spacing")
    count = q.unblock_all()
    assert count == 1
    assert q.list_jobs()[0]["status"] == "pending"


def test_expired_lease_recovers(tmp_path):
    import json, time
    q = _queue(tmp_path)
    jid = q.enqueue("instagram", "clip.mp4")
    # Manually set lease to the past
    path = tmp_path / "queue.json"
    jobs = json.loads(path.read_text())
    jobs[0]["status"] = "leased"
    jobs[0]["lease_until"] = time.time() - 10
    path.write_text(json.dumps(jobs))
    recovered = q.acquire()
    assert recovered is not None
    assert recovered["id"] == jid
