"""
Clips that cannot post yet are KEPT, not dropped.

THE BUG THIS EXISTS TO FIX
--------------------------
Ten clips come out of a stream within a few minutes of each other, and
Instagram is spaced at one post every twenty-five minutes. The first clip
posted. The other nine asked the guard, were told "posted 2 min ago,
minimum is 25", and were thrown away - the function printed the reason and
returned False, and nothing ever looked at that clip again. A whole day's
output reduced to one Reel, with the spacing rule doing exactly what it
was told and the clips vanishing anyway.

Spacing is a WHEN, not a NO. So a clip the guard defers is written to the
job queue with its caption, and drained later when the wait has passed.
The queue already knew how to say this - `block()` records a guard refusal
without consuming a retry, and carries the time to come back - it just was
never wired to anything.

WHAT IS NOT QUEUED
------------------
Only timing gets deferred. A platform that is disabled, manual-only,
missing credentials, or sitting behind an open circuit breaker has no
retry time to wait for, and queueing it would build a pile of clips that
can never go out and would then all fire at once the moment it was fixed.
Those are skipped with the guard's own reason, exactly as before.
"""

from __future__ import annotations

import os
import sys
from typing import Optional

# auto_uploader/ is the import root for publishers/ and publish_guard.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Platforms that can carry a video clip. Reddit and X take links, which
# is a different path (announce_to_platforms) with a different cadence.
CLIP_PLATFORMS = ("instagram", "facebook")

# A blocked clip is worth keeping for about a day. Past that the stream it
# came from is stale and posting it is worse than not.
MAX_DEFERRED_AGE_S = 36 * 3600


def _queue(posting: dict):
    from job_queue import JobQueue

    return JobQueue(path=(posting or {}).get("queue_path") or "./clip_jobs.json")


def _publisher(platform: str, config: dict):
    from utils.social_promoter import _publisher_for

    return _publisher_for(platform, config or {})


def caption_for(platform: str, video_path: str, fallback: str,
                config: dict) -> str:
    """The caption this platform posts with.

    Instagram has a studied template that matches how the account already
    writes; Facebook falls back to it rather than inventing a second
    voice, because the same clip reading two different ways across two
    Pages is what looks automated.
    """
    from utils.social_promoter import build_caption

    settings = (config or {}).get(platform, {}) or {}
    template = settings.get("caption_template", "")
    if not template:
        template = ((config or {}).get("instagram", {}) or {}).get(
            "caption_template", "")
    return build_caption(template, video_path) or fallback


def publish(platform: str, video_path: str, caption: str,
            config: dict, dry_run: bool = False) -> bool:
    """Actually post one clip. No guard, no queue - callers do that."""
    if not os.path.isfile(video_path):
        print(f"[Clips] {platform}: the clip is gone: {video_path}")
        return False

    publisher = _publisher(platform, config)
    if publisher is None or not getattr(publisher, "supports_reels", False):
        return False
    ready = getattr(publisher, "ready", None)
    if ready is not None and not ready():
        return False

    if dry_run:
        print(f"[Clips] {platform}: WOULD post "
              f"{os.path.basename(video_path)} as a Reel")
        return True

    from utils.social_promoter import _vertical_copy

    settings = (config or {}).get(platform, {}) or {}
    upload_path, temp = _vertical_copy(video_path, settings,
                                       (config or {}).get("clips", {}) or {})
    print(f"[Clips] {platform}: uploading "
          f"{os.path.basename(video_path)} as a Reel...")
    try:
        ok = bool(publisher.post_reel_from_file(
            upload_path, caption,
            share_to_feed=bool(settings.get("share_to_feed", True))))
    except Exception as exc:
        ok = False
        print(f"[Clips] {platform}: Reel upload raised {exc}")
    finally:
        if temp:
            try:
                os.remove(temp)
            except OSError:
                pass
    return ok


def offer(posting: dict, config: dict, video_path: str,
          fallback_caption: str = "", platforms=CLIP_PLATFORMS,
          dry_run: bool = False) -> dict:
    """Post one clip everywhere it can go; defer where it cannot yet.

    Returns {platform: "posted" | "queued" | "skipped: reason"}.
    """
    from publish_guard import PublishGuard

    outcome: dict = {}
    if not posting or not video_path:
        return outcome

    guard = PublishGuard(posting, posting.get("state_path"))
    queue = _queue(posting)

    for platform in platforms:
        already = queue.find(platform, video_path)
        if already is not None and already.state == "done":
            # This clip has been through here before - a re-run of the
            # same file must not post it a second time.
            outcome[platform] = "skipped: already posted"
            continue

        caption = caption_for(platform, video_path, fallback_caption, config)
        decision = guard.check(platform)

        if not decision and decision.retry_after_s is None:
            # Not a timing problem - disabled, manual-only, killed, or
            # breakered. Nothing to wait for, so nothing to queue: a pile
            # of these would all fire at once the day it was fixed.
            outcome[platform] = f"skipped: {decision.reason}"
            print(f"[Clips] {platform}: skipped - {decision.reason}")
            continue

        publisher = _publisher(platform, config)
        ready = getattr(publisher, "ready", None) if publisher else None
        if publisher is None or (ready is not None and not ready()):
            outcome[platform] = "skipped: not configured"
            print(f"[Clips] {platform}: skipped - not configured yet.")
            continue

        # Recorded before the attempt, so the queue is also the ledger of
        # what has been posted - which is what stops a re-run of the same
        # file posting it twice.
        job_id = queue.enqueue(platform, video_path, caption)

        if not decision:
            queue.block(job_id, decision.reason, decision.retry_after_s)
            outcome[platform] = "queued"
            print(f"[Clips] {platform}: {decision.reason} - queued, back in "
                  f"{decision.retry_after_s / 60:.0f} min.")
            continue

        ok = publish(platform, video_path, caption, config, dry_run)
        if dry_run:
            queue.block(job_id, "dry run", 300)
            outcome[platform] = "posted"
            continue
        guard.record_result(platform, ok)
        if ok:
            queue.complete(job_id)
            outcome[platform] = "posted"
            print(f"[Clips] {platform}: posted a Reel.")
        else:
            # Worth one more go later; the queue's attempt ceiling stops
            # it becoming a loop.
            queue.fail(job_id, "first attempt failed")
            outcome[platform] = "queued"
            print(f"[Clips] {platform}: Reel failed - queued to retry.")
    return outcome


def drain(posting: dict, config: dict, limit: int = 0,
          dry_run: bool = False, quiet: bool = False) -> dict:
    """Post whatever the queue is now allowed to post.

    Called on every pass of the watcher, so a clip deferred at 14:02 goes
    out the moment its wait is up rather than when someone remembers.

    Returns {platform: posted_count}.
    """
    from publish_guard import PublishGuard

    posted: dict = {}
    if not posting:
        return posted

    queue = _queue(posting)
    guard = PublishGuard(posting, posting.get("state_path"))
    import time

    now = time.time()
    sent = 0
    while True:
        job = queue.claim()
        if job is None:
            break

        if now - (job.created_at or now) > MAX_DEFERRED_AGE_S:
            queue.abandon(job.id, "too old to be worth posting", now=now)
            if not quiet:
                print(f"[Clips] {job.platform}: dropped "
                      f"{os.path.basename(job.clip_path)} - over a day old.")
            continue

        if not os.path.isfile(job.clip_path):
            queue.abandon(job.id, "the clip is no longer on disk", now=now)
            continue

        decision = guard.check(job.platform)
        if not decision:
            # Still not allowed. Put it back with its new wait rather
            # than burning an attempt on a scheduling fact.
            queue.block(job.id, decision.reason, decision.retry_after_s)
            if not quiet:
                print(f"[Clips] {job.platform}: still waiting - {decision.reason}")
            continue

        ok = publish(job.platform, job.clip_path, job.caption, config, dry_run)
        if dry_run:
            # Put it back with a real wait: a zero would make it eligible
            # again on the next claim and loop this forever.
            queue.block(job.id, "dry run", 300)
            sent += 1
            if limit and sent >= limit:
                break
            continue
        guard.record_result(job.platform, ok)
        if ok:
            queue.complete(job.id)
            posted[job.platform] = posted.get(job.platform, 0) + 1
            print(f"[Clips] {job.platform}: posted a queued Reel "
                  f"({os.path.basename(job.clip_path)}).")
        else:
            queue.fail(job.id, "Reel upload failed")

        sent += 1
        if limit and sent >= limit:
            break
    return posted


def summary(posting: dict) -> str:
    """One line on what is waiting, for the status output."""
    if not posting:
        return ""
    counts = _queue(posting).counts()
    waiting = sum(counts.get(state, 0) for state in ("pending", "blocked"))
    if not waiting:
        return "No clips waiting to post."
    return (f"{waiting} clip(s) waiting to post "
            f"(done {counts.get('done', 0)}, given up on "
            f"{counts.get('failed', 0)}).")
