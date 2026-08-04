"""
job_queue.py — Atomic, lease-based job queue for clip publishing.

Design goals:
  - Atomic writes: Ctrl-C cannot corrupt the queue file.
  - Leases: a worker that dies mid-job does not strand the job forever.
  - Attempt ceiling: a broken job stops eating quota after MAX_ATTEMPTS.
  - Blocked ≠ failed: a post deferred by publish_guard is recorded as
    'blocked', not 'failed', so it does NOT burn the retry budget.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("job_queue")

MAX_ATTEMPTS = 5
LEASE_SECONDS = 300  # 5 minutes — worker must finish or renew within this window


# ── helpers ────────────────────────────────────────────────────────────────────

def _load(path: Path) -> List[Dict]:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return []


def _save(path: Path, jobs: List[Dict]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(jobs, indent=2))
    tmp.replace(path)  # atomic on POSIX; best-effort on Windows


# ── public API ─────────────────────────────────────────────────────────────────

class JobQueue:
    """
    Usage:
        q = JobQueue(queue_path)
        job_id = q.enqueue(platform="instagram", clip_path="clip.mp4",
                           caption="great moment", tags=["gta"])
        job = q.acquire()           # returns first ready job and sets a lease
        if job:
            ok = post(job)
            q.complete(job["id"])   # removes job on success
            # or:
            q.fail(job["id"])       # increments attempts; removes when exhausted
            # or:
            q.block(job["id"], reason="cap hit")  # deferred, not failed
    """

    def __init__(self, queue_path: str | Path) -> None:
        self._path = Path(queue_path)

    # ── write ops ─────────────────────────────────────────────────────────────

    def enqueue(
        self,
        platform: str,
        clip_path: str,
        caption: str = "",
        tags: Optional[List[str]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> str:
        jobs = _load(self._path)
        job: Dict[str, Any] = {
            "id": str(uuid.uuid4()),
            "platform": platform,
            "clip_path": clip_path,
            "caption": caption,
            "tags": tags or [],
            "extra": extra or {},
            "status": "pending",  # pending | leased | blocked | done | dead
            "attempts": 0,
            "blocked_reason": "",
            "lease_until": 0,
            "created_at": time.time(),
        }
        jobs.append(job)
        _save(self._path, jobs)
        log.info("[queue] enqueued %s for %s", job["id"][:8], platform)
        return job["id"]

    def acquire(self) -> Optional[Dict]:
        """Return the first ready job and mark it leased. Returns None if empty."""
        jobs = _load(self._path)
        now = time.time()
        for job in jobs:
            if job["status"] == "pending":
                job["status"] = "leased"
                job["lease_until"] = now + LEASE_SECONDS
                _save(self._path, jobs)
                log.debug("[queue] acquired %s (%s)", job["id"][:8], job["platform"])
                return dict(job)
            # recover expired leases
            if job["status"] == "leased" and now > job["lease_until"]:
                log.warning(
                    "[queue] lease expired on %s — recovering to pending",
                    job["id"][:8]
                )
                job["status"] = "pending"
                job["lease_until"] = 0
                _save(self._path, jobs)
                return self.acquire()  # retry with recovered job
        return None

    def complete(self, job_id: str) -> None:
        """Remove job on success."""
        jobs = _load(self._path)
        jobs = [j for j in jobs if j["id"] != job_id]
        _save(self._path, jobs)
        log.info("[queue] completed %s", job_id[:8])

    def fail(self, job_id: str) -> None:
        """Increment attempt count. Mark dead when MAX_ATTEMPTS reached."""
        jobs = _load(self._path)
        for job in jobs:
            if job["id"] == job_id:
                job["attempts"] += 1
                job["lease_until"] = 0
                if job["attempts"] >= MAX_ATTEMPTS:
                    job["status"] = "dead"
                    log.error(
                        "[queue] %s marked DEAD after %d attempts (%s)",
                        job_id[:8], job["attempts"], job["platform"]
                    )
                else:
                    job["status"] = "pending"
                    log.warning(
                        "[queue] %s attempt %d/%d — will retry",
                        job_id[:8], job["attempts"], MAX_ATTEMPTS
                    )
                break
        _save(self._path, jobs)

    def block(self, job_id: str, reason: str = "") -> None:
        """
        Defer the job (publish_guard said no).
        Does NOT increment attempts — guard refusals are not failures.
        """
        jobs = _load(self._path)
        for job in jobs:
            if job["id"] == job_id:
                job["status"] = "blocked"
                job["blocked_reason"] = reason
                job["lease_until"] = 0
                log.info("[queue] %s blocked — %s", job_id[:8], reason)
                break
        _save(self._path, jobs)

    def unblock_all(self) -> int:
        """Reset all blocked jobs to pending so they retry. Returns count."""
        jobs = _load(self._path)
        count = 0
        for job in jobs:
            if job["status"] == "blocked":
                job["status"] = "pending"
                job["blocked_reason"] = ""
                count += 1
        if count:
            _save(self._path, jobs)
            log.info("[queue] unblocked %d jobs", count)
        return count

    def list_jobs(self, status: Optional[str] = None) -> List[Dict]:
        jobs = _load(self._path)
        if status:
            return [j for j in jobs if j["status"] == status]
        return jobs
