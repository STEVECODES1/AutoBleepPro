"""
publishers/facebook.py — Post Facebook Reels/videos via the Graph API.

Required credentials (in .env):
    FB_PAGE_TOKEN  — long-lived Page access token
    FB_PAGE_ID     — numeric Facebook Page ID

Required Graph API scopes:
    pages_manage_posts
    pages_read_engagement

Facebook GROUPS are manual-approval only and cannot be enabled in config.
There is no approved Graph API route for group publishing; the old
/group/feed endpoint was removed. Any config flag attempting to enable
group posting is ignored and raises a warning.

This module stays DISABLED (enabled: false in config) until credentials
are provided.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

log = logging.getLogger("publisher.facebook")

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False
    log.warning("facebook: 'requests' not installed — pip install requests")

from .errors import NotConfigured, is_configuration_problem

GRAPH_API = "https://graph.facebook.com/v19.0"
_POLL_INTERVAL = 5
_POLL_TIMEOUT  = 180

# Reels are uploaded as bytes to the upload host, not fetched by Meta
# from a URL. The Graph call only hands back where to send them.
_RUPLOAD_TIMEOUT = 600

# Facebook rejects a Reel outside this range outright, so it is worth
# saying so before spending the upload.
MIN_REEL_SECONDS = 3
MAX_REEL_SECONDS = 90


def _graph_error(exc: Exception) -> tuple:
    """(code, message) from the body, which is where Graph puts them.

    A bare `400 Client Error` says nothing actionable; the JSON underneath
    names the permission or the parameter that was wrong.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return None, ""
    try:
        error = response.json().get("error", {}) or {}
    except Exception:
        return None, ""
    return error.get("code"), str(error.get("message", ""))


def _graph_reason(exc: Exception) -> str:
    return _graph_error(exc)[1]


def _raise_if_setup(exc: Exception, doing: str) -> None:
    """Turn a permissions refusal into NotConfigured rather than a failure.

    A token missing pages_manage_posts refuses every post forever. Counted
    as failures, three of those trip the circuit breaker - and then fixing
    the token leaves Facebook blocked anyway until someone runs
    --reset-failures. That is a config problem wearing an error code.
    """
    code, message = _graph_error(exc)
    if is_configuration_problem(code, message):
        raise NotConfigured(
            f"Facebook cannot {doing} with this token: {message.strip()} "
            "Fix it at developers.facebook.com -> your app -> Graph API "
            "Explorer: grant pages_manage_posts and pages_read_engagement, "
            "generate a new PAGE token, then: python main.py --set-env "
            "FB_PAGE_TOKEN=<new token>") from exc


class FacebookPublisher:
    # A Page can publish a plain link post, so an announcement needs no
    # hosting for the video itself.
    supports_link_posts = True
    # And a Reel can be uploaded from disk - see post_reel_from_file.
    supports_reels = True

    def __init__(self, cfg: Dict[str, Any]) -> None:
        self._cfg = cfg
        self._token = os.getenv("FB_PAGE_TOKEN", "")
        self._page_id = os.getenv("FB_PAGE_ID", "")
        # Hard-block group posting regardless of config
        if cfg.get("posting", {}).get("platforms", {}).get(
            "facebook_group", {}
        ).get("enabled", False):
            log.warning(
                "facebook_group posting cannot be enabled — no approved "
                "Graph API route exists. Ignoring config flag."
            )

    def ready(self) -> bool:
        """Whether this publisher can post at all right now.

        Public because the announcer asks before attempting: an unset
        credential is a CONFIGURATION problem, and treating it as a failed
        post tripped the circuit breaker after three uploads - so filling
        the credentials in correctly still left the platform blocked.
        """
        return self._ready()

    def _ready(self) -> bool:
        if not _REQUESTS_OK:
            return False
        if not self._token or not self._page_id:
            log.error(
                "Facebook: FB_PAGE_TOKEN and FB_PAGE_ID must be set in .env "
                "before posting can be enabled."
            )
            return False
        return True

    def post_link(self, message: str, link: str) -> bool:
        """Publish a link post to the Page. Returns True on success.

        /{page_id}/feed with a `link` is the announcement path: Facebook
        fetches the preview card itself, so nothing has to be hosted
        first. Same `pages_manage_posts` scope the video path needs.
        """
        if not self._ready():
            return False
        if not link:
            log.error("Facebook: refusing to post an announcement with no link")
            return False

        url = f"{GRAPH_API}/{self._page_id}/feed"
        params = {
            "message": message,
            "link": link,
            "access_token": self._token,
        }
        try:
            r = requests.post(url, data=params, timeout=30)
            r.raise_for_status()
            post_id = r.json().get("id")
        except Exception as exc:
            _raise_if_setup(exc, "post a link")
            log.error("Facebook: link post failed: %s", _graph_reason(exc) or exc)
            return False

        if not post_id:
            log.error("Facebook: link post returned no id")
            return False
        log.info("Facebook: posted link, id=%s", post_id)
        return True

    # ── Reels from a local file ──────────────────────────────────────────

    def post_reel_from_file(self, video_path: str, caption: str = "",
                            share_to_feed: bool = True) -> bool:
        """Publish a local clip as a Page Reel. No hosting anywhere.

        post_reel() below takes a `file_url` that Meta fetches server-side,
        which is why Facebook never received a single Reel: there is
        nothing to hand it. A Rumble watch page is not a video file, and
        putting clips on public hosting purely to satisfy a fetch is
        infrastructure for its own sake.

        /{page_id}/video_reels removes the requirement the same way
        Instagram's resumable upload does - start the session, POST the
        bytes to the upload host it names, then finish and publish.

        `share_to_feed` is accepted so this matches the Instagram
        publisher's signature; a Page Reel appears on the Page either way,
        so there is nothing to pass on.
        """
        if not self._ready():
            return False
        if not os.path.isfile(video_path):
            log.error("Facebook: no such file: %s", video_path)
            return False

        size = os.path.getsize(video_path)
        if size <= 0:
            log.error("Facebook: %s is empty", os.path.basename(video_path))
            return False

        session = self._start_reel_session()
        if not session:
            return False
        video_id, upload_url = session

        if not self._upload_reel_bytes(upload_url, video_path, size):
            return False
        return self._finish_reel(video_id, caption)

    def _start_reel_session(self) -> Optional[tuple]:
        """(video_id, upload_url) for a new Reel, or None."""
        url = f"{GRAPH_API}/{self._page_id}/video_reels"
        try:
            r = requests.post(url, data={"upload_phase": "start",
                                         "access_token": self._token},
                              timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            _raise_if_setup(exc, "publish a Reel")
            log.error("Facebook: could not start a Reel upload: %s",
                      _graph_reason(exc) or exc)
            return None
        video_id = data.get("video_id") or data.get("id")
        upload_url = data.get("upload_url")
        if not video_id or not upload_url:
            log.error("Facebook: Reel session came back without an upload "
                      "target: %s", data)
            return None
        return video_id, upload_url

    def _upload_reel_bytes(self, upload_url: str, video_path: str,
                           size: int) -> bool:
        headers = {
            "Authorization": f"OAuth {self._token}",
            "offset": "0",
            "file_size": str(size),
        }
        try:
            with open(video_path, "rb") as f:
                r = requests.post(upload_url, data=f.read(), headers=headers,
                                  timeout=_RUPLOAD_TIMEOUT)
            r.raise_for_status()
        except Exception as exc:
            log.error("Facebook: Reel upload failed: %s",
                      _graph_reason(exc) or exc)
            return False
        log.info("Facebook: uploaded %.1f MB", size / 1e6)
        return True

    def _finish_reel(self, video_id: str, caption: str) -> bool:
        url = f"{GRAPH_API}/{self._page_id}/video_reels"
        params = {
            "upload_phase": "finish",
            "video_id": video_id,
            "video_state": "PUBLISHED",
            "description": caption,
            "access_token": self._token,
        }
        try:
            r = requests.post(url, data=params, timeout=60)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            log.error("Facebook: publishing Reel %s failed: %s", video_id,
                      _graph_reason(exc) or exc)
            return False
        if not data.get("success", True):
            log.error("Facebook: Reel %s was not published: %s", video_id, data)
            return False
        log.info("Facebook: published Reel %s", video_id)
        return True

    def post_reel(
        self,
        video_url: str,
        description: str = "",
    ) -> bool:
        """
        Publish a Reel to the Facebook Page.
        video_url must be a publicly accessible URL.
        Returns True on success.
        """
        if not self._ready():
            return False

        log.info("Facebook: initiating Reel upload for page %s", self._page_id)
        video_id = self._upload_video(video_url, description)
        if not video_id:
            return False

        log.info("Facebook: waiting for video %s to finish processing ...", video_id)
        return self._wait_for_video(video_id)

    def _upload_video(self, video_url: str, description: str) -> Optional[str]:
        url = f"{GRAPH_API}/{self._page_id}/videos"
        params = {
            "file_url": video_url,
            "description": description,
            "published": "true",
            "access_token": self._token,
        }
        try:
            r = requests.post(url, data=params, timeout=60)
            r.raise_for_status()
            data = r.json()
            video_id = data.get("id")
            if not video_id:
                log.error("Facebook: no video id in response: %s", data)
                return None
            log.info("Facebook: upload queued, video_id=%s", video_id)
            return video_id
        except Exception as exc:
            log.error("Facebook: upload failed: %s", exc)
            return None

    def _wait_for_video(self, video_id: str) -> bool:
        url = f"{GRAPH_API}/{video_id}"
        params = {
            "fields": "status",
            "access_token": self._token,
        }
        deadline = time.time() + _POLL_TIMEOUT
        while time.time() < deadline:
            try:
                r = requests.get(url, params=params, timeout=15)
                r.raise_for_status()
                data = r.json()
                processing = data.get("status", {}).get("processing_progress", 0)
                video_status = data.get("status", {}).get("video_status", "")
                if video_status == "ready":
                    log.info("Facebook: video %s is live", video_id)
                    return True
                if video_status == "error":
                    log.error("Facebook: video processing error: %s", data)
                    return False
                log.debug("Facebook: processing %s%% ...", processing)
            except Exception as exc:
                log.warning("Facebook: status poll error: %s", exc)
            time.sleep(_POLL_INTERVAL)
        log.error("Facebook: video %s did not become ready within %ds", video_id, _POLL_TIMEOUT)
        return False
