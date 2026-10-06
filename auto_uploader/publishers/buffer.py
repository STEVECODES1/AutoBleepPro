"""
publishers/buffer.py - X (Twitter) and TikTok posts through Buffer's
free plan.

Why Buffer: X's own API is pay-per-post, and Buffer's free plan includes
an X channel and API access (1 key, 3,000 requests per 30 days). Each post
here is two or three requests, so that is a few hundred posts a month.

The free plan takes 3 channels, so one Buffer account carries both X and
TikTok (the clip queue's buffer_x and buffer_tiktok) - no card needed.

Two kinds of post:
  post_link - an announcement: the YouTube / Rumble link, X shows the
              preview card. Nothing needs hosting.
  post_clip - the clip itself as a native video (buffer_x on X,
              buffer_tiktok on TikTok). Buffer only takes media by
              public URL, so the file is put on Cloudinary first - see
              utils/cloud_host.py. Buffer fetches it when the post goes
              out (straight away: shareNow) and X / TikTok keep their own
              copy, so Cloudinary deleting it a day later changes nothing.

Setup (once):
  1. buffer.com -> connect the X (and TikTok) account as channels.
  2. Settings -> API -> create a key.
  3. python main.py --set-env BUFFER_API_KEY=...

API: one GraphQL endpoint, Bearer token.
  https://developers.buffer.com/reference.md
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Optional

from .errors import NotConfigured

API_URL = "https://api.buffer.com"
KEY_NAME = "BUFFER_API_KEY"
# X counts every link as 23 characters whatever its length.
X_LIMIT = 280
X_LINK_CHARS = 23
# TikTok's caption cap through the posting API.
TIKTOK_LIMIT = 2200
# How each service is named in messages.
LABELS = {"twitter": "X", "x": "X", "tiktok": "TikTok"}
_TIMEOUT = 60

_ORGS = "query { account { organizations { id name } } }"
_CHANNELS = ("query Channels($org: OrganizationId!) { "
             "channels(input: { organizationId: $org }) { id service name } }")
_CREATE = """mutation Create($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess { post { id } }
    ... on MutationError { message }
  }
}"""


class BufferError(RuntimeError):
    """Buffer answered, and the answer was no."""


def x_length(text: str) -> int:
    """Length as X counts it: every http(s) link is 23 characters."""
    import re

    return len(re.sub(r"https?://\S+", "x" * X_LINK_CHARS, text))


def fit_for_x(message: str, link: str) -> str:
    """The message cut to fit X, always keeping the link."""
    if x_length(message) <= X_LIMIT:
        return message
    head = message.replace(link, "").strip()
    room = X_LIMIT - X_LINK_CHARS - 2
    if len(head) > room:
        head = head[:room - 1].rstrip() + "…"
    return f"{head}\n{link}"


class BufferPublisher:
    """Posts to one channel connected in Buffer: X ("twitter", links and
    videos) or TikTok ("tiktok", videos only)."""

    supports_link_posts = True

    def __init__(self, config: dict = None, service: str = "twitter"):
        self.config = config or {}
        self.service = service
        self._channel = ""
        self.label = LABELS.get(service, service)
        # TikTok has no link posts; announcements stay on X.
        self.supports_link_posts = service in ("twitter", "x")

    def token(self) -> str:
        return os.environ.get(KEY_NAME, "").strip()

    def ready(self) -> bool:
        return bool(self.token())

    def _call(self, query: str, variables: dict = None) -> dict:
        body = json.dumps({"query": query,
                           "variables": variables or {}}).encode("utf-8")
        request = urllib.request.Request(
            API_URL, data=body, method="POST",
            headers={"Authorization": f"Bearer {self.token()}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
                answer = json.loads(response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            if exc.code in (401, 403):
                raise NotConfigured(
                    f"buffer: the API key was refused (HTTP {exc.code}). "
                    f"Make a new one in Buffer, then: python main.py "
                    f"--set-env {KEY_NAME}=...") from exc
            raise BufferError(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
            raise BufferError(str(exc)) from exc
        if answer.get("errors"):
            raise BufferError("; ".join(str(e.get("message", e))
                                        for e in answer["errors"])[:300])
        return answer.get("data") or {}

    def channel_id(self) -> str:
        """The connected channel for this service (X = "twitter")."""
        if self._channel:
            return self._channel
        wanted = {"twitter", "x"} if self.service in ("twitter", "x") \
            else {self.service}
        orgs = (self._call(_ORGS).get("account") or {}).get("organizations") or []
        for org in orgs:
            for channel in self._call(_CHANNELS, {"org": org["id"]}).get(
                    "channels") or []:
                if str(channel.get("service", "")).lower() in wanted:
                    self._channel = channel["id"]
                    return self._channel
        raise NotConfigured(
            f"buffer: no {self.label} channel is connected. Connect "
            f"{self.label} in Buffer (Channels -> Connect), then this posts "
            f"on its own.")

    def post_link(self, message: str, link: str) -> bool:
        if not self.ready():
            raise NotConfigured(
                f"buffer: no API key. python main.py --set-env {KEY_NAME}=...")
        text = message if link in message else f"{message}\n{link}"
        text = fit_for_x(text, link)
        data = self._call(_CREATE, {"input": {
            "channelId": self.channel_id(),
            "text": text,
            "schedulingType": "automatic",
            # Now: the spacing that decides WHEN is PublishGuard's job,
            # and a second scheduler in Buffer's queue would fight it.
            "mode": "shareNow",
        }})
        result = data.get("createPost") or {}
        if (result.get("post") or {}).get("id"):
            return True
        print(f"[Buffer] X post refused: {result.get('message') or result}")
        return False

    # ── native video (the clip queue) ────────────────────────────────

    def post_clip(self, video_path: str, caption: str,
                  dry_run: bool = False) -> Optional[str]:
        """Post the clip itself as a native video. Buffer only takes media by
        public URL, so the file goes to Cloudinary first (utils/
        cloud_host.py) and Buffer fetches it from there."""
        from utils import cloud_host

        if not self.ready():
            raise NotConfigured(
                f"buffer: no API key. python main.py --set-env {KEY_NAME}=...")
        if not cloud_host.ready():
            raise NotConfigured(
                f"buffer: {self.label} video needs a public link and there is no host "
                "set up. Free Cloudinary account, then: python main.py "
                "--set-env CLOUDINARY_URL=cloudinary://...")
        if not os.path.isfile(video_path):
            raise NotConfigured(f"buffer: no such file {video_path}")
        text = caption.strip()
        if self.label == "X":
            if x_length(text) > X_LIMIT:
                text = text[:X_LIMIT - 1].rstrip() + "…"
        elif len(text) > TIKTOK_LIMIT:
            text = text[:TIKTOK_LIMIT - 1].rstrip() + "…"
        if dry_run:
            print(f"[Buffer] DRY RUN - would post "
                  f"{os.path.basename(video_path)} to {self.label}")
            return "dry-run"
        try:
            url = cloud_host.host_video(video_path)
        except Exception as exc:
            print(f"[Buffer] Could not host the clip: {exc}")
            return None
        data = self._call(_CREATE, {"input": {
            "channelId": self.channel_id(),
            "text": text,
            "schedulingType": "automatic",
            "mode": "shareNow",
            "assets": [{"video": {"url": url}}],
        }})
        result = data.get("createPost") or {}
        post_id = (result.get("post") or {}).get("id")
        if post_id:
            return f"buffer post {post_id}"
        print(f"[Buffer] {self.label} video refused: "
              f"{result.get('message') or result}")
        return None
