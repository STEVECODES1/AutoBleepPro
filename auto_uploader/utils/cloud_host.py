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


def fallback_on() -> bool:
    """Litterbox when Cloudinary cannot (CLOUD_HOST_FALLBACK=0 turns it
    off)."""
    return os.environ.get("CLOUD_HOST_FALLBACK", "1").strip() not in (
        "0", "false", "no", "off")


def ready() -> bool:
    return credentials() is not None or fallback_on()


# ── the free fallback ─────────────────────────────────────────────────
#
# Cloudinary's free plan is 25 GB of delivery a month. Buffer fetches
# each clip once - about 60 MB - and with clips cut live as well as from
# the VOD that is ~25 GB a month: the allowance runs out, it does not
# bill, and every X and TikTok post after that fails. Litterbox (the
# temporary side of catbox.moe) takes a file up to 1 GB with no account
# and no key, keeps it 24 hours - Buffer fetches within minutes - and
# costs nothing.
LITTERBOX_URL = "https://litterbox.catbox.moe/resources/internals/api.php"
LITTERBOX_MAX_BYTES = 1000 * 1024 * 1024
# Cloudinary said it is over its limit: not asked again until then.
_RESTING = {"until": 0.0, "why": ""}
_OVER_LIMIT = ("limit", "exceeded", "quota", "usage", "420")


def _litterbox(path: str) -> str:
    import requests

    if os.path.getsize(path) > LITTERBOX_MAX_BYTES:
        raise RuntimeError(f"{os.path.basename(path)} is over 1 GB")
    with open(path, "rb") as handle:
        r = requests.post(LITTERBOX_URL,
                          data={"reqtype": "fileupload", "time": "24h"},
                          files={"fileToUpload": (os.path.basename(path),
                                                  handle, "video/mp4")},
                          timeout=600)
    url = (r.text or "").strip()
    if not r.ok or not url.startswith("https://"):
        raise RuntimeError(f"Litterbox HTTP {r.status_code}: {url[:200]}")
    return url


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
    """Upload `path`; return its public https URL. Raises on failure.

    Cloudinary first (it is the steadier host); Litterbox when Cloudinary
    is not set up, refuses, or is over its month."""
    problem = ""
    if credentials() and time.time() >= _RESTING["until"]:
        try:
            return _cloudinary(path)
        except Exception as exc:
            problem = str(exc)
            if any(sign in problem.lower() for sign in _OVER_LIMIT):
                _RESTING.update(until=time.time() + 6 * 3600, why=problem)
    elif credentials():
        problem = f"Cloudinary is over its limit ({_RESTING['why'][:80]})"
    else:
        problem = "no CLOUDINARY_URL in .env"
    if not fallback_on():
        raise RuntimeError(problem)
    try:
        url = _litterbox(path)
    except Exception as exc:
        raise RuntimeError(f"{problem}; and the free fallback failed too: "
                           f"{exc}") from exc
    print(f"[Clips] Hosted on Litterbox (free) - {problem[:100]}")
    return url


def _cloudinary(path: str) -> str:
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
