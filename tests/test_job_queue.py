"""
Job queue: retry safety and crash recovery.

The failure modes worth testing are duplicate posts (re-queuing the same
clip), stranded work (a worker dies holding a job), and infinite retries
(a broken job eating the daily posting quota).
"""

from __future__ import annotations

import json
import os
import sys

import pytest

_UPLOADER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto_uploader")
sys.path.insert(0, _UPLOADER)

from utils.job_queue import (  # noqa: E402
    BLOCKED,
    DONE,
    FAILED,
    IN_PROGRESS,
    NEEDS_APPROVAL,
    PENDING,
    JobQueue,
)

NOW = 1_770_000_000.0


@pytest.fixture
def queue(tmp_path):
    return JobQueue(path=str(tmp_path / "clip_jobs.json"),
                    max_attempts=3, lease_seconds=1800,
                    backoff_seconds=(60, 300, 1800))


# ═════════════════════════════════════════════════════════════════════════════
# Adding work
# ═════════════════════════════════════════════════════════════════════════════

def test_add_and_claim(queue):
    job = queue.add("instagram", "/clips/a.mp4", caption="hi", now=NOW)
    assert job.state == PENDING and job.attempts == 0

    claimed = queue.claim("instagram", now=NOW)
    assert claimed.id == job.id and claimed.state == IN_PROGRESS


def test_requeuing_the_same_clip_does_not_duplicate(queue):
    """Two jobs for one clip means the clip gets posted twice."""
    first = queue.add("instagram", "/clips/a.mp4", now=NOW)
    second = queue.add("instagram", "/clips/a.mp4", now=NOW + 10)
    assert first.id == second.id
    assert len(queue.list_jobs()) == 1


def test_same_clip_on_different_platforms_is_two_jobs(queue):
    queue.add("instagram", "/clips/a.mp4", now=NOW)
    queue.add("facebook", "/clips/a.mp4", now=NOW)
    assert len(queue.list_jobs()) == 2


def test_a_finished_clip_can_be_queued_again(queue):
    job = queue.add("instagram", "/clips/a.mp4", now=NOW)
    queue.complete(job.id, "https://example/1", now=NOW + 10)
    again = queue.add("instagram", "/clips/a.mp4", now=NOW + 20)
    assert again.id != job.id, "a deliberate re-post should be possible"


def test_claim_returns_nothing_when_empty(queue):
    assert queue.claim(now=NOW) is None


def test_claim_is_oldest_first(queue):
    queue.add("instagram", "/clips/old.mp4", now=NOW)
    queue.add("instagram", "/clips/new.mp4", now=NOW + 100)
    assert queue.claim("instagram", now=NOW + 200).clip_path == "/clips/old.mp4"


def test_claim_can_filter_by_platform(queue):
    queue.add("instagram", "/clips/a.mp4", now=NOW)
    queue.add("facebook", "/clips/b.mp4", now=NOW)
    assert queue.claim("facebook", now=NOW).platform == "facebook"


# ═════════════════════════════════════════════════════════════════════════════
# Manual approval
# ═════════════════════════════════════════════════════════════════════════════

def test_approval_jobs_are_never_claimed(queue):
    queue.add("reddit", "/clips/a.mp4", needs_approval=True, now=NOW)
    assert queue.claim(now=NOW) is None
    assert queue.claim("reddit", now=NOW) is None


def test_approval_makes_it_claimable(queue):
    job = queue.add("reddit", "/clips/a.mp4", needs_approval=True, now=NOW)
    assert queue.approve(job.id, now=NOW + 10).state == PENDING
    assert queue.claim("reddit", now=NOW + 20).id == job.id


def test_approving_a_normal_job_is_a_no_op(queue):
    job = queue.add("instagram", "/clips/a.mp4", now=NOW)
    assert queue.approve(job.id, now=NOW) is None
    assert queue.get(job.id).state == PENDING


# ═════════════════════════════════════════════════════════════════════════════
# Failure, backoff, and the attempt ceiling
# ═════════════════════════════════════════════════════════════════════════════

def test_failure_backs_off_before_retrying(queue):
    job = queue.add("instagram", "/clips/a.mp4", now=NOW)
    queue.claim("instagram", now=NOW)
    queue.fail(job.id, "http 500", now=NOW)

    refreshed = queue.get(job.id)
    assert refreshed.state == PENDING and refreshed.attempts == 1
    assert queue.claim("instagram", now=NOW + 30) is None, "backoff not honoured"
    assert queue.claim("instagram", now=NOW + 61) is not None


def test_attempts_are_capped(queue):
    """A permanently broken job must stop consuming posting quota."""
    job = queue.add("instagram", "/clips/a.mp4", now=NOW)
    at = NOW
    for _ in range(3):
        queue.claim("instagram", now=at)
        queue.fail(job.id, "still broken", now=at)
        at += 3600

    final = queue.get(job.id)
    assert final.state == FAILED and final.attempts == 3
    assert queue.claim("instagram", now=at + 86_400) is None


def test_failure_records_the_error(queue):
    job = queue.add("instagram", "/clips/a.mp4", now=NOW)
    queue.fail(job.id, "invalid access token", now=NOW)
    assert "invalid access token" in queue.get(job.id).last_error


def test_completion_is_terminal(queue):
    job = queue.add("instagram", "/clips/a.mp4", now=NOW)
    queue.claim("instagram", now=NOW)
    queue.complete(job.id, "https://instagram.com/reel/x", now=NOW + 5)

    done = queue.get(job.id)
    assert done.state == DONE and done.result_url.endswith("/x")
    assert queue.claim("instagram", now=NOW + 100) is None


# ═════════════════════════════════════════════════════════════════════════════
# Guard deferral is not a failure
# ═════════════════════════════════════════════════════════════════════════════

def test_blocking_does_not_consume_an_attempt(queue):
    """A full daily cap is a scheduling fact, not a broken job - counting
    it would burn the retry budget on days the cap is simply full."""
    job = queue.add("instagram", "/clips/a.mp4", now=NOW)
    queue.claim("instagram", now=NOW)
    queue.block(job.id, "daily cap reached", retry_after_s=3600, now=NOW)

    blocked = queue.get(job.id)
    assert blocked.state == BLOCKED and blocked.attempts == 0


def test_blocked_job_returns_when_the_window_passes(queue):
    job = queue.add("instagram", "/clips/a.mp4", now=NOW)
    queue.block(job.id, "cap", retry_after_s=3600, now=NOW)
    assert queue.claim("instagram", now=NOW + 100) is None
    assert queue.claim("instagram", now=NOW + 3601).id == job.id


def test_repeated_blocking_never_fails_the_job(queue):
    job = queue.add("instagram", "/clips/a.mp4", now=NOW)
    at = NOW
    for _ in range(10):
        queue.claim("instagram", now=at)
        queue.block(job.id, "cap", retry_after_s=60, now=at)
        at += 61
    assert queue.get(job.id).state == BLOCKED
    assert queue.get(job.id).attempts == 0


# ═════════════════════════════════════════════════════════════════════════════
# Crash recovery
# ═════════════════════════════════════════════════════════════════════════════

def test_a_dead_worker_does_not_strand_the_job(queue):
    job = queue.add("instagram", "/clips/a.mp4", now=NOW)
    queue.claim("instagram", now=NOW)          # worker claims, then dies
    assert queue.claim("instagram", now=NOW + 60) is None, "double-claim"

    recovered = queue.claim("instagram", now=NOW + 1801)
    assert recovered is not None and recovered.id == job.id


def test_lease_recovery_does_not_count_as_an_attempt(queue):
    job = queue.add("instagram", "/clips/a.mp4", now=NOW)
    queue.claim("instagram", now=NOW)
    queue.claim("instagram", now=NOW + 1801)
    assert queue.get(job.id).attempts == 0


# ═════════════════════════════════════════════════════════════════════════════
# Persistence
# ═════════════════════════════════════════════════════════════════════════════

def test_state_survives_a_restart(tmp_path):
    path = str(tmp_path / "jobs.json")
    first = JobQueue(path=path)
    job = first.add("instagram", "/clips/a.mp4", caption="hello", now=NOW)
    first.claim("instagram", now=NOW)
    first.complete(job.id, "https://example/1", now=NOW + 5)

    second = JobQueue(path=path)
    restored = second.get(job.id)
    assert restored is not None
    assert restored.state == DONE and restored.caption == "hello"


def test_in_progress_survives_a_restart_then_recovers(tmp_path):
    path = str(tmp_path / "jobs.json")
    first = JobQueue(path=path, lease_seconds=1800)
    job = first.add("instagram", "/clips/a.mp4", now=NOW)
    first.claim("instagram", now=NOW)

    second = JobQueue(path=path, lease_seconds=1800)
    assert second.get(job.id).state == IN_PROGRESS
    assert second.claim("instagram", now=NOW + 1801).id == job.id


def test_corrupt_queue_file_does_not_crash(tmp_path):
    path = tmp_path / "jobs.json"
    path.write_text("}{ not json")
    queue = JobQueue(path=str(path))
    assert queue.list_jobs() == []
    assert queue.add("instagram", "/clips/a.mp4", now=NOW).state == PENDING


def test_saved_file_is_valid_json(tmp_path):
    path = tmp_path / "jobs.json"
    queue = JobQueue(path=str(path))
    queue.add("instagram", "/clips/a.mp4", now=NOW)
    with open(path) as f:
        data = json.load(f)
    assert "jobs" in data and len(data["jobs"]) == 1


def test_no_temp_files_are_left_behind(tmp_path):
    queue = JobQueue(path=str(tmp_path / "jobs.json"))
    for i in range(5):
        queue.add("instagram", f"/clips/{i}.mp4", now=NOW + i)
    leftovers = [p for p in os.listdir(tmp_path) if p.endswith(".tmp")]
    assert leftovers == []


# ═════════════════════════════════════════════════════════════════════════════
# Reporting / housekeeping
# ═════════════════════════════════════════════════════════════════════════════

def test_counts_by_state(queue):
    a = queue.add("instagram", "/clips/a.mp4", now=NOW)
    queue.add("facebook", "/clips/b.mp4", now=NOW)
    queue.add("reddit", "/clips/c.mp4", needs_approval=True, now=NOW)
    queue.complete(a.id, "url", now=NOW)

    counts = queue.counts()
    assert counts[DONE] == 1 and counts[PENDING] == 1
    assert counts[NEEDS_APPROVAL] == 1


def test_purge_drops_old_done_but_keeps_failures(queue):
    done = queue.add("instagram", "/clips/a.mp4", now=NOW)
    queue.complete(done.id, "url", now=NOW)
    broken = queue.add("facebook", "/clips/b.mp4", now=NOW)
    for _ in range(3):
        queue.fail(broken.id, "nope", now=NOW)

    removed = queue.purge_done(older_than_s=86_400, now=NOW + 200_000)
    assert removed == 1
    assert queue.get(done.id) is None
    assert queue.get(broken.id).state == FAILED, \
        "failures are the record of what needs looking at"


def test_purge_keeps_recent_done(queue):
    job = queue.add("instagram", "/clips/a.mp4", now=NOW)
    queue.complete(job.id, "url", now=NOW)
    assert queue.purge_done(older_than_s=86_400, now=NOW + 60) == 0
