"""
publishers/tiktok_free.py — Post TikTok videos via the TikTok Open API.

Two free routes, tried in order:

  1. tiktok-api-client  — OAuth-based TikTok Open API. Needs a developer app
     (free to register at developers.tiktok.com) and a user access token.
     Upload from local file or from a public URL. This is the clean path.

  2. tiktokautouploader  — Playwright-based uploader that logs in with the
     account password and uploads through the browser flow. No developer
     app needed. Used as a fallback when OAuth is not configured.

The TikTok Open API caps a posted video at 10 minutes (and the docs note
smaller ceilings for newer accounts). Clips from this pipeline are seconds
long, so that only catches a full stream being handed here by mistake.

References:
  - https://pypi.org/project/tiktok-api-client/
  - https://github.com/mymi14s/tiktok-api-client
  - https://developers.tiktok.com/ — Content Posting API
  - https://github.com/arsantoid/TikTokAutoUploader  (playwright fallback)
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

from .errors import NotConfigured

log = logging.getLogger("publisher.tiktok_free")

# ── Route 1: tiktok-api-client (OAuth) ──────────────────────────────────────

_TIK_OK = False
_TIK_CLIENT = None

try:
    from tiktok_api_client import TikTok
    _TIK_OK = True
except ImportError:
    log.warning(
        "tiktok_free: tiktok-api-client not installed — "
        "pip install tiktok-api-client  (needed for the OAuth route)")


class TikTokFreePublisher:
    """Posts one clip to TikTok, trying the free API routes in order."""

    supports_link_posts = False       # TikTok takes video, not link posts
    supports_reels = True             # "reels" here = native video posts

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg or {}
        # Route 1 creds (OAuth). These come from the TikTok developer portal.
        self._client_key = os.environ.get("TIKTOK_CLIENT_KEY", "").strip()
        self._client_secret = os.environ.get("TIKTOK_CLIENT_SECRET", "").strip()
        self._redirect_uri = os.environ.get("TIKTOK_REDIRECT_URI", "").strip()
        # A pre-obtained access token (from an OAuth flow run separately).
        self._access_token = os.environ.get("TIKTOK_ACCESS_TOKEN", "").strip()
        # Route 2 creds (playwright fallback).
        self._tiktok_user = os.environ.get("TIKTOK_USERNAME", "").strip()
        self._tiktok_password = os.environ.get("TIKTOK_PASSWORD", "").strip()
        # Posting preferences.
        self._title = str(self._cfg.get("title", "")
                          or os.environ.get("TIKTOK_DEFAULT_TITLE", "")).strip()
        self._hashtags = str(self._cfg.get("hashtags", "")
                             or os.environ.get("TIKTOK_HASHTAGS", "#fyp #foryou"))
        self._privacy = str(self._cfg.get("privacy", "PUBLIC")).upper()

    # ── readiness ──────────────────────────────────────────────────────

    def ready(self) -> bool:
        """True when at least one route has the credentials it needs."""
        if not _TIK_OK:
            log.error(
                "TikTok: 'tiktok-api-client' is not installed — "
                "pip install tiktok-api-client")
            return False
        # Route 1 needs the OAuth pieces.
        if self._client_key and self._client_secret and self._access_token:
            return True
        # Route 2 needs the account password.
        if self._tiktok_user and self._tiktok_password:
            return True
        log.error(
            "TikTok: need either (TIKTOK_CLIENT_KEY + TIKTOK_CLIENT_SECRET + "
            "TIKTOK_ACCESS_TOKEN) for the OAuth route, or "
            "(TIKTOK_USERNAME + TIKTOK_PASSWORD) for the browser fallback.")
        return False

    # ── Route 1: OAuth via tiktok-api-client ───────────────────────────

    def _post_oauth(self, video_path: str, caption: str) -> bool:
        """Post using the TikTok Open API client."""
        if not (self._client_key and self._client_secret and self._access_token):
            return False

        try:
            tik = TikTok(
                client_key=self._client_key,
                client_secret=self._client_secret,
                redirect_uri=self._redirect_uri or "http://localhost",
                # scopes needed for video.upload + video.publish
                scopes=["video.upload", "video.publish", "video.list"],
            )
            # Set the token we already have. In a full setup you would run
            # the OAuth web flow once to get this token; this project stores
            # it in .env so the uploader does not need a web server.
            tik.set_access_token(self._access_token)

            log.info("TikTok (OAuth): uploading %s ...", os.path.basename(video_path))
            response = tik.create_video(
                title=self._title or os.path.splitext(
                    os.path.basename(video_path))[0],
                source="FILE_UPLOAD",
                upload_type="POST_VIDEO_FILE",
                privacy_level=self._privacy,
                video_path=video_path,
                disable_comment=False,
                disable_duet=True,
                disable_stitch=True,
                video_cover_timestamp_ms=1000,
            )
            post_id = (response.get("publish_id")
                       or response.get("id")
                       or response.get("video_id")
                       or "")
            if post_id:
                log.info("TikTok (OAuth): posted, publish_id=%s", post_id)
                return True
            log.error("TikTok (OAuth): unexpected response: %s", response)
            return False
        except Exception as exc:
            log.error("TikTok (OAuth): %s", exc, exc_info=True)
            return False

    # ── Route 2: browser fallback via tiktokautouploader ───────────────

    def _post_browser(self, video_path: str, caption: str) -> bool:
        """Post using tiktokautouploader (Playwright-based)."""
        try:
            from tiktokautouploader import upload_tiktok
        except ImportError:
            log.error(
                "TikTok (browser): tiktokautouploader not installed — "
                "pip install tiktokautouploader")
            return False

        full_caption = f"{caption or self._title}\n{self._hashtags}".strip()

        try:
            log.info("TikTok (browser): uploading %s ...", os.path.basename(video_path))
            upload_tiktok(
                video=video_path,
                description=full_caption,
                accountname=self._tiktok_user,
                hashtags=[t.lstrip("#") for t in self._hashtags.split()
                          if t.strip()],
                headless=True,
                stealth=True,
                suppressprint=False,
            )
            log.info("TikTok (browser): upload launched for %s",
                     self._tiktok_user)
            return True
        except Exception as exc:
            log.error("TikTok (browser): %s", exc, exc_info=True)
            return False

    # ── public surface ──────────────────────────────────────────────────

    def post_reel_from_file(self, video_path: str, caption: str = "") -> bool:
        """Post a local video to TikTok. Tries OAuth first, then browser."""
        if not self.ready():
            return False
        if not os.path.isfile(video_path):
            log.error("TikTok: no such file: %s", video_path)
            return False

        size_mb = os.path.getsize(video_path) / 1e6
        if size_mb > 500:
            log.error("TikTok: %.1f MB — clips should be much smaller than "
                      "this; something sent the wrong file.", size_mb)
            return False

        full_caption = caption or self._title

        # Route 1: OAuth.
        if self._client_key and self._client_secret and self._access_token:
            if self._post_oauth(video_path, full_caption):
                return True
            log.warning("TikTok (OAuth): failed — trying browser fallback.")

        # Route 2: browser.
        if self._tiktok_user and self._tiktok_password:
            return self._post_browser(video_path, full_caption)

        log.error("TikTok: neither route produced a post.")
        return False
