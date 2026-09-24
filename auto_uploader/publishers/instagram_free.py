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

# Why the import failed, not just that it did.
#
# This used to catch ImportError and report, always, "instagrapi not
# installed - pip install instagrapi". On a machine where instagrapi
# WAS installed - pip said "Requirement already satisfied: instagrapi
# 3.0.5", same interpreter - that message sent someone to run the
# install again and get the same answer, while Instagram, the account
# with 28k followers, posted nothing.
#
# The real cause, found by downloading 3.0.5 and reading
# instagrapi/exceptions.py directly rather than guessing again: THREE
# of the six names this file imported were never real in that module.
# `NotFound` is `NotFoundError`. `RetryAfterContent` and `UploadError`
# do not exist under any name - the closest real classes are
# RateLimitError and ClipNotUpload. Every one of the three raised
# ImportError at import time, and Python's `from X import (a, b, c)`
# fails the whole statement on the first bad name - so this module
# never loaded, on every run, regardless of Pillow. The previous
# version of this comment blamed a Pillow/moviepy version clash. That
# was a guess, made without the package in hand, and it was wrong: the
# actual failure was `cannot import name 'NotFound'`, an API mismatch
# with nothing to do with Pillow.
#
# Fixed at both ends: the names below are the real ones, AND each is
# read with getattr + a private placeholder that instagrapi will never
# raise, rather than a bare `from ... import`. A name still wrong, or
# renamed again in a future release, now disables just the one except
# branch that used it - not the entire publisher, the way one bad name
# did here.
_INSTA_IMPORT_ERROR = ""
try:
    from instagrapi import Client as InstagrapiClient
    import instagrapi.exceptions as _insta_exceptions
    _INSTA_OK = True
except Exception as exc:                      # noqa: BLE001 - see above
    _INSTA_OK = False
    _insta_exceptions = None
    _INSTA_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
    log.warning("instagram_free: instagrapi could not be imported - %s",
                _INSTA_IMPORT_ERROR)


class _NoSuchInstagrapiError(Exception):
    """Stands in for an instagrapi exception class this installed
    version does not define. Never raised by real code, so an except
    clause built on it is simply unreachable dead code rather than a
    crash - which is the point: one missing/renamed class must disable
    only the branch that names it."""


def _insta_exc(name: str):
    if _insta_exceptions is None:
        return _NoSuchInstagrapiError
    return getattr(_insta_exceptions, name, _NoSuchInstagrapiError)


# Real names in instagrapi 3.0.5's exceptions.py (confirmed by reading
# the module, not assumed): ChallengeRequired, FeedbackRequired and
# LoginRequired were always correct. NotFoundError, RateLimitError and
# ClipNotUpload are the three that were wrong before.
ChallengeRequired = _insta_exc("ChallengeRequired")
FeedbackRequired = _insta_exc("FeedbackRequired")
LoginRequired = _insta_exc("LoginRequired")
NotFound = _insta_exc("NotFoundError")
RetryAfterContent = _insta_exc("RateLimitError")
UploadError = _insta_exc("ClipNotUpload")
TwoFactorRequired = _insta_exc("TwoFactorRequired")


def instagrapi_problem() -> str:
    """Why instagrapi is unusable, or "" when it is fine.

    Written for the caller's message, so "not configured yet" can say
    which of the two it is - never installed, or installed and broken.
    """
    if _INSTA_OK:
        return ""
    if not _INSTA_IMPORT_ERROR:
        return "instagrapi is not installed - pip install instagrapi"
    if "No module named 'instagrapi'" in _INSTA_IMPORT_ERROR:
        return "instagrapi is not installed - pip install instagrapi"
    if "Pillow" in _INSTA_IMPORT_ERROR:
        return (f"instagrapi IS installed but will not import - "
                f"{_INSTA_IMPORT_ERROR}. This looks like the Pillow "
                f"version clash: instagrapi needs Pillow>=12.2 and "
                f"moviepy needs Pillow<12.")
    return (f"instagrapi IS installed but will not import - "
            f"{_INSTA_IMPORT_ERROR}. Installing it again will not help "
            f"if this names a symbol, not a missing package - check "
            f"what changed in the instagrapi version installed.")


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
            # The real reason, which is not always "not installed" - see
            # instagrapi_problem(). Telling someone to install a package
            # that pip says is already there is a dead end, and this one
            # cost the account its clips.
            log.error("Instagram (instagrapi): %s", instagrapi_problem())
            print(f"[Publisher] Instagram (instagrapi): {instagrapi_problem()}")
            return False
        if not self._user or not self._password:
            log.error(
                "Instagram (instagrapi): INSTA_USERNAME and INSTA_PASSWORD "
                "must be set in .env before posting can be enabled.")
            return False
        return True

    # ── login (lazy, with session persistence) ─────────────────────────

    def _login(self, cl: "InstagrapiClient") -> bool:
        """Log `cl` in, solving a 2FA challenge once if Instagram asks.

        Every cl.login() call in _ensure_client() used to be unguarded, so
        a fresh/expired session (the saved cookies are stale, or there is
        no session file yet) that made Instagram challenge the login for
        2FA raised straight out of _ensure_client() as a raw
        TwoFactorRequired: "Instagram returned a Bloks two-factor context
        from the CAA login flow; provide verification_code for login" -
        which post_reel_from_file() had no handling for either, so it
        surfaced all the way to the caller as an unrecognised exception,
        failed the post, and after enough of those opened the circuit
        breaker for the whole platform. Nothing about that was a real
        problem with the account or the code - Instagram just wanted the
        code, and nothing ever offered to type it in.

        Same pattern as Rumble's own 2FA prompt (utils/rumble_uploader.py:
        `input("[Rumble] 2FA code requested...")`): a blocking input() on
        the console this process is running in. This can also run from
        --watch's background thread, but that thread shares the process's
        own console, so the prompt still appears there and still reads
        back whatever gets typed into that window - it just means nobody
        watching it will sit there until someone is.
        """
        try:
            cl.login(self._user, self._password)
            return True
        except TwoFactorRequired:
            pass
        code = input("[Instagram] 2FA code requested - check your "
                     "authenticator app, SMS or email and enter it "
                     "here: ").strip()
        if not code:
            log.error("Instagram (instagrapi): no 2FA code entered - "
                      "cannot finish logging in.")
            return False
        try:
            cl.login(self._user, self._password, verification_code=code)
            return True
        except Exception as exc:
            log.error("Instagram (instagrapi): 2FA login failed: %s", exc)
            return False

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
                if not self._login(cl):
                    return None
                log.info("Instagram (instagrapi): restored session for %s",
                         self._user)
            except Exception as exc:
                log.warning("Instagram (instagrapi): session restore failed "
                            "(re-logging in): %s", exc)
                cl = InstagrapiClient()
                if not self._login(cl):
                    return None
        else:
            if not self._login(cl):
                return None

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
