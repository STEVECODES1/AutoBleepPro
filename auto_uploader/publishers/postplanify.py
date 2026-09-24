"""
publishers/postplanify.py - post one clip to every account connected on
PostPlanify (postplanify.com), through its REST API.

WHY THIS EXISTS
    upload_post was the only fan-out route, and its free plan is 10 uploads
    a MONTH - which is why its daily_cap had to be 1. One clip a day across
    Instagram, TikTok and X is not what a stream's worth of clips needs.

    PostPlanify is a paid plan with no monthly posting quota. Its own
    limits are per connected account: 6 posts an hour and 25 a day
    (counted on scheduled publish time). The publish_guard cap for this
    route is kept well under that.

HOW A POST WORKS
    1. POST /media/upload           - the clip, multipart. Returns a media id.
    2. POST /posts, once per account - schedules it a short lead time ahead
                                       (the API only accepts future times).
    3. GET  /posts/{id}             - polled until PUBLISHED, an ERROR log,
                                       or the timeout. A post still
                                       SCHEDULED at the timeout is reported
                                       as accepted, not failed: PostPlanify
                                       owns publishing from that point.

    The result has the same shape upload_post returns -
    {"results": [{"platform": <project name>, "success": bool, ...}]} - so
    clip_queue decides what this call covered from what actually happened,
    and the per-platform publishers are skipped only for those.

WHICH ACCOUNTS
    Every connected account whose platform this project posts clips to
    (instagram, tiktok, x, facebook, youtube_shorts), unless
    posting.platforms.<name>.enabled is explicitly false. Connecting a new
    account on the PostPlanify dashboard is all it takes to add it; there
    is no list to keep in step here.

SETUP
    auto_uploader/.env:   POSTPLANIFY_API_KEY=sk_live_...
    config.json:          posting.platforms.postplanify {enabled, daily_cap,
                          min_minutes_between}
    API reference:        https://postplanify.com/docs
                          (OpenAPI: https://postplanify.com/openapi.yaml)
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .errors import NotConfigured, PermanentlyRejected

log = logging.getLogger("publisher.postplanify")

try:
    import requests
    _REQUESTS_OK = True
except ImportError:  # pragma: no cover - requests is a hard dependency
    requests = None  # type: ignore
    _REQUESTS_OK = False

API_BASE = os.environ.get("POSTPLANIFY_API_URL",
                          "https://api.postplanify.com/api/v1").rstrip("/")

# PostPlanify's platform names -> this project's.
PLATFORM_TO_PROJECT = {
    "INSTAGRAM": "instagram",
    "TIKTOK": "tiktok",
    "X": "x",
    "FACEBOOK": "facebook",
    "YOUTUBE": "youtube_shorts",
}

# The API refuses anything bigger. A clip is a few MB; a file this size
# is a full stream handed here by mistake, and no retry changes that.
MAX_UPLOAD_BYTES = 100 * 1024 * 1024

# X's own limit on a normal account. PostPlanify's docs say "no strict
# limit" for X, which is only true for Premium - past this the post fails
# at publish time, after this code has already reported it scheduled.
X_MAX_CHARS = 280
CAPTION_LIMITS = {"instagram": 2200, "tiktok": 2200, "x": X_MAX_CHARS}

# Seconds ahead to schedule. The API requires a future time; a minute is
# enough slack for clock drift without leaving the clip waiting.
DEFAULT_LEAD_S = 60
# How long to watch for the real outcome after the scheduled time.
DEFAULT_POLL_TIMEOUT_S = 240
POLL_INTERVAL_S = 15

YOUTUBE_GAMING_CATEGORY = "20"


def platforms_reached(result: Optional[Dict[str, Any]]) -> set:
    """Project platform names this call actually got to. Empty - never a
    guess - when there is nothing real to read (a dry run, a malformed
    result), matching publishers.upload_post.platforms_reached."""
    if not isinstance(result, dict):
        return set()
    return {
        entry.get("platform") for entry in result.get("results") or ()
        if isinstance(entry, dict) and entry.get("success")
        and entry.get("platform")
    }


def fit_caption(platform: str, caption: str) -> str:
    """The caption trimmed to what this platform will accept.

    Whole lines are dropped from the end rather than cutting mid-word, so
    what goes first is the hashtag block, then any extra lines - the title
    line is the last thing to be shortened.
    """
    caption = (caption or "").strip()
    limit = CAPTION_LIMITS.get(platform)
    if not limit or len(caption) <= limit:
        return caption
    lines = caption.splitlines()
    while len(lines) > 1 and len("\n".join(lines).strip()) > limit:
        last = lines[-1]
        # Shed hashtags one at a time before giving up the whole line.
        words = last.split()
        if len(words) > 1 and all(w.startswith("#") for w in words):
            lines[-1] = " ".join(words[:-1])
        else:
            lines.pop()
    text = "\n".join(lines).strip()
    if len(text) > limit:
        text = text[:limit - 1].rsplit(" ", 1)[0].rstrip() + "…"
    return text


def _enabled(posting: Dict[str, Any], name: str) -> bool:
    """Off only when this project says so explicitly. A platform with no
    block under posting.platforms (tiktok, today) is not a refusal - the
    account being connected on PostPlanify is the opt-in."""
    block = ((posting or {}).get("platforms", {}) or {}).get(name)
    if not isinstance(block, dict):
        return True
    return block.get("enabled", True) is not False


class PostPlanifyPublisher:
    """Posts one clip to every connected PostPlanify account."""

    supports_link_posts = False
    supports_reels = True

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg or {}
        self._key = os.environ.get("POSTPLANIFY_API_KEY", "").strip()
        settings = self._cfg.get("postplanify", {}) or {}
        self._lead_s = int(settings.get("lead_seconds", DEFAULT_LEAD_S))
        self._poll_timeout_s = int(settings.get("poll_timeout_seconds",
                                                DEFAULT_POLL_TIMEOUT_S))
        self._tiktok_privacy = settings.get("tiktok_privacy",
                                            "PUBLIC_TO_EVERYONE")
        self._youtube_category = str(settings.get("youtube_category",
                                                  YOUTUBE_GAMING_CATEGORY))

    # ── plumbing ────────────────────────────────────────────────────────

    def ready(self) -> bool:
        if not _REQUESTS_OK:
            log.error("postplanify: 'requests' not installed")
            return False
        if not self._key:
            log.error("postplanify: POSTPLANIFY_API_KEY is not set in "
                      "auto_uploader/.env (dashboard -> API Keys).")
            return False
        return True

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._key}"}

    def _check(self, response, what: str) -> dict:
        """The parsed body of a successful call; raises on anything else.

        401 and 403 are configuration - a bad key, a lapsed subscription -
        and must not be counted against the circuit breaker.
        """
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code in (401, 403):
            message = ((body.get("error") or {}).get("message")
                       or f"HTTP {response.status_code}")
            raise NotConfigured(f"PostPlanify refused {what}: {message}")
        if not response.ok or not body.get("ok", False):
            error = body.get("error") or {}
            raise RuntimeError(
                f"PostPlanify {what} failed: HTTP {response.status_code} "
                f"{error.get('code', '')} {error.get('message', '')}".strip())
        return body

    def accounts(self) -> List[dict]:
        """Connected accounts this project would post a clip to."""
        response = requests.get(f"{API_BASE}/social-accounts",
                                headers=self._headers(), timeout=30)
        body = self._check(response, "listing social accounts")
        posting = self._cfg.get("posting", {}) or {}
        wanted = []
        for account in body.get("data") or ():
            name = PLATFORM_TO_PROJECT.get(str(account.get("platform", "")))
            if name and _enabled(posting, name):
                wanted.append(dict(account, project_platform=name))
        return wanted

    def _upload(self, video_path: str) -> str:
        with open(video_path, "rb") as handle:
            response = requests.post(
                f"{API_BASE}/media/upload", headers=self._headers(),
                files={"file": (os.path.basename(video_path), handle,
                                "video/mp4")},
                timeout=600)
        body = self._check(response, "the media upload")
        media_id = (body.get("data") or {}).get("id")
        if not media_id:
            raise RuntimeError("PostPlanify accepted the upload but returned "
                               "no media id")
        return media_id

    def _schedule(self, account: dict, media_id: str, caption: str,
                  when: str) -> dict:
        name = account["project_platform"]
        payload: Dict[str, Any] = {
            "workspaceId": account.get("workspaceId"),
            "socialAccountId": account.get("id"),
            "caption": fit_caption(name, caption),
            "scheduledAt": when,
            "mediaIds": [media_id],
        }
        if name == "tiktok":
            payload["tiktokParams"] = {"privacyLevel": self._tiktok_privacy}
        if name == "youtube_shorts":
            title = (caption or "").strip().splitlines()[0][:100] \
                if (caption or "").strip() else "Clip"
            payload["youtubeParams"] = {"title": title,
                                        "categoryId": self._youtube_category}
        response = requests.post(f"{API_BASE}/posts", headers=self._headers(),
                                 json=payload, timeout=60)
        return self._check(response, f"scheduling the {name} post")["data"]

    def _status(self, post_id: str) -> Optional[dict]:
        try:
            response = requests.get(f"{API_BASE}/posts/{post_id}",
                                    headers=self._headers(), timeout=30)
            return self._check(response, "reading a post").get("data")
        except NotConfigured:
            raise
        except Exception as exc:
            log.warning("postplanify: could not read post %s (%s)",
                        post_id, exc)
            return None

    @staticmethod
    def _error_in(post: dict) -> str:
        for entry in post.get("logs") or ():
            if isinstance(entry, dict) and entry.get("type") == "ERROR":
                return entry.get("message") or "publish error"
        return ""

    def _await(self, pending: Dict[str, dict], deadline: float) -> None:
        """Poll every scheduled post until each has an outcome or time is
        up. Fills in `success`/`status`/`error` on each entry in place."""
        while pending and time.time() < deadline:
            time.sleep(POLL_INTERVAL_S)
            for post_id in list(pending):
                post = self._status(post_id)
                if not post:
                    continue
                entry = pending[post_id]
                entry["status"] = post.get("status")
                error = self._error_in(post)
                if error:
                    entry.update(success=False, error=error)
                    del pending[post_id]
                elif post.get("status") == "PUBLISHED":
                    entry.update(success=True, published=True)
                    del pending[post_id]
                elif post.get("status") == "CANCELLED":
                    entry.update(success=False, error="cancelled")
                    del pending[post_id]
        for entry in pending.values():
            # Still queued at PostPlanify. Accepted, not confirmed - said
            # plainly in the log so a later failure there can be traced.
            entry.update(success=True, published=False)
            log.info("postplanify: %s post %s still scheduled after the "
                     "wait - PostPlanify will publish it.",
                     entry["platform"], entry.get("post_id"))

    # ── the call clip_queue makes ───────────────────────────────────────

    def post_clip(self, video_path: str, caption: str = "",
                  dry_run: bool = False) -> Optional[Dict[str, Any]]:
        if not os.path.isfile(video_path):
            log.error("postplanify: no such file: %s", video_path)
            return None
        if os.path.getsize(video_path) > MAX_UPLOAD_BYTES:
            raise PermanentlyRejected(
                f"{os.path.basename(video_path)} is over PostPlanify's "
                f"100 MB upload limit")

        if dry_run:
            log.info("[postplanify] WOULD POST %s to every connected account",
                     os.path.basename(video_path))
            return {"dry_run": True}

        accounts = self.accounts()
        if not accounts:
            raise NotConfigured(
                "no PostPlanify account this project posts clips to is "
                "connected - connect Instagram/TikTok/X/Facebook/YouTube on "
                "the PostPlanify dashboard")

        media_id = self._upload(video_path)
        when_dt = datetime.now(timezone.utc) + timedelta(seconds=self._lead_s)
        when = when_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        results: List[dict] = []
        pending: Dict[str, dict] = {}
        for account in accounts:
            entry = {"platform": account["project_platform"],
                     "account": account.get("accountName"),
                     "success": False}
            try:
                post = self._schedule(account, media_id, caption, when)
                entry["post_id"] = post.get("id")
                entry["status"] = post.get("status")
                pending[post.get("id")] = entry
            except NotConfigured:
                raise
            except Exception as exc:
                # One account refusing (a 429 on its hourly limit, most
                # likely) must not cost the others their post.
                entry["error"] = str(exc)
                log.warning("postplanify: %s (%s): %s", entry["platform"],
                            entry["account"], exc)
            results.append(entry)

        log.info("postplanify: %s scheduled for %s on %s",
                 os.path.basename(video_path), when,
                 ", ".join(f"{e['platform']} ({e['account']})"
                           for e in results if e.get("post_id")) or "nothing")
        self._await(pending, when_dt.timestamp() + self._poll_timeout_s)

        for entry in results:
            log.info("postplanify: %s -> %s%s", entry["platform"],
                     "ok" if entry["success"] else "FAILED",
                     f" ({entry['error']})" if entry.get("error") else "")
        return {"media_id": media_id, "scheduled_at": when,
                "results": results}
