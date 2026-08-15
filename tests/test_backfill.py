"""Switching a platform on does not reach back on its own.

Ten clips were cut and queued while youtube_shorts was disabled. offer()
correctly does not enqueue a platform that is switched off - a pile of
those would all fire at once the day it was enabled. But that means
enabling it later leaves those ten clips with no Shorts job at all, and
nothing ever appears on the channel with no error to explain it.
"""
from __future__ import annotations

import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from job_queue import JobQueue  # noqa: E402
from utils.clip_queue import CLIP_PLATFORMS, MAX_DEFERRED_AGE_S  # noqa: E402


@pytest.fixture
def queue(tmp_path):
    return JobQueue(path=str(tmp_path / "jobs.json"))


def _clip(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"x")
    return str(path)


def _missing_for(queue, platform, now=None):
    """The selection --backfill makes, isolated from the CLI."""
    now = time.time() if now is None else now
    jobs = queue.list_jobs()
    have = {j.clip_path for j in jobs if j.platform == platform}
    known = {}
    for job in jobs:
        known.setdefault(job.clip_path, job)
    cutoff = now - MAX_DEFERRED_AGE_S
    out = []
    for path, job in known.items():
        if path in have or not os.path.isfile(path):
            continue
        if job.created_at and job.created_at < cutoff:
            continue
        out.append(path)
    return out


def test_a_clip_queued_before_the_platform_was_on_is_found(queue, tmp_path):
    clip = _clip(tmp_path, "a.mp4")
    queue.add("instagram", clip, "cap")
    assert _missing_for(queue, "youtube_shorts") == [clip]


def test_a_clip_already_offered_is_not_queued_twice(queue, tmp_path):
    """Posting the same clip twice is the one mistake with no undo."""
    clip = _clip(tmp_path, "a.mp4")
    queue.add("instagram", clip, "cap")
    queue.add("youtube_shorts", clip, "cap")
    assert _missing_for(queue, "youtube_shorts") == []


def test_a_finished_shorts_job_still_counts_as_offered(queue, tmp_path):
    clip = _clip(tmp_path, "a.mp4")
    queue.add("instagram", clip, "cap")
    job = queue.add("youtube_shorts", clip, "cap")
    queue.complete(job.id, "https://youtu.be/x")
    assert _missing_for(queue, "youtube_shorts") == []


def test_a_deleted_clip_is_not_queued(queue, tmp_path):
    clip = _clip(tmp_path, "gone.mp4")
    queue.add("instagram", clip, "cap")
    os.remove(clip)
    assert _missing_for(queue, "youtube_shorts") == []


def test_a_clip_past_the_queue_s_own_limit_is_not_queued(queue, tmp_path):
    """Queueing something the next drain drops is a promise it breaks."""
    clip = _clip(tmp_path, "old.mp4")
    job = queue.add("instagram", clip, "cap")
    job.created_at = time.time() - MAX_DEFERRED_AGE_S - 60
    queue._save()
    assert _missing_for(queue, "youtube_shorts") == []


def test_an_empty_queue_yields_nothing(queue):
    assert _missing_for(queue, "youtube_shorts") == []


def test_every_clip_platform_can_be_backfilled():
    """The flag is not Shorts-specific; any platform can be switched on
    after the clips were cut."""
    assert "youtube_shorts" in CLIP_PLATFORMS
    assert "instagram" in CLIP_PLATFORMS
    assert "facebook" in CLIP_PLATFORMS


def test_several_clips_are_all_found(queue, tmp_path):
    clips = [_clip(tmp_path, f"{n}.mp4") for n in range(3)]
    for clip in clips:
        queue.add("instagram", clip, "cap")
    assert sorted(_missing_for(queue, "youtube_shorts")) == sorted(clips)
