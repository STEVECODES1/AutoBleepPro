"""
publishers/instagram.py — Post Instagram Reels via the Graph API.

Required credentials (in .env):
    IG_PAGE_TOKEN          — long-lived Page access token
    IG_BUSINESS_ACCOUNT_ID — numeric Instagram Business Account ID

Required Graph API scopes:
    instagram_content_publish
    pages_manage_posts

This module stays DISABLED (enabled: false in config) until you supply
real credentials. Posting to a 27K-follower account without testing
first would be the wrong trade.

Flow (two-step Graph API container upload):
  1. POST /ig_user_id/media   → returns container_id
  2. POST /ig_user_id/media_publish  → publishes the container
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

log = logging.getLogger("publisher.instagram")

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False
    log.warning("instagram: 'requests' not installed — pip install requests")

GRAPH_API = "https://graph.facebook.com/v19.0"
_PUBLISH_POLL_INTERVAL = 5   # seconds between status checks
_PUBLISH_TIMEOUT      = 120  # give up after 2 minutes


class InstagramPublisher:
    def __init__(self, cfg: Dict[str, Any]) -> None:
        self._cfg = cfg
        self._token = os.getenv("IG_PAGE_TOKEN", "")
        self._account_id = os.getenv("IG_BUSINESS_ACCOUNT_ID", "")

    def _ready(self) -> bool:
        if not _REQUESTS_OK:
            return False
        if not self._token or not self._account_id:
            log.error(
                "Instagram: IG_PAGE_TOKEN and IG_BUSINESS_ACCOUNT_ID must be "
                "set in .env before posting can be enabled."
            )
            return False
        return True

    def post_reel(
        self,
        video_url: str,
        caption: str = "",
        share_to_feed: bool = True,
    ) -> bool:
        """
        Publish a Reel. video_url must be a publicly accessible URL
        (e.g. a signed S3/CDN link) — the Graph API fetches the video
        server-side, so local file paths will not work.

        Returns True on success.
        """
        if not self._ready():
            return False

        log.info("Instagram: creating media container for Reel ...")
        container_id = self._create_container(video_url, caption, share_to_feed)
        if not container_id:
            return False

        log.info("Instagram: waiting for container %s to be ready ...", container_id)
        if not self._wait_for_container(container_id):
            return False

        log.info("Instagram: publishing container %s ...", container_id)
        return self._publish_container(container_id)

    def _create_container(
        self, video_url: str, caption: str, share_to_feed: bool
    ) -> Optional[str]:
        url = f"{GRAPH_API}/{self._account_id}/media"
        params = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": str(share_to_feed).lower(),
            "access_token": self._token,
        }
        try:
            r = requests.post(url, data=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            container_id = data.get("id")
            if not container_id:
                log.error("Instagram: no container id in response: %s", data)
                return None
            return container_id
        except Exception as exc:
            log.error("Instagram: container creation failed: %s", exc)
            return None

    def _wait_for_container(self, container_id: str) -> bool:
        url = f"{GRAPH_API}/{container_id}"
        params = {
            "fields": "status_code,status",
            "access_token": self._token,
        }
        deadline = time.time() + _PUBLISH_TIMEOUT
        while time.time() < deadline:
            try:
                r = requests.get(url, params=params, timeout=15)
                r.raise_for_status()
                data = r.json()
                status = data.get("status_code", "")
                if status == "FINISHED":
                    return True
                if status == "ERROR":
                    log.error("Instagram: container error: %s", data)
                    return False
                log.debug("Instagram: container status=%s — waiting ...", status)
            except Exception as exc:
                log.warning("Instagram: status poll error: %s", exc)
            time.sleep(_PUBLISH_POLL_INTERVAL)
        log.error("Instagram: container did not become FINISHED within %ds", _PUBLISH_TIMEOUT)
        return False

    def _publish_container(self, container_id: str) -> bool:
        url = f"{GRAPH_API}/{self._account_id}/media_publish"
        params = {
            "creation_id": container_id,
            "access_token": self._token,
        }
        try:
            r = requests.post(url, data=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            media_id = data.get("id")
            if media_id:
                log.info("Instagram: published Reel, media_id=%s", media_id)
                return True
            log.error("Instagram: publish response missing id: %s", data)
            return False
        except Exception as exc:
            log.error("Instagram: publish failed: %s", exc)
            return False
