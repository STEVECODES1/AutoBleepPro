"""
Checks the public Rumble channel RSS feed for videos that are already
uploaded, so backfilling can skip Rumble the same way it skips YouTube.

Fetch strategy: plain HTTPS first, but Rumble sits behind Cloudflare,
which sometimes challenges non-browser clients. When that happens and a
CDP browser is configured (the same logged-in Chrome the uploader
attaches to), the feed is fetched through that browser's own request
context instead - real browser, real cookies, no challenge.

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


def _parse_rss(xml_text: str) -> list:
    """Parse an RSS feed's <item> entries into ExistingVideo records."""
    videos = []
    root = ET.fromstring(xml_text)
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if title:
            videos.append(ExistingVideo(title=title, video_id=link.rstrip("/").rsplit("/", 1)[-1], url=link))
    return videos


def fetch_rumble_videos(rss_url: str, cdp_url: str = None) -> list:
    """Fetch + parse the channel RSS. Raises on total failure so callers
    can decide how loudly to react (the local hash/title checks still
    protect against re-uploads even when this is unavailable)."""
    xml_text = None
    plain_error = None
    try:
        request = urllib.request.Request(rss_url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(request, timeout=20) as response:
            xml_text = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        plain_error = exc

    if xml_text is None and cdp_url:
        # Cloudflare challenged the plain request - go through the user's
        # real, already-open Chrome session instead.
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(cdp_url)
            try:
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                response = context.request.get(rss_url, timeout=30_000)
                if response.ok:
                    xml_text = response.text()
            finally:
                browser.close()

    if xml_text is None:
        raise RuntimeError(f"Could not fetch Rumble RSS feed ({plain_error})")

    if "<rss" not in xml_text and "<feed" not in xml_text:
        # Got HTML (e.g. a Cloudflare challenge page) instead of a feed.
        raise RuntimeError("Rumble RSS fetch returned a non-feed page (likely a Cloudflare challenge)")

    return _parse_rss(xml_text)
