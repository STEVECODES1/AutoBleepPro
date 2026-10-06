"""
utils/cloud_host.py - a public link to a clip, for services that only take
media by URL (Buffer).

Cloudinary's free plan: 25 credits a month, 1 credit = 1 GB of storage or
1 GB of delivery, no card. A clip is 20-60 MB and the service fetches it
once, so this is hundreds of clips a month. Each upload is deleted a day
later - long enough for the post to have gone out, short enough that the
storage never fills.

Setup (once): cloudinary.com -> sign up -> Dashboard -> "API environment
variable" (cloudinary://KEY:SECRET@CLOUD), then:
    python main.py --set-env CLOUDINARY_URL=cloudinary://...
or the three apart (Settings -> API Keys):
    python main.py --set-env CLOUDINARY_CLOUD_NAME=... \
        CLOUDINARY_API_KEY=... CLOUDINARY_API_SECRET=...
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Optional, Tuple
from urllib.parse import urlparse

KEEP_HOURS = 24
# The free plan's cap on one video upload (100 MB).
MAX_BYTES = 100 * 1024 * 1024
FOLDER = "autobleep"
_LEDGER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))), "logs", "cloud_host.json")


def credentials() -> Optional[Tuple[str, str, str]]:
    """(cloud, key, secret) from CLOUDINARY_URL, or from the three separate
    CLOUDINARY_CLOUD_NAME / CLOUDINARY_API_KEY / CLOUDINARY_API_SECRET
    (the console shows them apart, and its copy of the URL has
    placeholders in it). None when neither is complete."""
    raw = os.environ.get("CLOUDINARY_URL", "").strip()
    if raw.startswith("cloudinary://") and "<" not in raw:
        parsed = urlparse(raw)
        if parsed.hostname and parsed.username and parsed.password:
            return parsed.hostname, parsed.username, parsed.password
    parts = tuple(os.environ.get(f"CLOUDINARY_{name}", "").strip()
                  for name in ("CLOUD_NAME", "API_KEY", "API_SECRET"))
    if all(parts) and not any("<" in part for part in parts):
        return parts
    return None


def ready() -> bool:
    return credentials() is not None


def _sign(params: dict, secret: str) -> str:
    """Cloudinary's signature: sorted key=value pairs joined by &, plus the
    secret, SHA-1."""
    joined = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return hashlib.sha1((joined + secret).encode("utf-8")).hexdigest()


def _ledger() -> list:
    try:
        with open(_LEDGER, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_ledger(items: list) -> None:
    try:
        os.makedirs(os.path.dirname(_LEDGER), exist_ok=True)
        with open(_LEDGER, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=1)
    except OSError:
        pass


def delete(public_id: str) -> bool:
    creds = credentials()
    if not creds:
        return False
    import requests

    cloud, key, secret = creds
    params = {"public_id": public_id, "timestamp": int(time.time())}
    try:
        r = requests.post(
            f"https://api.cloudinary.com/v1_1/{cloud}/video/destroy",
            data=dict(params, api_key=key, signature=_sign(params, secret)),
            timeout=60)
        return r.ok
    except Exception:
        return False


def sweep(now: float = 0.0) -> int:
    """Delete uploads older than KEEP_HOURS. Returns how many went."""
    now = now or time.time()
    keep, gone = [], 0
    for item in _ledger():
        if now - float(item.get("at", now)) > KEEP_HOURS * 3600:
            if delete(item.get("public_id", "")):
                gone += 1
                continue
        keep.append(item)
    _save_ledger(keep)
    return gone


def host_video(path: str) -> str:
    """Upload `path`; return its public https URL. Raises on failure."""
    creds = credentials()
    if not creds:
        raise RuntimeError("no CLOUDINARY_URL in .env")
    import requests

    size = os.path.getsize(path)
    if size > MAX_BYTES:
        raise RuntimeError(f"{os.path.basename(path)} is {size >> 20} MB; "
                           f"Cloudinary's free plan takes up to "
                           f"{MAX_BYTES >> 20} MB per video")
    sweep()
    cloud, key, secret = creds
    params = {"folder": FOLDER, "timestamp": int(time.time())}
    with open(path, "rb") as handle:
        r = requests.post(
            f"https://api.cloudinary.com/v1_1/{cloud}/video/upload",
            data=dict(params, api_key=key, signature=_sign(params, secret)),
            files={"file": (os.path.basename(path), handle, "video/mp4")},
            timeout=600)
    if not r.ok:
        raise RuntimeError(f"Cloudinary upload HTTP {r.status_code}: "
                           f"{r.text[:200]}")
    answer = r.json()
    url = str(answer.get("secure_url", "") or "")
    if not url.startswith("https://"):
        raise RuntimeError(f"Cloudinary returned no URL: {answer}")
    items = _ledger()
    items.append({"public_id": answer.get("public_id", ""),
                  "at": time.time(), "file": os.path.basename(path)})
    _save_ledger(items)
    return url
