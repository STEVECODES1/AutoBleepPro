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
    log.warning(
        "upload_post: 'upload-post' SDK not installed — "
        "pip install upload-post")

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False
    log.warning("upload_post: 'requests' not installed — pip install requests")

UPLOAD_POST_API = "https://api.upload-post.com/api/upload"
_STATUS_PATH = "/api/uploadposts/status"
_POLL_INTERVAL = 10
_POLL_TIMEOUT = 600

# Platforms Upload-Post can reach, and the alias it wants for each.
PLATFORM_ALIASES = {
    "instagram": "instagram",
    "facebook": "facebook",
    "tiktok": "tiktok",
    "x": "twitter",
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
        if not _UP_CLIENT_OK:
            log.error(
                "upload_post: 'upload-post' SDK not installed — "
                "pip install upload-post")
            return False
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
        return True

    def post_clip(self, video_path: str, caption: str = "",
                  dry_run: bool = False) -> Optional[Dict[str, Any]]:
        if not os.path.isfile(video_path):
            log.error("upload_post: no such file: %s", video_path)
            return None

        if not self._api_key or not self._user:
            log.error("upload_post: UPLOAD_POST_API_KEY and UPLOAD_POST_USER "
                      "must both be set in .env.")
            return None

        targets = self._target_platforms or []
        if not targets:
            from publish_guard import platform_names
            posting = self._cfg.get("posting", {}) or {}
            platforms = platform_names(posting)
            targets = [
                p for p in platforms
                if self.PLATFORM_ALIASES.get(p)
                and (posting.get("platforms", {}).get(p, {}) or {}).get("enabled")
                and p in _THIS_PROJECT_PLATFORMS
            ]
            if not targets:
                targets = ["instagram", "tiktok", "youtube_shorts", "facebook"]

        aliases = [self.PLATFORM_ALIASES.get(t, t) for t in targets]
        log.info("upload_post: posting %s to %s",
                 os.path.basename(video_path), aliases)

        title = (caption or "").splitlines()[0][:100] or "Clip"
        description = "\n".join((caption or "").splitlines()[1:])[:5000] or ""

        extra = {}
        for project_name, alias in self.PLATFORM_ALIASES.items():
            for suffix in ("title", "description"):
                key = f"{alias}_{suffix}"
                if key in self._overrides:
                    extra[key] = self._overrides[key]

        if dry_run:
            log.info("[upload_post] WOULD POST to %s", aliases)
            return {alias: "dry-run" for alias in aliases}

        # Try the SDK first; fall back to raw requests if it throws.
        try:
            result = self._upload_via_sdk(video_path, title, user=self._user,
                                          platforms=aliases,
                                          description=description or None,
                                          extra=extra)
            if result is not None:
                log.info("upload_post: upload accepted via SDK, result: %s",
                         result)
                return result
            log.warning("upload_post: SDK returned None — trying raw REST.")
        except Exception as exc:
            log.warning("upload_post: SDK failed (%s) — trying raw REST.", exc)

        return self._upload_via_rest(video_path, title, self._user,
                                     aliases, description or None, extra)

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
        data = {
            "title": title,
            "user": user,
            "platforms": ",".join(platforms),
        }
        if description:
            data["description"] = description
        if extra:
            for k, v in extra.items():
                data[k] = str(v)

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
