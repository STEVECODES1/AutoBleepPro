"""10-09: one Reel went up four times, and a clip re-offered after a failed
Rumble upload collected six jobs per platform. A posted clip stays posted."""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from job_queue import DONE, FAILED, IN_PROGRESS, JobQueue  # noqa: E402


def test_a_stale_holder_cannot_bring_a_posted_clip_back(tmp_path):
    path = str(tmp_path / "jobs.json")
    poster, drain = JobQueue(path), JobQueue(path)
    job_id = poster.enqueue("instagram", "clips/A - Clip 01.mp4")
    drain._merge_from_disk()                       # the drain has it as pending
    job = poster.claim()
    poster.complete(job.id, "https://ig/1")        # posted
    drain.block(job_id, "instagram daily cap reached", 600)   # stale view
    assert JobQueue(path).get(job_id).state == DONE


def test_a_deliberate_retry_of_a_failed_job_still_goes_through(tmp_path):
    path = str(tmp_path / "jobs.json")
    q = JobQueue(path)
    job_id = q.enqueue("facebook", "clips/B - Clip 01.mp4")
    q.abandon(job_id, "too big")
    q.retry(job_id)
    assert JobQueue(path).get(job_id).state not in (DONE, FAILED)


def test_the_same_clip_under_another_folder_is_the_same_job(tmp_path):
    q = JobQueue(str(tmp_path / "jobs.json"))
    first = q.enqueue("youtube_shorts", r"watch_folder\S 18 48 - Clip 08.mp4")
    q.get(first).clip_path = r"clip_queue_files\S 18 48 - Clip 08.mp4"
    q._save()
    again = q.enqueue("youtube_shorts", r"watch_folder\S 18 48 - Clip 08.mp4")
    assert again == first


def test_a_second_job_for_a_posted_clip_is_dropped_not_posted(tmp_path):
    q = JobQueue(str(tmp_path / "jobs.json"))
    a = q.enqueue("instagram", r"clip_queue_files\S Live 04 - Clip 01.mp4")
    q.complete(q.claim().id, "https://ig/1")
    # An old duplicate, the way 10-09 left them.
    b = q.add("instagram", r"watch_folder\S Live 04 - Clip 01.mp4").id
    q.get(b).clip_path = r"other\S Live 04 - Clip 01.mp4"
    q._save()
    assert q.claim() is None
    got = JobQueue(q.path).get(b)
    assert got.state == FAILED and a in got.last_error


def test_other_clips_still_post(tmp_path):
    q = JobQueue(str(tmp_path / "jobs.json"))
    q.enqueue("instagram", "a/S - Clip 01.mp4")
    q.complete(q.claim().id)
    q.enqueue("instagram", "a/S - Clip 02.mp4")
    assert q.claim().clip_path.endswith("Clip 02.mp4")
