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

from .errors import PermanentlyRejected, is_permanent_rejection

log = logging.getLogger("publisher.instagram")

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False
    log.warning("instagram: 'requests' not installed — pip install requests")

GRAPH_API = "https://graph.facebook.com/v19.0"
# Uploading the bytes goes to a different host from the rest of Graph.
RUPLOAD_API = "https://rupload.facebook.com/ig-api-upload/v19.0"
# Reels cap at 1 GB and 15 minutes. Clips are seconds long, so this only
# catches a full stream being handed here by mistake - which would
# otherwise upload for an hour and then be rejected.
MAX_REEL_BYTES = 1_000_000_000
_UPLOAD_TIMEOUT = 600
_PUBLISH_POLL_INTERVAL = 5   # seconds between status checks
_PUBLISH_TIMEOUT      = 120  # give up after 2 minutes


def _why(response) -> str:
    """The reason out of a Meta error response, in one readable line.

    Meta nests it as {"error": {"message": ..., "error_user_title": ...,
    "error_user_msg": ...}}. The user-facing pair is usually the plain
    English one and is often absent, so both are tried before falling
    back to the raw body.
    """
    try:
        payload = response.json()
    except Exception:
        body = (getattr(response, "text", "") or "").strip()
        return body[:400] or "no body"

    error = payload.get("error", payload) if isinstance(payload, dict) else {}
    if not isinstance(error, dict):
        return str(payload)[:400]

    parts = [str(error.get(key, "")).strip() for key in
             ("error_user_title", "error_user_msg", "message")]
    said = " - ".join(part for part in parts if part)
    code = error.get("code")
    subcode = error.get("error_subcode")
    if code:
        said = f"{said} (code {code}" + (f"/{subcode}" if subcode else "") + ")"
    return said or str(payload)[:400]


class InstagramPublisher:
    # Instagram has NO text or link post. Every Content Publishing call
    # builds a media container - IMAGE, VIDEO, REELS, CAROUSEL, STORIES -
    # and each needs a publicly reachable image_url/video_url that Meta
    # fetches server-side. There is no endpoint that takes a URL and
    # renders a preview card the way a Facebook Page post does.
    #
    # So Instagram cannot carry a link announcement, and this flag is what
    # stops the announcer quietly reporting a post that never happened.
    # It becomes reachable once clips are rendered AND hosted somewhere
    # public - at which point post_reel() below is already written.
    supports_link_posts = False

    # ...but a VIDEO can be published, which is what a clip is. See
    # post_reel_from_file(): the bytes are uploaded directly, so no
    # hosting is needed and this is reachable for clips even though a
    # link announcement never will be.
    supports_reels = True

    def __init__(self, cfg: Dict[str, Any]) -> None:
        self._cfg = cfg
        self._token = os.getenv("IG_PAGE_TOKEN", "")
        self._account_id = os.getenv("IG_BUSINESS_ACCOUNT_ID", "")

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
        if not self._token or not self._account_id:
            log.error(
                "Instagram: IG_PAGE_TOKEN and IG_BUSINESS_ACCOUNT_ID must be "
                "set in .env before posting can be enabled."
            )
            return False
        return True

    def post_reel_from_file(self, video_path: str, caption: str = "",
                            share_to_feed: bool = True) -> bool:
        """Publish a local file as a Reel, with no hosting anywhere.

        This is what makes Instagram reachable at all. The documented
        Content Publishing flow takes a `video_url` that Meta fetches
        server-side, which means every clip needs a public URL first -
        and a Rumble page is not a fetchable video file, so there was
        nothing to give it.

        upload_type=resumable removes that requirement: the container is
        created empty and the bytes are POSTed straight to
        rupload.facebook.com. Same container, same publish step
        afterwards.

        Returns True on success. On any failure the caller still has the
        hosted-URL path in post_reel().
        """
        if not self._ready():
            return False
        if not os.path.isfile(video_path):
            log.error("Instagram: no such file: %s", video_path)
            return False

        size = os.path.getsize(video_path)
        if size > MAX_REEL_BYTES:
            log.error("Instagram: %s is %.0f MB - Reels cap at %.0f MB.",
                      os.path.basename(video_path), size / 1e6,
                      MAX_REEL_BYTES / 1e6)
            return False

        container_id = self._create_resumable_container(caption, share_to_feed)
        if not container_id:
            return False
        if not self._upload_bytes(container_id, video_path, size):
            return False

        log.info("Instagram: waiting for container %s to be ready ...", container_id)
        if not self._wait_for_container(container_id):
            return False
        return self._publish_container(container_id)

    def _create_resumable_container(self, caption: str,
                                    share_to_feed: bool) -> Optional[str]:
        params = {
            "media_type": "REELS",
            "upload_type": "resumable",
            "caption": caption,
            "share_to_feed": str(share_to_feed).lower(),
            "access_token": self._token,
        }
        try:
            r = requests.post(f"{GRAPH_API}/{self._account_id}/media",
                              data=params, timeout=30)
            r.raise_for_status()
            container_id = r.json().get("id")
        except Exception as exc:
            log.error("Instagram: resumable container creation failed: %s", exc)
            return None
        if not container_id:
            log.error("Instagram: no container id came back")
            return None
        return container_id

    def _upload_bytes(self, container_id: str, video_path: str,
                      size: int) -> bool:
        """POST the file to the upload host. offset/file_size are required.

        Read whole rather than streamed: a Reel is capped at a size that
        fits in memory comfortably, and a streamed body without a known
        length is what the offset/file_size headers exist to avoid.
        """
        url = f"{RUPLOAD_API}/{container_id}"
        headers = {
            "Authorization": f"OAuth {self._token}",
            "offset": "0",
            "file_size": str(size),
        }
        try:
            with open(video_path, "rb") as f:
                r = requests.post(url, data=f.read(), headers=headers,
                                  timeout=_UPLOAD_TIMEOUT)
        except Exception as exc:
            log.error("Instagram: upload failed: %s", exc)
            return False

        # Read off status_code rather than .ok so this is true of any
        # response-shaped object, not only a requests.Response.
        status = getattr(r, "status_code", 0)
        if not 200 <= status < 300:
            # raise_for_status() throws away the only useful part. Meta
            # answers a rejected upload with a JSON body naming the
            # reason - wrong codec, bad aspect, expired token - and
            # without it the message is "400 Bad Request for url: ..."
            # with a container ID in it, which says nothing at all and
            # cost an evening.
            log.error("Instagram: upload rejected (HTTP %s): %s",
                      status, _why(r))
            # Meta answers a Reel it cannot process with
            # `'retriable': False`. That is a considered statement, and
            # ignoring it cost three identical uploads of one clip inside
            # a single drain, each rejected identically.
            try:
                payload = r.json()
            except Exception:
                payload = None
            if is_permanent_rejection(payload, status):
                raise PermanentlyRejected(
                    f"Instagram will not process this video "
                    f"(HTTP {status}): {_why(r)}")
            return False
        log.info("Instagram: uploaded %.1f MB for container %s",
                 size / 1e6, container_id)
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
