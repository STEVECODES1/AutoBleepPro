"""
publishers/twitter.py — Post to X (Twitter) via tweepy.

Use cases:
  1. Clip promotion after a successful upload.
  2. Stream announce: post every 30 min while streaming
     (call announce_stream() on a schedule).

Required credentials (in .env — same keys already in .env.example):
    TWITTER_API_KEY
    TWITTER_API_SECRET
    TWITTER_ACCESS_TOKEN
    TWITTER_ACCESS_SECRET

Rate limiting is handled by publish_guard (daily cap + min spacing).
Do NOT call post_clip() or announce_stream() directly — always go
through the guard first.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

log = logging.getLogger("publisher.twitter")

try:
    import tweepy
    _TWEEPY_OK = True
except ImportError:
    _TWEEPY_OK = False
    log.warning("twitter: 'tweepy' not installed — pip install tweepy")


class TwitterPublisher:
    def __init__(self, cfg: Dict[str, Any]) -> None:
        self._cfg = cfg
        self._api_key        = os.getenv("TWITTER_API_KEY", "")
        self._api_secret     = os.getenv("TWITTER_API_SECRET", "")
        self._access_token   = os.getenv("TWITTER_ACCESS_TOKEN", "")
        self._access_secret  = os.getenv("TWITTER_ACCESS_SECRET", "")
        self._client: Optional[Any] = None

    def _ready(self) -> bool:
        if not _TWEEPY_OK:
            return False
        missing = [k for k, v in {
            "TWITTER_API_KEY": self._api_key,
            "TWITTER_API_SECRET": self._api_secret,
            "TWITTER_ACCESS_TOKEN": self._access_token,
            "TWITTER_ACCESS_SECRET": self._access_secret,
        }.items() if not v]
        if missing:
            log.error("Twitter: missing env vars: %s", ", ".join(missing))
            return False
        return True

    def _get_client(self):
        if self._client is None:
            self._client = tweepy.Client(
                consumer_key=self._api_key,
                consumer_secret=self._api_secret,
                access_token=self._access_token,
                access_token_secret=self._access_secret,
            )
        return self._client

    def post_clip(
        self,
        text: str,
        url: Optional[str] = None,
    ) -> bool:
        """
        Tweet a clip promotion. URL is appended to text if provided.
        Returns True on success.
        """
        if not self._ready():
            return False
        full_text = f"{text}\n{url}" if url else text
        # Twitter hard cap: 280 chars
        full_text = full_text[:280]
        try:
            client = self._get_client()
            resp = client.create_tweet(text=full_text)
            tweet_id = resp.data.get("id") if resp.data else None
            log.info("Twitter: tweeted clip promotion, id=%s", tweet_id)
            return True
        except Exception as exc:
            log.error("Twitter: post_clip failed: %s", exc)
            return False

    def announce_stream(
        self,
        stream_title: str,
        stream_url: str,
        channel: str = "Stackswopo",
    ) -> bool:
        """
        Post a stream-is-live announcement.
        Designed to be called every 30 minutes by an external scheduler
        (e.g. APScheduler or a simple loop in main.py --watch).
        publish_guard must be checked BEFORE calling this.
        """
        if not self._ready():
            return False
        text = (
            f"🔴 {channel} is LIVE — {stream_title}\n"
            f"Join now 👇\n{stream_url}"
        )[:280]
        try:
            client = self._get_client()
            resp = client.create_tweet(text=text)
            tweet_id = resp.data.get("id") if resp.data else None
            log.info("Twitter: stream announce posted, id=%s", tweet_id)
            return True
        except Exception as exc:
            log.error("Twitter: announce_stream failed: %s", exc)
            return False
