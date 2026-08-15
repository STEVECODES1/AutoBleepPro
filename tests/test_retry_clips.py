"""Clips that gave up because the ACCOUNT was broken, not the clip.

Forty-three clips exhausted three attempts each against an expired
Facebook token and an open Instagram breaker. Every one of them would
have posted fine the day after the token was fixed, and nothing in the
tool could put them back.
"""
from __future__ import annotations

import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from job_queue import (  # noqa: E402
    JobQueue, PENDING, DONE, FAILED, BLOCKED)


@pytest.fixture
def queue(tmp_path):
    return JobQueue(path=str(tmp_path / "jobs.json"))


def _failed(queue, platform="instagram", error="token expired"):
    job = queue.add(platform, "/clips/a.mp4", "caption")
    for _ in range(queue.max_attempts):
        queue.fail(job.id, error)
    assert queue.get(job.id).state == FAILED
    return job


def test_a_given_up_clip_gets_its_budget_back(queue):
    job = _failed(queue)
    revived = queue.retry(job.id)
    assert revived.state == PENDING
    assert revived.attempts == 0


def test_it_becomes_claimable_again(queue):
    job = _failed(queue)
    queue.retry(job.id)
    assert queue.claim("instagram") is not None


def test_backoff_is_cleared_so_it_does_not_wait(queue):
    job = _failed(queue)
    assert queue.retry(job.id).not_before == 0.0


def test_a_finished_clip_is_never_revived(queue):
    """Posting the same clip twice is the one mistake here with no undo."""
    job = queue.add("instagram", "/clips/a.mp4", "c")
    queue.complete(job.id, "https://example.com/1")
    assert queue.get(job.id).state == DONE
    assert queue.retry(job.id) is None
    assert queue.get(job.id).state == DONE


def test_a_pending_clip_is_left_alone(queue):
    job = queue.add("instagram", "/clips/a.mp4", "c")
    assert queue.retry(job.id) is None


def test_a_blocked_clip_is_left_alone(queue):
    """Blocked is the guard metering, not a failure - it returns on its own."""
    job = queue.add("instagram", "/clips/a.mp4", "c")
    queue.block(job.id, "daily cap reached", retry_after_s=600)
    assert queue.get(job.id).state == BLOCKED
    assert queue.retry(job.id) is None


def test_an_unknown_job_is_not_a_crash(queue):
    assert queue.retry("nope") is None


def test_it_survives_a_restart(tmp_path):
    path = str(tmp_path / "jobs.json")
    first = JobQueue(path=path)
    job = _failed(first)
    first.retry(job.id)
    assert JobQueue(path=path).get(job.id).state == PENDING


def test_reviving_one_leaves_the_others_alone(queue):
    a = _failed(queue, "instagram")
    b = _failed(queue, "facebook")
    queue.retry(a.id)
    assert queue.get(a.id).state == PENDING
    assert queue.get(b.id).state == FAILED


def test_a_revived_clip_can_fail_again_properly(queue):
    """The ceiling must still hold the second time around."""
    job = _failed(queue)
    queue.retry(job.id)
    for _ in range(queue.max_attempts):
        queue.fail(job.id, "still broken")
    assert queue.get(job.id).state == FAILED


# ── the age limit must match the queue's own ─────────────────────────

def test_the_retry_window_matches_the_limit_the_drain_applies():
    """A retry wider than the drain's limit is a promise the next run
    breaks: it reported 32 revived and posted none of them."""
    from utils.clip_queue import MAX_DEFERRED_AGE_S

    old = _Job("/clips/a.mp4", age_s=MAX_DEFERRED_AGE_S + 60)
    eligible, gone, stale = JobQueue.sort_retryable(
        [old], MAX_DEFERRED_AGE_S, exists=lambda p: True)
    assert stale == [old] and eligible == []


class _Job:
    def __init__(self, clip_path, age_s=0.0):
        self.clip_path = clip_path
        self.created_at = time.time() - age_s
        self.id = clip_path


def test_a_fresh_clip_is_worth_retrying():
    fresh = _Job("/clips/a.mp4", age_s=60)
    eligible, gone, stale = JobQueue.sort_retryable(
        [fresh], 36 * 3600, exists=lambda p: True)
    assert eligible == [fresh]


def test_a_deleted_clip_is_never_retried():
    """The cleanup removes posted clips; there is nothing left to post."""
    missing = _Job("/clips/gone.mp4", age_s=60)
    eligible, gone, stale = JobQueue.sort_retryable(
        [missing], 36 * 3600, exists=lambda p: False)
    assert gone == [missing] and eligible == []


def test_a_missing_file_beats_the_age_check():
    """Reported as gone, not as old - the reason a person sees matters."""
    both = _Job("/clips/gone.mp4", age_s=99 * 3600)
    eligible, gone, stale = JobQueue.sort_retryable(
        [both], 36 * 3600, exists=lambda p: False)
    assert gone == [both] and stale == []


def test_a_job_with_no_recorded_age_is_given_the_benefit():
    unknown = _Job("/clips/a.mp4")
    unknown.created_at = 0.0
    eligible, gone, stale = JobQueue.sort_retryable(
        [unknown], 36 * 3600, exists=lambda p: True)
    assert eligible == [unknown]


def test_nothing_in_means_nothing_out():
    assert JobQueue.sort_retryable([], 36 * 3600) == ([], [], [])
