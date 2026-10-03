"""
publishers/postproxy.py - post one clip to the accounts connected on
Postproxy (postproxy.dev), through its REST API.

WHY THIS EXISTS
    Upload-Post and PostPlanify were the fan-out routes; both
    subscriptions ended and their code is gone. Postproxy takes their
    place: one upload, posted to every connected account.

TWO ROUTES, ONE SERVICE
    postproxy          - Instagram (as a Reel), TikTok, and anything else
                         connected there except YouTube.
    postproxy_youtube  - YouTube only, as a Short on the clips channel.

    YouTube is split off so it keeps its own daily cap and spacing in
    publish_guard. Twenty clips from one stream in an afternoon is what
    YouTube's "repetitious content" policy describes, so the cap that
    suits Instagram and TikTok is far too loose for a channel.

HOW A POST WORKS
    1. GET  /api/profiles  - the connected accounts. Only "active" ones are
                             used, so an expired login is skipped rather
                             than failed.
    2. POST /api/posts     - the clip itself, multipart, with every profile
                             in one call. An Idempotency-Key made from the
                             clip means a retry after a dropped connection
                             cannot post it twice.
    3. GET  /api/posts/ID  - polled until every platform says published or
                             failed, or the timeout. One still pending at
                             the timeout counts as accepted, not failed:
                             Postproxy owns publishing from that point.

    The result has the shape clip_queue reads -
    {"results": [{"platform": <project name>, "success": bool, ...}]} - so
    the direct publishers are skipped only for what really went out.

SETUP
    auto_uploader/.env:  POSTPROXY_API_KEY=...   (Postproxy -> API Keys)
    Connect accounts on the Postproxy dashboard. Nothing else to list here.
    config.json (optional): posting.platforms.postproxy and
    posting.platforms.postproxy_youtube {enabled, daily_cap,
    min_minutes_between} override the defaults in publish_guard.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any, Dict, List, Optional

from .errors import NotConfigured

log = logging.getLogger("publisher.postproxy")

try:
    import requests
    _REQUESTS_OK = True
except ImportError:  # pragma: no cover - requests is a hard dependency
    requests = None  # type: ignore
    _REQUESTS_OK = False

API_BASE = os.environ.get("POSTPROXY_API_URL",
                          "https://api.postproxy.dev").rstrip("/") + "/api"

# Postproxy's platform names -> this project's.
PLATFORM_TO_PROJECT = {
    "instagram": "instagram",
    "tiktok": "tiktok",
    "facebook": "facebook",
    "twitter": "x",
    "threads": "threads",
    "youtube": "youtube_shorts",
}

# Which Postproxy platforms each route posts to. None = every connected
# platform the other route does not take.
ROUTE_PLATFORMS = {
    "postproxy": None,
    "postproxy_youtube": ("youtube",),
}

CAPTION_LIMITS = {"instagram": 2200, "tiktok": 2200, "youtube": 5000,
                  "facebook": 5000, "twitter": 280, "threads": 500}

YOUTUBE_TITLE_MAX = 100
YOUTUBE_GAMING_CATEGORY = "20"

DEFAULT_POLL_TIMEOUT_S = 300
POLL_INTERVAL_S = 15


def platforms_reached(result: Optional[Dict[str, Any]]) -> set:
    """Project platform names this call actually got to. Empty - never a
    guess - when there is nothing real to read (a dry run, a malformed
    result)."""
    if not isinstance(result, dict):
        return set()
    return {
        entry.get("platform") for entry in result.get("results") or ()
        if isinstance(entry, dict) and entry.get("success")
        and entry.get("platform")
    }


def fit_caption(caption: str, limit: int) -> str:
    """The caption trimmed to `limit`, shedding whole hashtags and then
    whole lines from the end before cutting into the title line."""
    caption = (caption or "").strip()
    if not limit or len(caption) <= limit:
        return caption
    lines = caption.splitlines()
    while len(lines) > 1 and len("\n".join(lines).strip()) > limit:
        words = lines[-1].split()
        if len(words) > 1 and all(w.startswith("#") for w in words):
            lines[-1] = " ".join(words[:-1])
        else:
            lines.pop()
    text = "\n".join(lines).strip()
    if len(text) > limit:
        text = text[:limit - 1].rsplit(" ", 1)[0].rstrip() + "…"
    return text


def youtube_title(caption: str) -> str:
    """The first non-hashtag line of the caption, as a Short's title."""
    for line in (caption or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            if len(line) > YOUTUBE_TITLE_MAX:
                line = line[:YOUTUBE_TITLE_MAX - 1].rsplit(" ", 1)[0] + "…"
            return line
    return "Stackswopo clip"


def idempotency_key(route: str, video_path: str) -> str:
    """The same clip on the same route always gets the same key, so a
    retry of a call whose answer was lost is not a second post."""
    name = os.path.basename(video_path)
    try:
        size = os.path.getsize(video_path)
    except OSError:
        size = 0
    return hashlib.sha256(f"{route}|{name}|{size}".encode()).hexdigest()[:40]


def _enabled(posting: Dict[str, Any], name: str) -> bool:
    """Off only when this project says so explicitly."""
    block = ((posting or {}).get("platforms", {}) or {}).get(name)
    if not isinstance(block, dict):
        return True
    return block.get("enabled", True) is not False


class PostproxyPublisher:
    """Posts one clip to the connected Postproxy accounts for one route."""

    supports_link_posts = False
    supports_reels = True

    def __init__(self, cfg: dict, route: str = "postproxy") -> None:
        if route not in ROUTE_PLATFORMS:
            raise ValueError(f"unknown Postproxy route {route!r}")
        self._cfg = cfg or {}
        self.route = route
        self._key = os.environ.get("POSTPROXY_API_KEY", "").strip()
        settings = self._cfg.get("postproxy", {}) or {}
        self._poll_timeout_s = int(settings.get("poll_timeout_seconds",
                                                DEFAULT_POLL_TIMEOUT_S))
        self._tiktok_privacy = settings.get("tiktok_privacy",
                                            "PUBLIC_TO_EVERYONE")
        self._youtube_privacy = settings.get("youtube_privacy", "public")
        self._youtube_category = str(settings.get("youtube_category",
                                                  YOUTUBE_GAMING_CATEGORY))

    # ── plumbing ────────────────────────────────────────────────────────

    def ready(self) -> bool:
        if not _REQUESTS_OK:
            log.error("postproxy: 'requests' not installed")
            return False
        if not self._key:
            log.error("postproxy: POSTPROXY_API_KEY is not set in "
                      "auto_uploader/.env (Postproxy -> API Keys).")
            return False
        return True

    def _headers(self, extra: Optional[dict] = None) -> dict:
        headers = {"Authorization": f"Bearer {self._key}",
                   "User-Agent": "AutoBleepPro/1.0"}
        headers.update(extra or {})
        return headers

    @staticmethod
    def _check(response, what: str) -> Any:
        """The parsed body of a successful call; raises on anything else.

        401, 402 and 403 are configuration - a bad key, a lapsed plan -
        and must not be counted against the circuit breaker.
        """
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.ok:
            return body
        message = ""
        if isinstance(body, dict):
            message = str(body.get("error") or body.get("message") or body)
        message = message or f"HTTP {response.status_code}"
        if response.status_code in (401, 402, 403):
            raise NotConfigured(f"Postproxy refused {what}: {message}")
        raise RuntimeError(f"Postproxy {what} failed: HTTP "
                           f"{response.status_code} {message}"[:500])

    def _wanted(self, platform: str) -> bool:
        only = ROUTE_PLATFORMS[self.route]
        if only is not None:
            return platform in only
        taken = {p for r, ps in ROUTE_PLATFORMS.items()
                 if r != self.route and ps for p in ps}
        if platform in taken:
            return False
        project = PLATFORM_TO_PROJECT.get(platform)
        return bool(project) and _enabled(self._cfg.get("posting", {}),
                                          project)

    def profiles(self) -> List[dict]:
        """Connected, active accounts this route posts a clip to."""
        response = requests.get(f"{API_BASE}/profiles",
                                headers=self._headers(), timeout=30)
        body = self._check(response, "listing profiles")
        listed = body.get("data") if isinstance(body, dict) else body
        wanted = []
        for profile in listed or ():
            if not isinstance(profile, dict):
                continue
            platform = str(profile.get("platform") or "")
            if profile.get("status", "active") != "active":
                log.warning("postproxy: %s (%s) is %s - reconnect it on "
                            "the Postproxy dashboard", platform,
                            profile.get("name"), profile.get("status"))
                continue
            if self._wanted(platform):
                wanted.append(profile)
        return wanted

    def _form(self, platforms: set, caption: str) -> List[tuple]:
        """The non-file multipart fields for one post."""
        body_limit = min((CAPTION_LIMITS.get(p, 2200) for p in platforms),
                         default=2200)
        fields: List[tuple] = [("post[body]", fit_caption(caption,
                                                          body_limit))]
        if "instagram" in platforms:
            fields.append(("platforms[instagram][format]", "reel"))
        if "facebook" in platforms:
            fields.append(("platforms[facebook][format]", "reel"))
        if "tiktok" in platforms:
            fields.append(("platforms[tiktok][privacy_status]",
                           self._tiktok_privacy))
        if "youtube" in platforms:
            fields += [
                ("platforms[youtube][title]", youtube_title(caption)),
                ("platforms[youtube][privacy_status]", self._youtube_privacy),
                ("platforms[youtube][category_id]", self._youtube_category),
                ("platforms[youtube][made_for_kids]", "false"),
            ]
        return fields

    def _create(self, video_path: str, caption: str,
                profiles: List[dict]) -> dict:
        platforms = {str(p.get("platform")) for p in profiles}
        files: List[tuple] = [(name, (None, value))
                              for name, value in self._form(platforms,
                                                            caption)]
        files += [("profiles[]", (None, str(p.get("id"))))
                  for p in profiles]
        with open(video_path, "rb") as handle:
            files.append(("media[]", (os.path.basename(video_path), handle,
                                      "video/mp4")))
            response = requests.post(
                f"{API_BASE}/posts", files=files, timeout=900,
                headers=self._headers({"Idempotency-Key": idempotency_key(
                    self.route, video_path)}))
        post = self._check(response, "creating the post")
        if not isinstance(post, dict) or not post.get("id"):
            raise RuntimeError("Postproxy accepted the clip but returned no "
                               "post id")
        return post

    def _status(self, post_id: str) -> Optional[dict]:
        try:
            response = requests.get(f"{API_BASE}/posts/{post_id}",
                                    headers=self._headers(), timeout=30)
            return self._check(response, "reading the post")
        except NotConfigured:
            raise
        except Exception as exc:
            log.warning("postproxy: could not read post %s (%s)",
                        post_id, exc)
            return None

    @staticmethod
    def _url(entry: dict) -> str:
        for key in ("url", "post_url", "permalink", "platform_url"):
            if entry.get(key):
                return str(entry[key])
        params = entry.get("params")
        if isinstance(params, dict):
            for key in ("url", "post_url", "permalink"):
                if params.get(key):
                    return str(params[key])
        return ""

    def _settle(self, post: dict) -> Dict[str, dict]:
        """Poll until every platform has an outcome or time is up.

        Returns {postproxy platform: its last entry}.
        """
        def outcome(current: dict) -> Dict[str, dict]:
            return {str(e.get("platform")): e
                    for e in current.get("platforms") or ()
                    if isinstance(e, dict) and e.get("platform")}

        latest = outcome(post)
        deadline = time.time() + self._poll_timeout_s
        while time.time() < deadline and (
                not latest or any(e.get("status") in (None, "pending",
                                                      "processing")
                                  for e in latest.values())):
            time.sleep(POLL_INTERVAL_S)
            current = self._status(post["id"])
            if current:
                latest = outcome(current) or latest
        return latest

    # ── the call clip_queue makes ───────────────────────────────────────

    def post_clip(self, video_path: str, caption: str = "",
                  dry_run: bool = False) -> Optional[Dict[str, Any]]:
        if not os.path.isfile(video_path):
            log.error("postproxy: no such file: %s", video_path)
            return None

        if dry_run:
            log.info("[%s] WOULD POST %s to every connected account",
                     self.route, os.path.basename(video_path))
            return {"dry_run": True}

        profiles = self.profiles()
        if not profiles:
            where = ("YouTube (the clips channel)"
                     if self.route == "postproxy_youtube"
                     else "Instagram or TikTok")
            raise NotConfigured(f"no {where} account is connected and "
                                f"active on Postproxy")

        post = self._create(video_path, caption, profiles)
        latest = self._settle(post)

        results: List[dict] = []
        for profile in profiles:
            platform = str(profile.get("platform"))
            entry = latest.get(platform, {})
            status = entry.get("status") or "pending"
            result = {"platform": PLATFORM_TO_PROJECT.get(platform, platform),
                      "account": profile.get("name"),
                      "status": status,
                      "success": status != "failed",
                      "published": status == "published"}
            if entry.get("error"):
                result["error"] = str(entry["error"])
            url = self._url(entry)
            if url:
                result["post_url"] = url
            if status in ("pending", "processing"):
                # Accepted, not confirmed - said plainly so a later
                # failure on Postproxy's side can be traced.
                log.info("postproxy: %s still %s after the wait - Postproxy "
                         "will finish it.", platform, status)
            results.append(result)

        for result in results:
            log.info("postproxy: %s -> %s%s", result["platform"],
                     "ok" if result["success"] else "FAILED",
                     f" ({result['error']})" if result.get("error") else "")
        return {"post_id": post.get("id"), "results": results}
