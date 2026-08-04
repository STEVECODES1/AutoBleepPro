"""
Retry-safe job queue for clip publishing.

Deliberately a JSON file rather than a database: this runs on one Windows
PC alongside the existing uploader, which already tracks its state the same
way, and adding a service to manage a handful of clips a day would be
infrastructure for its own sake.

The properties that actually matter here:

- **Atomic writes.** Temp file + os.replace, so Ctrl+C mid-write leaves the
  previous queue intact rather than a truncated file. The uploader gets
  interrupted routinely, and a corrupt queue would mean either lost work or
  duplicate posts.
- **Crash recovery.** A job claimed by a process that then died would be
  stuck in_progress forever, so claims carry a lease and expired ones
  return to pending.
- **A ceiling on attempts.** A permanently broken job must stop retrying;
  otherwise it burns daily posting quota that working jobs need.
- **Approval as a first-class state.** Manual-only platforms park in
  needs_approval and are never picked up by a worker.
"""

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Iterable, Optional

PENDING = "pending"
IN_PROGRESS = "in_progress"
DONE = "done"
FAILED = "failed"
BLOCKED = "blocked"              # guard said no; retry later, not a failure
NEEDS_APPROVAL = "needs_approval"

ACTIVE_STATES = (PENDING, IN_PROGRESS, BLOCKED)

# A claimed job whose worker vanished returns to pending after this.
DEFAULT_LEASE_SECONDS = 30 * 60
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = (60, 300, 1800)


@dataclass
class Job:
    id: str
    platform: str
    clip_path: str
    caption: str = ""
    state: str = PENDING
    attempts: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    not_before: float = 0.0      # backoff / guard deferral
    claimed_at: float = 0.0
    last_error: str = ""
    result_url: str = ""
    source_video: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        known = {k: v for k, v in (data or {}).items() if k in cls.__annotations__}
        return cls(**known)

    @property
    def is_terminal(self) -> bool:
        return self.state in (DONE, FAILED)


@dataclass
class JobQueue:
    path: str = "./clip_jobs.json"
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    lease_seconds: int = DEFAULT_LEASE_SECONDS
    backoff_seconds: tuple = DEFAULT_BACKOFF_SECONDS
    _jobs: dict = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self._load()

    # ── Persistence ──────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = {}
        jobs = raw.get("jobs", {}) if isinstance(raw, dict) else {}
        self._jobs = {
            job_id: Job.from_dict(data)
            for job_id, data in jobs.items() if isinstance(data, dict)
        }

    def _save(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        tmp = f"{self.path}.{os.getpid()}.tmp"
        payload = {"jobs": {jid: job.to_dict() for jid, job in self._jobs.items()}}
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, self.path)   # atomic on POSIX and Windows
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass

    # ── Adding work ──────────────────────────────────────────────────────

    def add(self, platform: str, clip_path: str, caption: str = "",
            source_video: str = "", needs_approval: bool = False,
            now: Optional[float] = None) -> Job:
        """Queue a clip for one platform. Deduplicated on (platform, clip).

        Re-queuing the same clip is how a duplicate post happens, so an
        existing non-terminal job for that pair is returned untouched
        rather than adding a second.
        """
        now = time.time() if now is None else now
        existing = self.find(platform, clip_path)
        if existing and not existing.is_terminal:
            return existing

        job = Job(
            id=uuid.uuid4().hex[:12],
            platform=platform,
            clip_path=clip_path,
            caption=caption,
            source_video=source_video,
            state=NEEDS_APPROVAL if needs_approval else PENDING,
            created_at=now,
            updated_at=now,
        )
        self._jobs[job.id] = job
        self._save()
        return job

    def find(self, platform: str, clip_path: str) -> Optional[Job]:
        for job in self._jobs.values():
            if job.platform == platform and job.clip_path == clip_path:
                return job
        return None

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    # ── Claiming work ────────────────────────────────────────────────────

    def _recover_expired(self, now: float) -> int:
        """Return jobs whose worker died to pending."""
        recovered = 0
        for job in self._jobs.values():
            if job.state == IN_PROGRESS and now - job.claimed_at > self.lease_seconds:
                job.state = PENDING
                job.claimed_at = 0.0
                job.last_error = "worker lease expired; requeued"
                job.updated_at = now
                recovered += 1
        if recovered:
            self._save()
        return recovered

    def ready(self, platform: Optional[str] = None,
              now: Optional[float] = None) -> list:
        """Jobs eligible to run right now, oldest first."""
        now = time.time() if now is None else now
        self._recover_expired(now)
        out = [
            job for job in self._jobs.values()
            if job.state in (PENDING, BLOCKED)
            and job.not_before <= now
            and (platform is None or job.platform == platform)
        ]
        return sorted(out, key=lambda j: j.created_at)

    def claim(self, platform: Optional[str] = None,
              now: Optional[float] = None) -> Optional[Job]:
        """Take the next eligible job and mark it in_progress."""
        now = time.time() if now is None else now
        candidates = self.ready(platform, now)
        if not candidates:
            return None
        job = candidates[0]
        job.state = IN_PROGRESS
        job.claimed_at = now
        job.updated_at = now
        self._save()
        return job

    # ── Finishing work ───────────────────────────────────────────────────

    def complete(self, job_id: str, result_url: str = "",
                 now: Optional[float] = None) -> Optional[Job]:
        now = time.time() if now is None else now
        job = self._jobs.get(job_id)
        if job is None:
            return None
        job.state = DONE
        job.result_url = result_url
        job.last_error = ""
        job.claimed_at = 0.0
        job.updated_at = now
        self._save()
        return job

    def fail(self, job_id: str, error: str,
             now: Optional[float] = None) -> Optional[Job]:
        """Record a failure; retry with backoff until the ceiling."""
        now = time.time() if now is None else now
        job = self._jobs.get(job_id)
        if job is None:
            return None
        job.attempts += 1
        job.last_error = str(error)[:500]
        job.claimed_at = 0.0
        job.updated_at = now

        if job.attempts >= self.max_attempts:
            # Stop retrying: a permanently broken job otherwise consumes
            # the daily posting quota that working jobs need.
            job.state = FAILED
        else:
            index = min(job.attempts - 1, len(self.backoff_seconds) - 1)
            job.state = PENDING
            job.not_before = now + self.backoff_seconds[index]
        self._save()
        return job

    def block(self, job_id: str, reason: str, retry_after_s: Optional[float] = None,
              now: Optional[float] = None) -> Optional[Job]:
        """The guard said no. Not a failure - no attempt is consumed.

        A daily cap is a scheduling fact, not a broken job; counting it as
        an attempt would burn the retry budget on days the cap is simply
        full.
        """
        now = time.time() if now is None else now
        job = self._jobs.get(job_id)
        if job is None:
            return None
        job.state = BLOCKED
        job.last_error = str(reason)[:500]
        job.claimed_at = 0.0
        job.updated_at = now
        job.not_before = now + float(retry_after_s or 600)
        self._save()
        return job

    def approve(self, job_id: str, now: Optional[float] = None) -> Optional[Job]:
        """A human authorised a manual-approval job."""
        now = time.time() if now is None else now
        job = self._jobs.get(job_id)
        if job is None or job.state != NEEDS_APPROVAL:
            return None
        job.state = PENDING
        job.updated_at = now
        self._save()
        return job

    # ── Reporting ────────────────────────────────────────────────────────

    def counts(self) -> dict:
        out: dict = {}
        for job in self._jobs.values():
            out[job.state] = out.get(job.state, 0) + 1
        return out

    def list_jobs(self, states: Optional[Iterable[str]] = None) -> list:
        wanted = set(states) if states else None
        jobs = [j for j in self._jobs.values() if wanted is None or j.state in wanted]
        return sorted(jobs, key=lambda j: j.created_at)

    def purge_done(self, older_than_s: float = 7 * 86400,
                   now: Optional[float] = None) -> int:
        """Drop old finished jobs. Failures are kept - they're the record
        of what needs looking at."""
        now = time.time() if now is None else now
        doomed = [
            jid for jid, job in self._jobs.items()
            if job.state == DONE and now - job.updated_at > older_than_s
        ]
        for jid in doomed:
            del self._jobs[jid]
        if doomed:
            self._save()
        return len(doomed)
