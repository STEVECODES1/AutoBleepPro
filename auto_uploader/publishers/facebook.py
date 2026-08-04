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

GRAPH_API = "https://graph.facebook.com/v19.0"
_POLL_INTERVAL = 5
_POLL_TIMEOUT  = 180


class FacebookPublisher:
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
