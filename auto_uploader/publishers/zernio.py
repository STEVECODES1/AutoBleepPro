"""
Posts a clip to X through Zernio, instead of to X directly.

WHY NOT X DIRECTLY
------------------
Two routes to X were rejected before this one.

X's own API charges $0.20 for a post containing a URL, which is every
post this pipeline would make. At the configured cap that is $1.60 a
day to publish links.

twikit and Selenium avoid that by driving X's private endpoints with the
account password in a config file. The research proposing them says
plainly that X flags it as bot spam, and a brand-new clipping account -
which this is - is the easiest kind to catch. Losing the account costs
more than the API ever would.

Zernio is the third thing: an authorised poster that holds the X
relationship itself. The credential stored here is a Zernio key, not an
X password, and revoking it costs nothing.

HOW IT WORKS
------------
Three calls, because Zernio never takes the file directly:

  1. POST /v1/media/presign  ->  a one-hour upload URL in cloud storage
  2. PUT  <uploadUrl>        ->  the clip's bytes, no auth on this one
  3. POST /v1/posts          ->  the post, referencing the public URL

The account id comes from GET /v1/accounts and is written into
config.json once by `--setup-zernio`. It is not looked up per post: an
extra call before every upload is a way to fail that has nothing to do
with posting.

WHAT IT DOES NOT DO
-------------------
Retry. A failed post returns False and the queue's own ceiling, backoff
and circuit breaker decide what happens next, exactly as for every other
platform. A publisher that retries inside itself is a publisher whose
caps mean nothing.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Optional

from .errors import NotConfigured

BASE_URL = "https://zernio.com/api"
PRESIGN_PATH = "/v1/media/presign"
POSTS_PATH = "/v1/posts"
ACCOUNTS_PATH = "/v1/accounts"

# Zernio documents 5 GB. A clip from this pipeline is around 20 MB, so a
# file anywhere near this is a sign something sent the wrong path.
MAX_UPLOAD_BYTES = 5 * 1024 ** 3

# X truncates past this. Zernio would accept more and X would cut it.
MAX_TWEET_CHARS = 280

# Every destination Zernio can reach that this project posts clips to,
# and the guarded platform name each one uses. Separate names on purpose:
# a single "zernio" cap would force X and TikTok to share a budget, and
# their rules are nothing alike - X tolerates hourly, TikTok's spam
# checks punish rapid near-identical posting far harder.
DESTINATIONS = {
    "zernio_twitter": "twitter",
    "zernio_tiktok": "tiktok",
}

# How much text each destination will actually keep. Sending more is not
# an error anywhere; it is silently cut, usually mid-word.
CHAR_LIMITS = {"twitter": MAX_TWEET_CHARS, "tiktok": 2200}

# Reddit is reachable on this key and is NOT here. Self-promotion is
# governed per subreddit rather than by one account-wide rule, and an
# automated feed of one channel's clips is what most subreddits ban on
# sight. It stays a human's decision.
NOT_AUTOMATED = ("reddit",)

_TIMEOUT = 120


class ZernioError(RuntimeError):
    """Zernio answered, and the answer was no."""


def _request(method: str, url: str, token: str = "", payload=None,
             raw: bytes = b"", content_type: str = "") -> dict:
    """One HTTP call. Returns the decoded body, or {} when there is none."""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = raw
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if content_type:
        headers["Content-Type"] = content_type

    request = urllib.request.Request(url, data=body or None,
                                     headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            text = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        # The status matters to the caller: 401 is a key problem and no
        # retry fixes it, while 5xx is worth coming back for.
        raise ZernioError(f"HTTP {exc.code}: {detail or exc.reason}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise ZernioError(str(exc)) from exc
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except ValueError:
        return {}


class ZernioPublisher:
    """Posts one clip to X via Zernio."""

    def __init__(self, config: dict, destination: str = "zernio_twitter"):
        self.config = config or {}
        self.settings = dict(self.config.get("zernio", {}) or {})
        self.destination = destination
        self._platform = DESTINATIONS.get(destination, "twitter")

    # ── configuration ────────────────────────────────────────────────

    def token(self) -> str:
        # The key lives in .env, not config.json - config.json is copied
        # around, pasted into chat and screenshotted.
        return (os.environ.get("ZERNIO_API_KEY", "")
                or str(self.settings.get("api_key", ""))).strip()

    def account_id(self) -> str:
        """The Zernio account id for THIS destination.

        Per destination, not one shared id: the same key reaches four
        accounts, and posting a clip to whichever happened to be written
        down first is not a thing to leave to chance.
        """
        accounts = dict(self.settings.get("accounts", {}) or {})
        entry = accounts.get(self._platform) or {}
        if isinstance(entry, dict):
            return str(entry.get("account_id", "")).strip()
        return str(entry).strip()

    def platform_name(self) -> str:
        return self._platform

    def char_limit(self) -> int:
        return int(CHAR_LIMITS.get(self._platform, MAX_TWEET_CHARS))

    def base_url(self) -> str:
        return str(self.settings.get("base_url", "") or BASE_URL).rstrip("/")

    def ready(self) -> bool:
        """True only when a post could actually be made.

        Both halves are required. A key with no account id reaches
        Zernio and is told which account it forgot, one clip at a time.
        """
        return bool(self.token() and self.account_id())

    def _raise_if_setup(self) -> None:
        if not self.token():
            raise NotConfigured(
                "zernio: no API key. Add it to .env:\n"
                "         python main.py --set-env ZERNIO_API_KEY=sk_...")
        if not self.account_id():
            raise NotConfigured(
                f"zernio: no account_id for {self._platform}. Connect it in "
                f"the Zernio dashboard, then run:\n"
                f"         python main.py --setup-zernio")

    # ── the three calls ──────────────────────────────────────────────

    def accounts(self) -> list:
        """Every social account connected to this Zernio key."""
        data = _request("GET", self.base_url() + ACCOUNTS_PATH, self.token())
        found = data.get("accounts") if isinstance(data, dict) else data
        return found if isinstance(found, list) else []

    def upload(self, video_path: str) -> str:
        """Put the clip in Zernio's storage. Returns its public URL."""
        size = os.path.getsize(video_path)
        if size > MAX_UPLOAD_BYTES:
            raise ZernioError(f"{size / 1024 ** 3:.1f} GB is over Zernio's 5 GB limit")

        presigned = _request(
            "POST", self.base_url() + PRESIGN_PATH, self.token(),
            payload={"filename": os.path.basename(video_path),
                     "contentType": "video/mp4"})
        upload_url = str(presigned.get("uploadUrl", "") or "")
        public_url = str(presigned.get("publicUrl", "") or "")
        if not upload_url or not public_url:
            raise ZernioError(f"presign returned no URL: {presigned}")

        with open(video_path, "rb") as handle:
            payload = handle.read()
        # No Authorization on this one - the signature IS the auth, and
        # sending a bearer token to cloud storage is how a presigned PUT
        # gets rejected.
        _request("PUT", upload_url, raw=payload, content_type="video/mp4")
        return public_url

    def post(self, text: str, media_url: str) -> str:
        """Publish. Returns the post URL, or "" when Zernio gives none."""
        body = {
            "content": text[:self.char_limit()],
            "mediaItems": [{"type": "video", "url": media_url}],
            "platforms": [{"platform": self.platform_name(),
                           "accountId": self.account_id()}],
            # Now, not scheduled. The spacing that decides WHEN already
            # lives in PublishGuard with every other platform's, and a
            # second scheduler on Zernio's side would fight it.
            "publishNow": True,
        }
        answer = _request("POST", self.base_url() + POSTS_PATH,
                          self.token(), payload=body)
        return _post_url(answer)

    # ── what the queue calls ─────────────────────────────────────────

    def post_clip(self, video_path: str, caption: str,
                  dry_run: bool = False) -> Optional[str]:
        """Upload one clip. Returns the post URL, or None on failure."""
        self._raise_if_setup()
        if not os.path.isfile(video_path):
            raise NotConfigured(f"zernio: no such file {video_path}")

        if dry_run:
            print(f"[Zernio] DRY RUN - would post "
                  f"{os.path.basename(video_path)} to "
                  f"{self.platform_name()}")
            return "dry-run"

        try:
            media_url = self.upload(video_path)
            posted = self.post(caption, media_url)
        except ZernioError as exc:
            print(f"[Zernio] {exc}")
            return None
        # Zernio answers a publishNow post without always returning the
        # platform URL. The post was still made, so this must not read
        # as a failure - the queue would retry it and post it twice.
        return posted or "posted (Zernio returned no link)"


def _post_url(answer: dict) -> str:
    """The published URL out of Zernio's reply, wherever it put it."""
    if not isinstance(answer, dict):
        return ""
    direct = answer.get("platformPostUrl")
    if isinstance(direct, str) and direct:
        return direct
    post = answer.get("post")
    if isinstance(post, dict):
        for platform in post.get("platforms") or []:
            if isinstance(platform, dict):
                url = platform.get("platformPostUrl") or platform.get("url")
                if isinstance(url, str) and url:
                    return url
    return ""
