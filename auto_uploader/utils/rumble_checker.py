"""
Checks the public Rumble channel RSS feed for videos that are already
uploaded, so backfilling can skip Rumble the same way it skips YouTube.

Fetch strategy: plain HTTPS first, but Rumble sits behind Cloudflare,
which challenges clients that don't look like a browser. When the plain
request comes back as anything other than a feed - a connection error OR
a challenge page, which returns HTTP 200 and so looks like success - and
a CDP browser is configured (the same logged-in Chrome the uploader
attaches to), the feed is refetched through that browser's own request
context: real browser, real cookies, no challenge.

Reuses ExistingVideo/find_existing_video from youtube_checker: the
matching logic is platform-agnostic (this channel's titles always carry
the stream date, in every era of title style).
"""

import urllib.request
import xml.etree.ElementTree as ET

from .youtube_checker import ExistingVideo

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)

# Cloudflare fingerprints on the whole header set, not just User-Agent. A
# request claiming to be Chrome while sending none of the headers Chrome
# always sends is a stronger bot signal than an honest urllib UA, so the
# rest of a normal feed request goes with it.
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",
    "Connection": "close",
}


def _looks_like_feed(raw: bytes) -> bool:
    """True if this is XML we can parse, not a challenge/error page.

    Checked on the first chunk only: a challenge page is HTML end to end,
    so the opening bytes settle it, and this avoids scanning megabytes.
    """
    head = raw[:2048].lstrip()
    return head.startswith(b"<?xml") or b"<rss" in head or b"<feed" in head


def _parse_rss(raw: bytes) -> list:
    """Parse an RSS feed's <item> entries into ExistingVideo records.

    Parsed from BYTES so ElementTree honours the feed's own encoding
    declaration. Decoding as UTF-8 first, as this used to, silently
    mangled any feed that wasn't UTF-8 - an accented stream title came
    back full of replacement characters and then failed to match the
    local history, which reads as "not uploaded yet".
    """
    videos = []
    root = ET.fromstring(raw)
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if title:
            videos.append(ExistingVideo(
                title=title,
                video_id=link.rstrip("/").rsplit("/", 1)[-1],
                url=link,
            ))
    return videos


def _fetch_plain(rss_url: str) -> tuple:
    """(raw_bytes | None, error_description)."""
    try:
        request = urllib.request.Request(rss_url, headers=_HEADERS)
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
    except Exception as exc:
        return None, str(exc)
    if not _looks_like_feed(raw):
        return None, "Cloudflare served a challenge page instead of the feed"
    return raw, ""


def _fetch_via_browser(rss_url: str, cdp_url: str) -> tuple:
    """(raw_bytes | None, error_description). Uses the already-open Chrome."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None, "playwright not installed"

    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(cdp_url)
            try:
                context = (browser.contexts[0] if browser.contexts
                           else browser.new_context())
                response = context.request.get(rss_url, timeout=30_000)
                if not response.ok:
                    return None, f"browser fetch returned HTTP {response.status}"
                raw = response.body()
            finally:
                # Disconnects from the attached Chrome; it does not close
                # the user's window, which we did not launch.
                try:
                    browser.close()
                except Exception:
                    pass
    except Exception as exc:
        return None, f"could not reach Chrome on {cdp_url} ({exc})"

    if not _looks_like_feed(raw):
        return None, "browser fetch also returned a non-feed page"
    return raw, ""


def fetch_rumble_videos(rss_url: str, cdp_url: str = None) -> list:
    """Fetch + parse the channel RSS.

    Raises RuntimeError listing every route that was tried, so callers can
    decide how loudly to react. The local hash/title history still
    protects against re-uploads when this is unavailable - the feed only
    adds cover for videos uploaded to Rumble outside this tool.
    """
    attempts = []

    raw, why = _fetch_plain(rss_url)
    if raw is None:
        attempts.append(f"direct: {why}")

    if raw is None and cdp_url:
        raw, why = _fetch_via_browser(rss_url, cdp_url)
        if raw is None:
            attempts.append(f"browser: {why}")

    if raw is None:
        if not cdp_url:
            attempts.append("browser: no rumble.cdp_url configured")
        raise RuntimeError("; ".join(attempts))

    try:
        return _parse_rss(raw)
    except ET.ParseError as exc:
        raise RuntimeError(f"feed fetched but could not be parsed ({exc})")
