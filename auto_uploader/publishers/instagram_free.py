"""
publishers/instagram_free.py — Post Instagram Reels via instagrapi.

instagrapi is the free, unofficial Instagram Private API wrapper. It logs in
with a regular (non-business) account username + password and uploads Reels
directly - no Graph API token, no Business Account ID, no container dance.

This is the FREE path. The Graph API path in instagram.py requires a Business
Account and a Page token grants-scoped for instagram_content_publish, which
Meta makes you apply for. instagrapi works with any account you can log into
on the app, which is why it is the path for a small creator account.

Tradeoffs instagrapi makes explicit (from its own docs):
  - Private API automation is fragile: account trust, proxies, device state,
    challenges and rate limits can change without notice.
  - Production use needs session persistence (dump_settings / load_settings)
    and a challenge resolver hook.
  - It is MIT-licensed and Python 3.10+.

Flow:
  1. Login (password, with session persistence on disk)
  2. clip_upload(video_path, caption)  ->  returns a Media object
  3. That is it. instagrapi handles the upload, processing poll and publish.

No hosting required. The bytes go straight from disk to Instagram's servers.

References:
  - https://github.com/subzeroid/instagrapi
  - https://github.com/subzeroid/instagrapi/issues/2575  (Reel upload bug,
    now closed; current versions handle the upload flow reported there)
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

from .errors import NotConfigured, PermanentlyRejected

log = logging.getLogger("publisher.instagram_free")

try:
    from instagrapi import Client as InstagrapiClient
    from instagrapi.exceptions import (
        ChallengeRequired, FeedbackRequired, LoginRequired,
        NotFound, RetryAfterContent, UploadError)
    _INSTA_OK = True
except ImportError:
    _INSTA_OK = False
    log.warning("instagram_free: instagrapi not installed — pip install instagrapi")


# Reels cap. instagrapi will reject longer uploads at the server side; this
# is the documented Reels ceiling so we fail fast with a useful message.
_MAX_REEL_SECONDS = 15 * 60
_MAX_REEL_MB = 1_000  # 1 GB — instagrapi enforces this server-side


class InstagramFreePublisher:
    """Posts one clip to Instagram as a Reel, via instagrapi.

    No hosting, no Graph API token, no Business Account. Just username +
    password (or a saved session file) and a local video.
    """

    supports_link_posts = False
    supports_reels = True

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg or {}
        # instagrapi reads these from the environment by convention; we
        # read them here so ready() can report which ones are missing.
        self._user = os.environ.get("INSTA_USERNAME", "").strip()
        self._password = os.environ.get("INSTA_PASSWORD", "").strip()
        self._session_file = os.environ.get(
            "INSTA_SESSION_FILE",
            os.path.join(os.getcwd(), ".instagrapi_session.json")).strip()
        self._reel_tags = str(self._cfg.get("reel_tags", "")
                              or os.environ.get("INSTA_REEL_TAGS", "")).strip()
        self._client: Optional[InstagrapiClient] = None
        self._logged_in = False

    # ── readiness ──────────────────────────────────────────────────────

    def ready(self) -> bool:
        """True when a post could actually go out right now."""
        if not _INSTA_OK:
            log.error(
                "Instagram (instagrapi): 'instagrapi' is not installed — "
                "pip install instagrapi")
            return False
        if not self._user or not self._password:
            log.error(
                "Instagram (instagrapi): INSTA_USERNAME and INSTA_PASSWORD "
                "must be set in .env before posting can be enabled.")
            return False
        return True

    # ── login (lazy, with session persistence) ─────────────────────────

    def _ensure_client(self) -> Optional[InstagrapiClient]:
        """Log in once, keep the client. Returns None on auth failure."""
        if self._client is not None and self._logged_in:
            return self._client

        cl = InstagrapiClient()

        # Reuse a saved session if one exists. dump_settings() writes the
        # device + cookies + mid; load_settings() restores them. This is
        # what makes repeated runs not ask for 2FA every time.
        if os.path.isfile(self._session_file):
            try:
                cl.load_settings(self._session_file)
                cl.login(self._user, self._password)
                log.info("Instagram (instagrapi): restored session for %s",
                         self._user)
            except Exception as exc:
                log.warning("Instagram (instagrapi): session restore failed "
                            "(re-logging in): %s", exc)
                cl = InstagrapiClient()
                cl.login(self._user, self._password)
        else:
            cl.login(self._user, self._password)

        self._client = cl
        self._logged_in = True

        # Persist the session for next time. Best-effort: if this fails the
        # client still works, next run just logs in again.
        try:
            cl.dump_settings(self._session_file)
        except Exception:
            pass

        return cl

    # ── posting ────────────────────────────────────────────────────────

    def post_reel_from_file(self, video_path: str, caption: str = "",
                            share_to_feed: bool = True) -> bool:
        """Publish a local file as a Reel. True on success."""
        if not self.ready():
            return False
        if not os.path.isfile(video_path):
            log.error("Instagram (instagrapi): no such file: %s", video_path)
            return False

        size_mb = os.path.getsize(video_path) / 1e6
        if size_mb > _MAX_REEL_MB:
            log.error(
                "Instagram (instagrapi): %.1f MB is over the Reels cap "
                "(%.0f MB).", size_mb, _MAX_REEL_MB)
            return False

        seconds = _media_duration(video_path)
        if seconds and seconds > _MAX_REEL_SECONDS:
            log.error(
                "Instagram (instagrapi): %.0f s is over the Reels cap "
                "(%d s).", seconds, _MAX_REEL_SECONDS)
            return False

        # instagrapi captions accept hashtags inline. Append the channel's
        # reel tags if any were configured — but never double the channel
        # tag if the caption already carries it.
        full_caption = _build_caption(caption, self._reel_tags)

        cl = self._ensure_client()
        if cl is None:
            return False

        try:
            log.info("Instagram (instagrapi): uploading %s ...",
                     os.path.basename(video_path))
            media = cl.clip_upload(video_path, full_caption)
            media_id = getattr(media, "pk", None) or getattr(media, "id", None)
            if media_id:
                log.info("Instagram (instagrapi): Reel posted, pk=%s",
                         media_id)
                return True
            log.error("Instagram (instagrapi): upload returned no media id")
            return False
        except ChallengeRequired:
            log.error(
                "Instagram (instagrapi): challenge required. Log in on the "
                "app and try again, or check your session file.")
            self._logged_in = False
            return False
        except FeedbackRequired as exc:
            log.error("Instagram (instagrapi): feedback required (rate "
                      "limit or action block): %s", exc)
            self._logged_in = False
            return False
        except RetryAfterContent:
            log.error("Instagram (instagrapi): rate limited on content. "
                      "Wait a bit and try again.")
            return False
        except LoginRequired:
            log.error("Instagram (instagrapi): session expired. Re-login "
                      "needed.")
            self._logged_in = False
            return False
        except UploadError as exc:
            log.error("Instagram (instagrapi): upload rejected: %s", exc)
            return False
        except NotFound as exc:
            log.error("Instagram (instagrapi): not found: %s", exc)
            return False
        except Exception as exc:
            log.error("Instagram (instagrapi): unexpected error: %s", exc,
                      exc_info=True)
            self._logged_in = False
            return False


# ── helpers ────────────────────────────────────────────────────────────────

def _media_duration(path: str) -> Optional[float]:
    """Duration in seconds, or None when it cannot be read."""
    try:
        import imageio_ffmpeg  # optional; instagrapi uses it internally
    except ImportError:
        pass
    try:
        from instagrapi import utils as _iutils
        return _iutils.get_media_duration(path)
    except Exception:
        pass
    # Fallback: probe with ffprobe if available.
    try:
        import subprocess
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=15)
        if out.returncode == 0 and out.stdout.strip():
            return float(out.stdout.strip())
    except Exception:
        pass
    return None


def _build_caption(caption: str, tags: str) -> str:
    """Append configured tags, avoiding duplicates of what is already there."""
    text = (caption or "").strip()
    if not tags:
        return text
    wanted = [t for t in tags.split() if t]
    if not wanted:
        return text
    lowered = text.lower()
    fresh = [t for t in wanted if t.lower() not in lowered]
    if not fresh:
        return text
    return f"{text}\n\n{' '.join(fresh)}" if text else " ".join(fresh)
