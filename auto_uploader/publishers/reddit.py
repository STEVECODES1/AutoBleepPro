"""
publishers/reddit.py — Post to Reddit as a SEPARATE, named account.

Credentials are resolved per account name (default "2"), so this never
touches the primary REDDIT_* variables and is not affected by the state of
any other Reddit account configured elsewhere in the project:

    REDDIT_CLIENT_ID_2      — from https://www.reddit.com/prefs/apps
    REDDIT_CLIENT_SECRET_2
    REDDIT_USERNAME_2
    REDDIT_PASSWORD_2
    REDDIT_SUBREDDIT        — subreddit name without the r/ prefix

Reddit expects one "script" app per account, so the alternate account
needs its own app registered under that account - reusing the first
account's client_id with a second username is what looks like credential
sharing.

Posting still goes through publish_guard (kill switch, rolling cap,
spacing, circuit breaker). This class does not check the guard itself;
the caller must, because the guard is the only component allowed to
authorise a post.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

log = logging.getLogger("publisher.reddit")

DEFAULT_ACCOUNT = "2"


def _credential_helpers():
    """Import the shared credential resolver from either import root.

    main.py runs with auto_uploader/ on sys.path (`utils.x`); the tests
    import `auto_uploader.publishers.x`. Same module either way.
    """
    try:
        from ..utils.social_promoter import (  # type: ignore
            reddit_credentials, reddit_credentials_missing)
    except ImportError:  # pragma: no cover - depends on sys.path shape
        from utils.social_promoter import (  # type: ignore
            reddit_credentials, reddit_credentials_missing)
    return reddit_credentials, reddit_credentials_missing


class RedditPublisher:
    # Reddit takes a title and a URL, so a link announcement is native
    # here - no hosted media needed, unlike Instagram.
    supports_link_posts = True

    def __init__(self, cfg: Optional[Dict[str, Any]] = None,
                 account: Optional[str] = None) -> None:
        cfg = cfg or {}
        self._cfg = cfg
        promoter = (cfg.get("features", {}) or {}).get("social_promoter", {}) or {}
        self._account = (
            account
            if account is not None
            else promoter.get("reddit_account", DEFAULT_ACCOUNT)
        )
        self._subreddit = (
            os.getenv("REDDIT_SUBREDDIT", "").strip()
            or promoter.get("reddit_subreddit", "")
        )
        self._reddit: Optional[Any] = None

    # ── Readiness ────────────────────────────────────────────────────────

    def _missing_credentials(self) -> list:
        _, missing_fn = _credential_helpers()
        return missing_fn(self._account)

    def _ready(self) -> bool:
        """True when this account's credentials and target are configured.

        Deliberately does NOT check whether praw is importable. Those are
        different problems with different fixes - "pip install praw" vs
        "fill in your .env" - and folding them together produced a
        publisher that reported missing credentials on a machine whose
        credentials were fine.
        """
        missing = self._missing_credentials()
        if missing:
            log.error("Reddit: missing env vars: %s", ", ".join(missing))
            return False
        if not self._subreddit:
            log.error("Reddit: REDDIT_SUBREDDIT not set (and no "
                      "features.social_promoter.reddit_subreddit in config)")
            return False
        return True

    def ready(self) -> bool:
        """Whether a post could actually go out right now.

        Public because the announcer asks BEFORE attempting: an unset
        credential is a configuration problem, and recording it as a
        failed post would trip the circuit breaker over a post that was
        never made.
        """
        return self._ready()

    def _get_reddit(self):
        if self._reddit is None:
            import praw  # optional dependency; raises ImportError if absent

            creds_fn, _ = _credential_helpers()
            creds = creds_fn(self._account)
            self._reddit = praw.Reddit(
                client_id=creds["client_id"],
                client_secret=creds["client_secret"],
                username=creds["username"],
                password=creds["password"],
                # Reddit asks that the user agent identify the app and the
                # account it acts for; a blank or shared one is itself a
                # spam signal.
                user_agent=f"AutoBleepPro/2.0 (by u/{creds['username']})",
            )
        return self._reddit

    # ── Posting ──────────────────────────────────────────────────────────

    def _submit(self, what: str, **kwargs) -> bool:
        if not self._ready():
            return False
        try:
            submission = self._get_reddit().subreddit(self._subreddit).submit(**kwargs)
            log.info("Reddit: %s to r/%s — %s", what, self._subreddit,
                     getattr(submission, "shortlink", ""))
            return True
        except ImportError:
            log.error("Reddit: 'praw' is not installed — pip install praw")
            return False
        except Exception as exc:
            log.error("Reddit: %s failed: %s", what, exc)
            return False

    def post_link(self, title: str, url: str, flair: Optional[str] = None) -> bool:
        """Post a link post to the configured subreddit. True on success.

        The announcer passes a whole multi-line announcement here, but a
        Reddit title is one line and caps at 300 characters - so the
        headline is taken from the first line rather than submitting a
        title with newlines in it, which Reddit rejects.
        """
        # splitlines() on an empty string is [], not [""], so the index
        # has to be guarded - an announcement that arrived empty would
        # otherwise crash here instead of posting.
        lines = (title or "").strip().splitlines()
        title = (lines[0][:300] if lines else "") or "New upload"
        kwargs = {"title": title, "url": url}
        if flair:
            # Passing flair_id=None is not the same as omitting it on some
            # subreddits, so only send it when there is one.
            kwargs["flair_id"] = flair
        return self._submit("posted link", **kwargs)

    def post_text(self, title: str, body: str) -> bool:
        """Post a self/text post. True on success."""
        return self._submit("posted text", title=title, selftext=body)
