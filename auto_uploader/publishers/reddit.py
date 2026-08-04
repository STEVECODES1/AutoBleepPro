"""
publishers/reddit.py — Post to Reddit using a SEPARATE account.

This uses _2 credentials (REDDIT_CLIENT_ID_2, etc.) so it is completely
independent of any existing or locked Reddit account configured elsewhere
in the project.  The original REDDIT_* env vars are never touched here.

Required credentials (in .env):
    REDDIT_CLIENT_ID_2      — from https://www.reddit.com/prefs/apps
    REDDIT_CLIENT_SECRET_2
    REDDIT_USERNAME_2
    REDDIT_PASSWORD_2
    REDDIT_SUBREDDIT        — subreddit name without r/ prefix (e.g. stackswopo)

Posting stays behind publish_guard (caps, kill switch, circuit breaker).
Facebook Groups equivalent (r/private groups you don't mod) = manual only.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

log = logging.getLogger("publisher.reddit")

try:
    import praw
    _PRAW_OK = True
except ImportError:
    _PRAW_OK = False
    log.warning("reddit: 'praw' not installed — pip install praw")


class RedditPublisher:
    def __init__(self, cfg: Dict[str, Any]) -> None:
        self._cfg = cfg
        self._client_id     = os.getenv("REDDIT_CLIENT_ID_2", "")
        self._client_secret = os.getenv("REDDIT_CLIENT_SECRET_2", "")
        self._username      = os.getenv("REDDIT_USERNAME_2", "")
        self._password      = os.getenv("REDDIT_PASSWORD_2", "")
        self._subreddit     = os.getenv("REDDIT_SUBREDDIT", "") or \
            cfg.get("features", {}).get("social_promoter", {}).get("reddit_subreddit", "")
        self._reddit: Optional[Any] = None

    def _ready(self) -> bool:
        if not _PRAW_OK:
            return False
        missing = [k for k, v in {
            "REDDIT_CLIENT_ID_2": self._client_id,
            "REDDIT_CLIENT_SECRET_2": self._client_secret,
            "REDDIT_USERNAME_2": self._username,
            "REDDIT_PASSWORD_2": self._password,
        }.items() if not v]
        if missing:
            log.error("Reddit: missing env vars: %s", ", ".join(missing))
            return False
        if not self._subreddit:
            log.error("Reddit: REDDIT_SUBREDDIT not set")
            return False
        return True

    def _get_reddit(self):
        if self._reddit is None:
            self._reddit = praw.Reddit(
                client_id=self._client_id,
                client_secret=self._client_secret,
                username=self._username,
                password=self._password,
                user_agent=f"AutoBleepPro/2.0 (by u/{self._username})",
            )
        return self._reddit

    def post_link(
        self,
        title: str,
        url: str,
        flair: Optional[str] = None,
    ) -> bool:
        """Post a link post to REDDIT_SUBREDDIT. Returns True on success."""
        if not self._ready():
            return False
        try:
            reddit = self._get_reddit()
            sub = reddit.subreddit(self._subreddit)
            submission = sub.submit(title=title, url=url, flair_id=flair)
            log.info(
                "Reddit: posted to r/%s — %s", self._subreddit, submission.shortlink
            )
            return True
        except Exception as exc:
            log.error("Reddit: post failed: %s", exc)
            return False

    def post_text(
        self,
        title: str,
        body: str,
    ) -> bool:
        """Post a self/text post. Returns True on success."""
        if not self._ready():
            return False
        try:
            reddit = self._get_reddit()
            sub = reddit.subreddit(self._subreddit)
            submission = sub.submit(title=title, selftext=body)
            log.info(
                "Reddit: text post to r/%s — %s", self._subreddit, submission.shortlink
            )
            return True
        except Exception as exc:
            log.error("Reddit: text post failed: %s", exc)
            return False
