"""
post_clip_to_upload_post, the Upload-Post bridge: one API call that posts
a clip to every connected short-form platform (TikTok, Instagram, YouTube,
Facebook, X, …) at once — no per-platform OAuth, no scraping, no Zernio.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

from .errors import NotConfigured, PermanentlyRejected, is_permanent_rejection

log = logging.getLogger("publisher.upload_post")

try:
    from upload_post import UploadPostClient
    _UP_CLIENT_OK = True
except ImportError:
    _UP_CLIENT_OK = False
    log.info(
        "upload_post: optional 'upload-post' SDK not installed — using the "
        "REST route. Install it with: pip install upload-post")

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False
    log.warning("upload_post: 'requests' not installed — pip install requests")

UPLOAD_POST_API = "https://api.upload-post.com/api/upload"
UPLOAD_POST_STATUS_API = "https://api.upload-post.com/api/uploadposts/status"
_STATUS_PATH = "/api/uploadposts/status"
_POLL_INTERVAL = 10
_POLL_TIMEOUT = 600

# Per docs.upload-post.com/api/upload-status. Reaching one of these means
# the job is done deciding, one way or another, for every platform in it
# - nothing left to poll for.
_TERMINAL_JOB_STATUSES = frozenset({"completed", "failed", "not_found"})

# Platforms Upload-Post can reach, and the alias it wants for each.
#
# MODULE level, and read as a plain name inside the class below - not as
# self.PLATFORM_ALIASES, which is what three call sites did. Python
# resolves an attribute on the instance and then the class, never the
# module, so every one of them raised
#
#   'UploadPostPublisher' object has no attribute 'PLATFORM_ALIASES'
#
# on the first clip it was asked to post. The queue counted that as a
# failed post, and after three the circuit breaker opened:
#
#   [Clips] upload_post: skipped - circuit breaker open for upload_post:
#           3 consecutive failures.
#
# So the route that had just been wired into the clip pipeline never
# posted anything and then switched itself off for an hour.
PLATFORM_ALIASES = {
    "instagram": "instagram",
    "facebook": "facebook",
    "tiktok": "tiktok",
    # "x", not "twitter". Upload-Post refused every call that named
    # "twitter" - "Invalid platforms: ['twitter']" - and a refused call
    # posts to NONE of its platforms, so TikTok and Instagram went down
    # with it on every clip. Its own SDK (2.13) lists "x".
    "x": "x",
    "youtube_shorts": "youtube",
    "youtube": "youtube",
    "threads": "threads",
    "pinterest": "pinterest",
    "reddit": "reddit",
    "linkedin": "linkedin",
    "bluesky": "bluesky",
    "discord": "discord",
    "telegram": "telegram",
}

_THIS_PROJECT_PLATFORMS = {
    "instagram", "facebook", "tiktok", "x", "youtube_shorts",
}

# The reverse of PLATFORM_ALIASES, restricted to names this project
# actually uses - "youtube" alone is ambiguous (both "youtube" and
# "youtube_shorts" alias to it) and every other extra name in
# PLATFORM_ALIASES (threads, pinterest, ...) is not a platform this
# project posts to at all, so neither belongs in a mapping this project
# reads its own results back through.
_ALIAS_TO_PROJECT_NAME = {
    alias: name for name, alias in PLATFORM_ALIASES.items()
    if name in _THIS_PROJECT_PLATFORMS
}
# Results written by an older API version may still say "twitter".
_ALIAS_TO_PROJECT_NAME.setdefault("twitter", "x")


def platforms_reached(result: Optional[Dict[str, Any]]) -> set:
    """Which of THIS PROJECT's platform names a resolved status result
    says actually succeeded - "success": true in that platform's own
    result entry, not just "the job was accepted."

    Reads the real, polled `results` array from check_status()/
    _resolve_final_result() when present. Falls back to an EMPTY set,
    never a guess, when the shape is not what was expected (an old SDK
    response with no `results` field, a dry run, a malformed payload) -
    the caller deciding a platform was reached because this function
    could not tell would be the exact bug this exists to fix, the other
    way round.
    """
    if not isinstance(result, dict):
        return set()
    reached = set()
    for entry in result.get("results") or ():
        if not isinstance(entry, dict) or not entry.get("success"):
            continue
        alias = str(entry.get("platform", "")).strip().lower()
        name = _ALIAS_TO_PROJECT_NAME.get(alias)
        if name:
            reached.add(name)
    return reached


class UploadPostPublisher:
    """Publishes one clip to every connected platform in one API call."""

    supports_link_posts = False
    supports_reels = True

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg or {}
        self._api_key = os.environ.get("UPLOAD_POST_API_KEY", "").strip()
        self._user = os.environ.get("UPLOAD_POST_USER", "").strip()
        self._overrides = dict(self._cfg.get("overrides", {}) or {})
        self._target_platforms = [
            p for p in (self._cfg.get("platforms") or
                        self._cfg.get("target_platforms") or [])
            if p]

    def ready(self) -> bool:
        # The SDK is optional: _upload_via_rest covers it. Only 'requests'
        # and the two credentials are hard requirements.
        if not _REQUESTS_OK:
            log.error("upload_post: 'requests' not installed — "
                      "pip install requests")
            return False
        if not self._api_key:
            log.error(
                "upload_post: UPLOAD_POST_API_KEY must be set in .env. "
                "Get one at upload-post.com (free plan: 10 uploads/month).")
            return False
        if not self._user:
            log.error(
                "upload_post: UPLOAD_POST_USER must be set in .env — "
                "your profile username from the dashboard.")
            return False
        if not _UP_CLIENT_OK:
            log.info("upload_post: SDK not installed — using the REST route "
                     "(same API, no extra dependency).")
        return True

    def post_clip(self, video_path: str, caption: str = "",
                  dry_run: bool = False) -> Optional[Dict[str, Any]]:
        if not os.path.isfile(video_path):
            log.error("upload_post: no such file: %s", video_path)
            return None

        # Compute targets for dry_run or actual upload
        targets = self._target_platforms or []
        if not targets:
            try:
                from auto_uploader.publish_guard import platform_names
                posting = self._cfg.get("posting", {}) or {}
                platforms = platform_names(posting)
                targets = [
                    p for p in platforms
                    if PLATFORM_ALIASES.get(p)
                    and (posting.get("platforms", {}).get(p, {}) or {}).get("enabled")
                    and p in _THIS_PROJECT_PLATFORMS
                ]
            except Exception:
                pass  # Fall through to default
            if not targets:
                targets = ["instagram", "tiktok", "youtube_shorts", "facebook"]

        # Dry run: simulate success for testing, no credential check needed
        if dry_run:
            aliases = targets
            log.info("[upload_post] WOULD POST to %s", aliases)
            return {alias: "dry-run" for alias in aliases}

        if not self._api_key or not self._user:
            log.error("upload_post: UPLOAD_POST_API_KEY and UPLOAD_POST_USER "
                      "must both be set in .env.")
            return None

        aliases = [PLATFORM_ALIASES.get(t, t) for t in targets]
        log.info("upload_post: posting %s to %s",
                 os.path.basename(video_path), aliases)

        title = (caption or "").splitlines()[0][:100] or "Clip"
        description = "\n".join((caption or "").splitlines()[1:])[:5000] or ""

        extra = {}
        for project_name, alias in PLATFORM_ALIASES.items():
            for suffix in ("title", "description"):
                key = f"{alias}_{suffix}"
                if key in self._overrides:
                    extra[key] = self._overrides[key]

        # Try the SDK first; fall back to raw requests if it throws.
        try:
            result = self._upload_via_sdk(video_path, title, user=self._user,
                                          platforms=aliases,
                                          description=description or None,
                                          extra=extra)
            if result is not None:
                log.info("upload_post: upload accepted via SDK, result: %s",
                         result)
                return self._resolve_final_result(result)
            log.warning("upload_post: SDK returned None — trying raw REST.")
        except Exception as exc:
            log.warning("upload_post: SDK failed (%s) — trying raw REST.", exc)

        result = self._upload_via_rest(video_path, title, self._user,
                                       aliases, description or None, extra)
        return self._resolve_final_result(result)

    # ── status: what actually happened, not just what was accepted ──────
    #
    # "Upload initiated successfully in background... Check its status
    # with GET /api/uploadposts/status?request_id=..." is upload-post's
    # own ack for an async job - and this project used to stop reading
    # right there, treat the ack as the outcome, and mark every platform
    # in the job "posted". A clip whose Instagram leg genuinely failed on
    # upload-post's side (an expired token, a disconnected account) came
    # back with exactly the same ack as one that actually landed
    # everywhere, and nothing here was ever able to tell the two apart -
    # which is indistinguishable, from the terminal, from "it's not
    # uploading" with no error anywhere to point at.
    #
    # _STATUS_PATH/_POLL_INTERVAL/_POLL_TIMEOUT existed already, unused,
    # since before this was written - the polling was clearly intended
    # and never wired up.

    def check_status(self, request_id: str) -> Optional[Dict[str, Any]]:
        """One status poll. None on any failure to reach or parse it -
        the caller falls back to the original ack rather than block on a
        status endpoint that may itself be having a bad day."""
        if not _REQUESTS_OK or not request_id:
            return None
        try:
            resp = requests.get(
                UPLOAD_POST_STATUS_API, params={"request_id": request_id},
                headers={"Authorization": f"Apikey {self._api_key}"},
                timeout=30)
        except Exception as exc:
            log.warning("upload_post: status check failed: %s", exc)
            return None
        if not resp.ok:
            log.warning("upload_post: status check rejected (HTTP %d): %s",
                       resp.status_code, resp.text[:300])
            return None
        try:
            return resp.json()
        except Exception:
            return None

    def _resolve_final_result(
            self, result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Poll until the job has actually finished, or give up and
        return what was known.

        `result` unchanged when there is nothing to poll: no request_id
        at all (a synchronous response, or a dry run), or 'requests' is
        unavailable to poll with. Otherwise polls check_status() every
        _POLL_INTERVAL seconds until the job reaches a terminal status
        (see _TERMINAL_JOB_STATUSES) or _POLL_TIMEOUT is spent, and
        returns upload-post's own final status payload - which is what
        carries the real, per-platform `results` this was all for.

        A poll that fails, or a timeout with the job still not terminal,
        returns the LAST status successfully read (or the original ack
        if none ever came back) rather than None - a clip that is still
        genuinely processing must not be reported as a failure.
        """
        if not isinstance(result, dict) or not _REQUESTS_OK:
            return result
        request_id = result.get("request_id")
        if not request_id:
            return result

        log.info("upload_post: polling status for request_id=%s ...",
                 request_id)
        deadline = time.time() + _POLL_TIMEOUT
        last_seen = result
        while time.time() < deadline:
            status = self.check_status(request_id)
            if status is not None:
                last_seen = status
                if str(status.get("status", "")).lower() in _TERMINAL_JOB_STATUSES:
                    log.info("upload_post: request_id=%s reached '%s' "
                            "(%s/%s platforms)", request_id,
                            status.get("status"), status.get("completed"),
                            status.get("total"))
                    return status
            time.sleep(_POLL_INTERVAL)
        log.warning(
            "upload_post: request_id=%s did not reach a final status "
            "within %ds - reporting what was last seen rather than "
            "waiting indefinitely. Check --posting-status later.",
            request_id, _POLL_TIMEOUT)
        return last_seen

    # ── SDK upload ───────────────────────────────────────────────────────

    def _upload_via_sdk(self, video_path: str, title: str, user: str,
                        platforms: list, description: Optional[str] = None,
                        extra: Optional[dict] = None) -> Optional[Dict[str, Any]]:
        """Upload via the upload-post Python SDK."""
        try:
            from upload_post import UploadPostClient
        except ImportError:
            log.error("upload_post: SDK not available — pip install upload-post")
            return None

        client = UploadPostClient(api_key=self._api_key)
        kwargs: Dict[str, Any] = {
            "video_path": video_path,
            "title": title,
            "user": user,
            "platforms": platforms,
        }
        if description:
            kwargs["description"] = description
        if extra:
            kwargs["extra"] = extra

        try:
            return client.upload_video(**kwargs)
        except Exception as exc:
            log.error("upload_post: SDK upload_video failed: %s", exc)
            return None


# ── Raw REST upload (fallback) ──────────────────────────────────────────────

    def _upload_via_rest(self, video_path: str, title: str, user: str,
                         platforms: list, description: Optional[str] = None,
                         extra: Optional[dict] = None) -> Optional[Dict[str, Any]]:
        """Upload via raw multipart POST to /api/upload.  Works without the SDK."""
        if not _REQUESTS_OK:
            log.error("upload_post: 'requests' not installed — pip install requests")
            return None

        log.info("upload_post: uploading via raw REST to %s ...", platforms)

        # Build the multipart form.  The SDK does the same under the hood.
        try:
            with open(video_path, "rb") as handle:
                video_bytes = handle.read()
        except OSError as exc:
            log.error("upload_post: could not read video: %s", exc)
            return None

        files = {
            "video": (os.path.basename(video_path), video_bytes, "video/mp4"),
        }
        # The API takes repeated `platform[]` fields, not a comma-joined
        # string (that shape belongs to /analyze-shorts). A dict cannot hold
        # duplicate keys, so build a list of tuples.
        data = [
            ("title", title),
            ("user", user),
        ]
        data += [("platform[]", p) for p in platforms]
        if description:
            data.append(("description", description))
        if extra:
            for k, v in extra.items():
                data.append((k, str(v)))

        headers = {"Authorization": f"Apikey {self._api_key}"}

        try:
            resp = requests.post(UPLOAD_POST_API, files=files, data=data,
                                 headers=headers, timeout=600)
        except Exception as exc:
            log.error("upload_post: REST upload failed: %s", exc)
            return None

        if not resp.ok:
            log.error("upload_post: REST upload rejected (HTTP %d): %s",
                      resp.status_code, resp.text[:500])
            return None

        try:
            result = resp.json()
        except Exception:
            result = {"raw_response": resp.text[:2000]}

        log.info("upload_post: REST upload accepted: %s", result)
        return result
