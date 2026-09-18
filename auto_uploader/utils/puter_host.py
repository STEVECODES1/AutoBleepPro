"""
utils/puter_host.py — Free web-host bridge via Puter.com.

Puter is an open-source, self-hostable "internet computer" with object storage
that gives every file a public URL. That makes it a free host for the clips
that need one (X/Twitter link posts, any platform whose API fetches a URL
server-side).

This module speaks Puter's REST API directly — no SDK needed, no heavy
dependency. The two things it does:

  1. put_file(local_path, remote_name)  →  public URL
     PUT the bytes to Puter's /storage endpoint, return the public link.

  2. delete_file(public_url)            →  True/False
     Remove a previously uploaded file by its URL.

Credentials (in .env):
    PUTER_API_KEY  — your Puter API key (free at puter.com/developers)

That is it. No server to run, no database, no account approval queue. The
host is the thing that needs credentials; every platform that consumes a
public URL just sees a link.

Usage in this project
    - X/Twitter free tier: upload the clip to Puter, post the Puter URL as
      the X post text (copy-paste on the free tier, or real post if an X
      Basic bearer token is also configured).
    - Any platform whose API needs a fetchable video_url: put it on Puter
      first, hand the Puter URL to the platform publisher.

Reference
    - https://github.com/heyPuter/puter
    - https://developer.puter.com  (REST API docs)
    - Puter object storage: https://developer.puter.com/storage/
"""

from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger("puter_host")

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False
    log.warning("puter_host: 'requests' not installed — pip install requests")

# Puter's REST API base. The storage endpoint is at /storage.
PUTER_API = "https://api.puter.com/v1"
_STORAGE_PATH = "/storage"

# How long we wait for a small file to land.
_UPLOAD_TIMEOUT = 120


class PuterHost:
    """A free public-host bridge backed by Puter object storage."""

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key or os.environ.get("PUTER_API_KEY", "").strip()

    def ready(self) -> bool:
        if not _REQUESTS_OK:
            log.error("puter_host: 'requests' not installed — "
                      "pip install requests")
            return False
        if not self._api_key:
            log.error(
                "puter_host: PUTER_API_KEY not set in .env. "
                "Get one at puter.com/developers (free).")
            return False
        return True

    def put_file(self, local_path: str, remote_name: Optional[str] = None
                 ) -> Optional[str]:
        """Upload a local file to Puter. Returns its public URL, or None."""
        if not self.ready():
            return None
        if not os.path.isfile(local_path):
            log.error("puter_host: no such file: %s", local_path)
            return None

        if remote_name is None:
            remote_name = os.path.basename(local_path)

        size = os.path.getsize(local_path)
        log.info("puter_host: uploading %s (%d bytes) as '%s' ...",
                 os.path.basename(local_path), size, remote_name)

        try:
            with open(local_path, "rb") as handle:
                data = handle.read()
        except OSError as exc:
            log.error("puter_host: could not read %s: %s", local_path, exc)
            return None

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/octet-stream",
        }
        url = f"{PUTER_API}{_STORAGE_PATH}/{remote_name}"

        try:
            r = requests.put(url, data=data, headers=headers,
                             timeout=_UPLOAD_TIMEOUT)
        except Exception as exc:
            log.error("puter_host: upload failed: %s", exc)
            return None

        if not r.ok:
            log.error("puter_host: upload rejected (HTTP %d): %s",
                      r.status_code, r.text[:300])
            return None

        # Puter returns the public URL in the response body.
        try:
            payload = r.json()
        except Exception:
            payload = {}

        public_url = (payload.get("url")
                      or payload.get("publicUrl")
                      or payload.get("fileUrl")
                      or "")

        if public_url and public_url.startswith("http"):
            log.info("puter_host: uploaded → %s", public_url)
            return public_url

        # Fallback: construct the expected public URL from Puter's pattern.
        # Puter stores files at https://api.puter.com/v1/storage/<name>
        # and serves them from a public CDN path. The exact public path
        # varies; when the API does not return it explicitly, try the
        # storage endpoint itself as the URL.
        fallback = f"{PUTER_API}{_STORAGE_PATH}/{remote_name}"
        log.warning("puter_host: API did not return a public URL; using "
                     "fallback: %s (verify this is reachable)", fallback)
        return fallback

    def delete_file(self, public_url: str) -> bool:
        """Remove a file that was uploaded to Puter. Best-effort."""
        if not self.ready():
            return False
        if not public_url:
            return False

        # Derive the storage API path from the public URL.
        # Puter public URLs look like:
        #   https://api.puter.com/v1/storage/<name>
        #   https://<cdn>.puter.com/...
        # We only know how to delete the /v1/storage/ form directly.
        if "api.puter.com/v1/storage/" in public_url:
            storage_path = public_url.split("api.puter.com/v1/storage/")[-1]
            # URL-decode the name in case it has special chars.
            from urllib.parse import unquote
            storage_path = unquote(storage_path)

            headers = {"Authorization": f"Bearer {self._api_key}"}
            url = f"{PUTER_API}{_STORAGE_PATH}/{storage_path}"
            try:
                r = requests.delete(url, headers=headers, timeout=30)
            except Exception as exc:
                log.warning("puter_host: delete failed: %s", exc)
                return False
            ok = r.ok or r.status_code == 204
            if ok:
                log.info("puter_host: deleted %s", public_url)
            else:
                log.warning("puter_host: delete returned HTTP %d: %s",
                            r.status_code, r.text[:200])
            return ok

        log.warning("puter_host: cannot delete %s — only /v1/storage/ URLs "
                     "are deletable programmatically", public_url)
        return False
