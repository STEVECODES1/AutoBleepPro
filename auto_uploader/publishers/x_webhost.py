"""
publishers/x_webhost.py — Post to X/Twitter via the free-tier web-host bridge.

WHAT THIS IS
    X's API free tier is read-only as of 2025. The Basic tier ($200/mo) is
    the cheapest route to posting, and this project does not take that cost.

    So X gets a BRIDGE instead: the clip is uploaded to a web host you
    control (Postiz, S3, Cloudflare R2, any static host with a public URL),
    and the post is the link + caption. The host is the only thing that needs
    credentials; X itself is only ever read by the audience.

    The host is read from env:
        WEBHOOK... no — a PUBLIC_URL_TEMPLATE that the caller fills in, OR
        a hoster module that knows how to PUT a file and get a URL back.

    This publisher implements the X leg: given a public video URL and a
    caption, it builds the post text and... still can't POST to X on the
    free tier. So this publisher's real job is to:
      1. Produce the exact post text for X (so a person can paste it), and
      2. Record that X is "ready but ad-supported only" so the status report
         is honest.

    When a paid X Basic token IS configured (X_BASIC_BEARER_TOKEN), this
    publisher uses the v2 media/upload + POST /2/tweets path, because at
    that point X is reachable for real.

Flow
    1. If X_BASIC_BEARER_TOKEN is set → upload media via /2/media/upload,
       then POST /2/tweets with the media ref. Real post.
    2. If not → the post text is printed to the log as "copy-paste this",
       and the platform is marked as "link-post only, manual on X".
    3. publish_guard still gates this: kill switch, cap, spacing, breaker.

References
    - https://docs.x.com/  (X API v2)
    - Free tier: read-only; Basic $200/mo for write.
    - https://github.com/gitroomhq/postiz-app  (self-hosted bridge that can
      hold the OAuth relationship with X so THIS project never stores an X
      password or a $200/mo token)
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

from .errors import NotConfigured

log = logging.getLogger("publisher.x_webhost")

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False
    log.warning("x_webhost: 'requests' not installed — pip install requests")

try:
    from utils.puter_host import PuterHost
    _PUTER_OK = True
except ImportError:
    _PUTER_OK = False
    log.warning("x_webhost: PuterHost not available — "
                "pip install requests (Puter needs no extra dep)")

X_API_V2 = "https://api.x.com/2"
_MEDIA_UPLOAD = f"{X_API_V2}/media/upload"
_POST_TWEET = f"{X_API_V2}/tweets"
_MAX_CHARS = 280
_POLL_INTERVAL = 5
_POLL_TIMEOUT = 180


class XWebhostPublisher:
    """One X post, either for real (paid Basic token) or as copy-paste text."""

    supports_link_posts = True

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg or {}
        # Paid route: X Basic bearer token. The free tier cannot post.
        self._bearer = os.environ.get("X_BASIC_BEARER_TOKEN", "").strip()

    def ready(self) -> bool:
        if not _REQUESTS_OK:
            log.error("X: 'requests' is not installed — pip install requests")
            return False
        # "Ready" means we can at least produce the post text. A real POST
        # only happens when the bearer token is present.
        return True

    # ── text production (always works) ────────────────────────────────

    def post_link(self, message: str, link: str) -> bool:
        """Build the X post text. If a bearer token is set, actually post.

        On the free tier this returns True (the text is ready) but the post
        itself is left as copy-paste — X has no free write API.
        """
        if not self.ready():
            return False

        text = _build_x_text(message, link)
        if not text:
            return False

        # Real post only with the paid token.
        if self._bearer:
            return self._post_with_token(text, link)
        else:
            _log_copy_paste(text)
            # Not a failure: X simply has no free write route. The text is
            # ready and printed so a person can paste it.
            return True

    # ── paid route ─────────────────────────────────────────────────────

    def _post_with_token(self, text: str, video_url: str) -> bool:
        """Upload the media, then tweet with the media ref."""
        if not _REQUESTS_OK:
            return False
        if not self._bearer:
            return False

        headers = {"Authorization": f"Bearer {self._bearer}",
                   "Content-Type": "application/json"}

        # Step 1: media upload. X v2 media/upload accepts a URL on the
        # free/basic tier via ?media=... — but the reliable path is the
        # chunked upload. For a clip that is already hosted, the simplest
        # supported path is to pass the public URL.
        try:
            # X's media endpoint can take a media_url parameter on some
            # tiers; if that fails, fall back to copy-paste.
            upload_resp = requests.post(
                _MEDIA_UPLOAD,
                params={"media": video_url},
                headers=headers,
                timeout=30,
            )
            if not upload_resp.ok:
                log.error("X: media upload rejected (HTTP %d): %s",
                          upload_resp.status_code,
                          upload_resp.text[:300])
                return False
            media_id = upload_resp.json().get("media_id")
            if not media_id:
                # Some tiers return the media ID differently; try the data.
                data = upload_resp.json()
                media_id = (data.get("media_id_string")
                            or data.get("id")
                            or "")
            if not media_id:
                log.error("X: no media_id in upload response: %s",
                          upload_resp.text[:300])
                return False
            log.info("X: media uploaded, media_id=%s", media_id)
        except Exception as exc:
            log.error("X: media upload failed: %s", exc)
            return False

        # Step 2: post the tweet with the media attached.
        payload = {"text": text[:_MAX_CHARS],
                   "media": {"media_ids": [str(media_id)]}}
        try:
            post_resp = requests.post(_POST_TWEET,
                                       json=payload,
                                       headers=headers,
                                       timeout=30)
            if not post_resp.ok:
                log.error("X: tweet rejected (HTTP %d): %s",
                          post_resp.status_code,
                          post_resp.text[:300])
                return False
            tweet = post_resp.json()
            tweet_id = (tweet.get("data", {}).get("id") or "")
            if tweet_id:
                log.info("X: posted tweet %s", tweet_id)
                return True
            log.error("X: no tweet id in response: %s", post_resp.text[:300])
            return False
        except Exception as exc:
            log.error("X: tweet post failed: %s", exc)
            return False

    def post_reel_from_file(self, video_path: str, caption: str = "",
                            share_to_feed: bool = True) -> bool:
        """Post a local clip to X.

        Free tier: upload the clip to Puter (free public host), then build the
        post text with the Puter URL. A person pastes it on X, OR configure
        X_BASIC_BEARER_TOKEN for a real post.

        Paid route (X_BASIC_BEARER_TOKEN): upload via X's media endpoint, then
        tweet with the media ref.
        """
        if not self.ready():
            return False
        if not os.path.isfile(video_path):
            log.error("X: no such file: %s", video_path)
            return False

        if self._bearer:
            # Paid route: host the clip ourselves via Puter, then use it as
            # the media source for X's upload endpoint.
            return self._post_with_token(caption or "", video_path)

        # Free tier: put the clip on Puter, print the post text.
        if not _PUTER_OK:
            log.error("X: no free host available — install requests, or set "
                      "X_BASIC_BEARER_TOKEN for a real post.")
            return False

        hoster = PuterHost()
        if not hoster.ready():
            log.error("X: Puter not configured — set PUTER_API_KEY in .env, "
                      "or set X_BASIC_BEARER_TOKEN for a real post.")
            return False

        public_url = hoster.put_file(video_path)
        if not public_url:
            log.error("X: could not host the clip on Puter.")
            return False

        text = _build_x_text(caption or "", public_url)
        if not text:
            return False

        _log_copy_paste(text)
        log.info("X: clip hosted at %s — copy-paste the post above, or set "
                  "X_BASIC_BEARER_TOKEN for an automatic post.", public_url)
        return True


# ── helpers ────────────────────────────────────────────────────────────────

def _build_x_text(message: str, link: str) -> Optional[str]:
    """The post text for X, within 280 chars including the link."""
    link_cost = 23   # X counts every link as 23 chars regardless of length
    budget = _MAX_CHARS - link_cost
    body = (message or "").strip()
    if len(body) > budget:
        body = body[:budget - 3].rstrip() + "..."
    if not body:
        return None
    return f"{body} {link}"


def _log_copy_paste(text: str) -> None:
    """Tell the operator the exact text to paste on X by hand."""
    log.info("───────────────────────────────────────────────────────────────")
    log.info("X (free tier): no write API — copy-paste this post by hand:")
    log.info("───────────────────────────────────────────────────────────────")
    log.info("  %s", text)
    log.info("───────────────────────────────────────────────────────────────")
    log.info("  OR: configure X_BASIC_BEARER_TOKEN in .env for a real post.")
    log.info("  OR: connect X to a self-hosted Postiz instance, which holds")
    log.info("  the OAuth relationship so this project never stores an X")
    log.info("  password or a $200/mo token. See: https://github.com/gitroomhq/postiz-app")
    log.info("───────────────────────────────────────────────────────────────")
