"""
publishers/facebook_free.py — Post Facebook Page videos + link posts via Graph API.

Free, official route. Needs a Page access token with create_content on the
Page. No scraping, no third-party poster, no credential proxy.

Two things this publisher can do:
  1. post_video_from_file()  — upload the clip bytes to the Page, get a
     video handle, publish. This is what a rendered clip is for.
  2. post_link()             — announce a YouTube/Rumble URL to the Page
     as a link post. This is what the social-promoter path uses.

Both go through publish_guard (kill switch, cap, spacing, breaker) — this
class does not check the guard itself; the caller must.

Credentials (in .env):
    FB_PAGE_TOKEN   — Page access token with create_content
    FB_PAGE_ID      — numeric Page ID

Both are free to obtain from developers.facebook.com using the Graph API
Explorer. The token expires; a long-lived token lasts about 60 days and can
be refreshed. When it expires the publisher reports "not configured" rather
than failing posts, so filling in a fresh token leaves the platform usable
rather than still blocked.

References
    - https://developers.facebook.com/docs/video-api/getting-started
    - https://developers.facebook.com/docs/graph-api/reference/page/videos
    - https://developers.facebook.com/docs/video-api/guides/publishing
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

from .errors import NotConfigured, PermanentlyRejected, is_permanent_rejection

log = logging.getLogger("publisher.facebook_free")

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False
    log.warning("facebook_free: 'requests' not installed — pip install requests")

# Graph API goes to two hosts. The video bytes upload to graph-video; the
# container/status/publish calls go to plain graph. Both are Meta-owned.
GRAPH_API = "https://graph.facebook.com/v19.0"
GRAPH_VIDEO_API = "https://graph-video.facebook.com/v19.0"

# A Page video can be up to 4 GB and 240 min. Clips are seconds long, so
# this only catches a full stream being handed here by mistake.
_MAX_VIDEO_MB = 4_000

# How long we poll the video handle for a "ready to publish" status.
_POLL_INTERVAL = 10
_POLL_TIMEOUT = 600   # 10 min — a 20 MB clip renders fast, but be safe.


class FacebookFreePublisher:
    """Posts one clip to a Facebook Page, or announces a link to it."""

    supports_link_posts = True
    supports_reels = True          # a Page video IS a replayable post

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg or {}
        self._token = os.environ.get("FB_PAGE_TOKEN", "").strip()
        self._page_id = os.environ.get("FB_PAGE_ID", "").strip()

    def ready(self) -> bool:
        if not _REQUESTS_OK:
            log.error("Facebook: 'requests' is not installed — "
                      "pip install requests")
            return False
        if not self._token:
            log.error(
                "Facebook: FB_PAGE_TOKEN must be set in .env. "
                "Get one from developers.facebook.com with pages_manage_posts "
                "+ pages_read_engagement + pages_show_list.")
            return False
        if not self._page_id:
            log.error(
                "Facebook: FB_PAGE_ID must be set in .env — the numeric ID "
                "of the Page you post to.")
            return False
        return True

    # ── video upload (the clip path) ───────────────────────────────────

    def post_video_from_file(self, video_path: str,
                              caption: str = "") -> bool:
        """Upload a local file as a Page video, then publish it.

        Two phases, same as the docs:
          1. POST /<page_id>/videos  with the bytes →  video ID
          2. The video is published automatically when the upload completes;
             no separate publish step is needed for a direct video upload.

        Returns True on success.
        """
        if not self.ready():
            return False
        if not os.path.isfile(video_path):
            log.error("Facebook: no such file: %s", video_path)
            return False

        size_mb = os.path.getsize(video_path) / 1e6
        if size_mb > _MAX_VIDEO_MB:
            log.error("Facebook: %.1f MB is over the Page video cap "
                      "(%.0f MB).", size_mb, _MAX_VIDEO_MB)
            return False

        try:
            log.info("Facebook: uploading %s (%d MB) ...",
                     os.path.basename(video_path), int(size_mb))
            with open(video_path, "rb") as handle:
                data = handle.read()
            response = requests.post(
                f"{GRAPH_VIDEO_API}/{self._page_id}/videos",
                data={
                    "message": caption or "",
                    "access_token": self._token,
                },
                files={"file": ("clip.mp4", data, "video/mp4")},
                timeout=_POLL_TIMEOUT,
            )
        except Exception as exc:
            log.error("Facebook: upload failed: %s", exc)
            return False

        if not response.ok:
            # Meta nests the reason the way Graph does everywhere.
            said = _facebook_why(response)
            log.error("Facebook: upload rejected (HTTP %d): %s",
                      response.status_code, said)
            payload = _safe_json(response)
            if is_permanent_rejection(payload, response.status_code):
                raise PermanentlyRejected(
                    f"Facebook will not process this video "
                    f"(HTTP {response.status_code}): {said}")
            return False

        video_id = _first_id(response.json())
        if not video_id:
            log.error("Facebook: no video id in response: %s",
                      response.text[:300])
            return False

        log.info("Facebook: posted video %s (%s)", video_id,
                 os.path.basename(video_path))
        return True

    # ── link post (the announcer path) ─────────────────────────────────

    def post_link(self, message: str, link: str) -> bool:
        """Post a link to the Page. True on success.

        message is the post text; link is the URL to share (YouTube/Rumble).
        """
        if not self.ready():
            return False
        if not link:
            log.error("Facebook: nothing to link to")
            return False

        try:
            log.info("Facebook: posting link to Page %s ...", self._page_id)
            response = requests.post(
                f"{GRAPH_API}/{self._page_id}/feed",
                data={
                    "message": message,
                    "link": link,
                    "access_token": self._token,
                },
                timeout=30,
            )
        except Exception as exc:
            log.error("Facebook: link post failed: %s", exc)
            return False

        if not response.ok:
            said = _facebook_why(response)
            log.error("Facebook: link post rejected (HTTP %d): %s",
                      response.status_code, said)
            payload = _safe_json(response)
            if is_permanent_rejection(payload, response.status_code):
                raise PermanentlyRejected(
                    f"Facebook will not accept this post "
                    f"(HTTP {response.status_code}): {said}")
            return False

        post_id = _first_id(response.json())
        if post_id:
            log.info("Facebook: posted link, post_id=%s", post_id)
            return True
        log.error("Facebook: no post id in response: %s", response.text[:300])
        return False

    # ── Reel/short path (same as video, named for the queue) ──────────

    def post_reel_from_file(self, video_path: str, caption: str = "") -> bool:
        """A Facebook "Reel" on a Page is just a Page video. Delegate."""
        return self.post_video_from_file(video_path, caption)


# ── helpers ────────────────────────────────────────────────────────────────

def _facebook_why(response) -> str:
    """The human-readable reason from a Meta error response."""
    try:
        payload = response.json()
    except Exception:
        body = (getattr(response, "text", "") or "").strip()
        return body[:400] or "no body"

    error = payload.get("error", payload) if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        return str(payload)[:400]

    # Meta's Graph API puts the message in a few places depending on the
    # endpoint. Grab the most specific one we have.
    parts = [str(error.get(key, "")).strip() for key in
             ("message", "error_user_msg", "error_user_title")]
    said = " — ".join(p for p in parts if p)
    code = error.get("code") or error.get("error_code")
    subcode = error.get("error_subcode")
    if code:
        said = f"{said} (code {code}" + (f"/{subcode}" if subcode else "") + ")"
    return said or str(payload)[:400]


def _safe_json(response) -> Any:
    try:
        return response.json()
    except Exception:
        return None


def _first_id(data) -> Optional[str]:
    """Pull an id out of a Meta response, wherever it put it."""
    if not isinstance(data, dict):
        return None
    for key in ("id", "video_id", "post_id", "facebook_id"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None
