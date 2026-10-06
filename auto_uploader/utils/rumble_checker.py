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

import json
import os
import re
import urllib.error
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


# ── Route three: read the channel PAGE ────────────────────────────────
#
# Rumble publishes no RSS at all. The feed URL this module was built
# around 404s, and every run has said so:
#
#   [Rumble] No channel feed - Rumble publishes no RSS. Using local
#            upload history instead.
#
# Local history only knows what THIS tool uploaded, so anything posted
# from a phone or the website is invisible to dedup, and a finished
# upload cannot be confirmed to have landed - which is the check that
# catches a Rumble upload that silently never published.
#
# The channel page itself has all of it, and it is behind Cloudflare,
# which is why plain urllib cannot have it. Firecrawl fetches through
# that and returns markdown. Measured against rumble.com/user/BinScripts
# on 2026-09-21: 2.4s, 33 videos, titles and URLs intact.
#
# Optional by design. No key means this route is skipped and the other
# two are unchanged - it must never become a new way for the check to
# fail.
_FIRECRAWL_URL = "https://api.firecrawl.dev/v2/scrape"
_FIRECRAWL_TIMEOUT = 90

# A published Rumble video is /v<id>-<slug>.html. The id always carries a
# digit, which is what keeps this off /videos and the other navigation
# paths that sit on every channel page.
_VIDEO_LINK = re.compile(
    r'\[([^\]\n]{3,150})\]\((https://rumble\.com/v[a-z0-9]*\d[a-z0-9]*-[^)\s]+\.html)\)')


def firecrawl_key() -> str:
    return (os.environ.get("FIRECRAWL_API_KEY") or "").strip()


def _parse_channel_markdown(markdown: str) -> list:
    """ExistingVideo records from a scraped channel page.

    Deduplicated on URL: a channel page shows its featured video twice,
    once in the banner and once in the grid, and counting it twice would
    make find_existing_video's "already uploaded" answer depend on
    which copy it happened to read.
    """
    seen = set()
    videos = []
    for title, url in _VIDEO_LINK.findall(markdown or ""):
        if url in seen:
            continue
        seen.add(url)
        # Rumble bolds the featured title in markdown; the asterisks are
        # formatting, not part of what the video is called, and leaving
        # them in stops it matching the title that was uploaded.
        clean = title.strip().strip("*").strip()
        if clean:
            videos.append(ExistingVideo(
                title=clean,
                video_id=url.rstrip("/").rsplit("/", 1)[-1],
                url=url,
            ))
    return videos


def _fetch_via_firecrawl(channel_url: str, key: str) -> tuple:
    """(videos | None, error_description)."""
    payload = json.dumps({
        "url": channel_url,
        "formats": ["markdown"],
        "onlyMainContent": False,
    }).encode()
    request = urllib.request.Request(
        _FIRECRAWL_URL, payload,
        {"Authorization": f"Bearer {key}",
         "Content-Type": "application/json",
         "User-Agent": _UA})
    try:
        with urllib.request.urlopen(request,
                                    timeout=_FIRECRAWL_TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
    except Exception as exc:
        return None, str(exc)[:200]

    if not isinstance(data, dict) or not data.get("success"):
        return None, f"scrape did not succeed: {str(data)[:160]}"
    markdown = ((data.get("data") or {}).get("markdown")) or ""
    if not markdown:
        return None, "scrape returned no markdown"
    videos = _parse_channel_markdown(markdown)
    if not videos:
        return None, (f"no video links in {len(markdown)} chars of page - "
                      "the channel page layout may have changed")
    return videos, ""


# ── Route four: the channel page through ScrapingBee ─────────────────
#
# Measured 2026-10-06 on rumble.com/user/BinScripts: a plain request and
# ScrapingBee's basic proxy are both refused by Cloudflare, and its
# premium (residential) proxy gets the page in under 2 s for 10 credits
# with no JavaScript - the channel's latest videos are already in the
# page, as a <script type="application/json"> {"items": [...]} block.
# A markdown conversion throws that block away, which is why a markdown
# scrape of the same page finds no videos at all.
#
# 10 credits a fetch against a free 1,000, and the uploader asks on every
# start - the keepalive restarts it several times a day - so the list is
# kept on disk for CACHE_HOURS. A video uploaded since is still caught by
# the local upload history, which is the primary defence anyway.
_SCRAPINGBEE_URL = "https://app.scrapingbee.com/api/v1/"
_SCRAPINGBEE_TIMEOUT = 90
CACHE_HOURS = 6
_CACHE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", "rumble_channel_cache.json")
_ITEMS_BLOCK = re.compile(
    r'<script type="application/json">\s*(\{"items":.*?)</script>', re.S)


def scrapingbee_key() -> str:
    return (os.environ.get("SCRAPINGBEE_API_KEY") or "").strip()


def _owner_path(channel_url: str) -> str:
    """'/user/BinScripts' from 'https://rumble.com/user/BinScripts'."""
    tail = (channel_url or "").split("rumble.com", 1)[-1]
    return "/" + tail.strip("/") if tail.strip("/") else ""


def _parse_channel_json(html: str, channel_url: str = "") -> list:
    """ExistingVideo records from the JSON the channel page carries.

    Only this channel's own videos: the same page lists other channels'
    live streams in its side menu, and a dedup check that matched one of
    those would skip a real upload.
    """
    owner = _owner_path(channel_url).lower()
    seen = set()
    videos = []
    for block in _ITEMS_BLOCK.findall(html or ""):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in (data.get("items") or []) if isinstance(data, dict) else []:
            if not isinstance(item, dict) or item.get("object_type") != "video":
                continue
            url = str(item.get("url") or "")
            by = str((item.get("by") or {}).get("relative_url") or "").lower()
            title = str(item.get("title") or "").strip()
            if not url or not title or url in seen:
                continue
            if owner and by and by != owner:
                continue
            seen.add(url)
            videos.append(ExistingVideo(
                title=title,
                video_id=url.rstrip("/").rsplit("/", 1)[-1],
                url=url,
            ))
    return videos


def _fetch_via_scrapingbee(channel_url: str, key: str) -> tuple:
    """(videos | None, error_description)."""
    from urllib.parse import urlencode

    query = urlencode({"api_key": key, "url": channel_url,
                       "render_js": "false", "premium_proxy": "true"})
    request = urllib.request.Request(f"{_SCRAPINGBEE_URL}?{query}",
                                     headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(
                request, timeout=_SCRAPINGBEE_TIMEOUT) as response:
            html = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        # The body says why (out of credits, bad key) - never the key.
        detail = exc.read().decode("utf-8", "replace")[:120]
        return None, f"HTTP {exc.code}: {' '.join(detail.split())}"
    except Exception as exc:
        return None, str(exc)[:160]
    videos = _parse_channel_json(html, channel_url)
    if not videos:
        return None, (f"no videos in {len(html)} chars of page - the "
                      "channel page layout may have changed")
    return videos, ""


def _read_cache(channel_url: str) -> list:
    """The channel's videos as last fetched, if that was recent enough."""
    import time

    try:
        with open(_CACHE_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or data.get("channel") != channel_url:
        return []
    if time.time() - float(data.get("fetched_at") or 0) > CACHE_HOURS * 3600:
        return []
    return [ExistingVideo(title=str(v.get("title", "")),
                          video_id=str(v.get("video_id", "")),
                          url=str(v.get("url", "")))
            for v in data.get("videos") or [] if isinstance(v, dict)]


def _write_cache(channel_url: str, videos: list) -> None:
    import time

    try:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        with open(_CACHE_PATH, "w", encoding="utf-8") as handle:
            json.dump({"channel": channel_url, "fetched_at": time.time(),
                       "videos": [{"title": v.title, "video_id": v.video_id,
                                   "url": v.url} for v in videos]},
                      handle, indent=1, ensure_ascii=False)
    except OSError:
        pass


def channel_page_for(rss_url: str, channel_url: str = "") -> str:
    """The channel page to scrape, derived from whatever is configured."""
    if channel_url:
        return channel_url.rstrip("/")
    # .../user/NAME/index.xml -> .../user/NAME
    return re.sub(r"/[^/]*\.xml$", "", (rss_url or "").rstrip("/"))


def fetch_rumble_videos(rss_url: str, cdp_url: str = None,
                        channel_url: str = "") -> list:
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

        # Read the page instead - the only routes that work today.
        page = channel_page_for(rss_url, channel_url)
        recent = _read_cache(page)
        if recent:
            return recent
        bee = scrapingbee_key()
        if bee:
            videos, why = _fetch_via_scrapingbee(page, bee)
            if videos:
                print(f"[Rumble] Read the channel page through ScrapingBee "
                      f"- {len(videos)} video(s) from {page}")
                _write_cache(page, videos)
                return videos
            attempts.append(f"scrapingbee: {why}")

        key = firecrawl_key()
        if key:
            videos, why = _fetch_via_firecrawl(page, key)
            if videos:
                print(f"[Rumble] No RSS, so read the channel page instead - "
                      f"{len(videos)} video(s) from {page}")
                return videos
            attempts.append(f"channel page: {why}")
        else:
            attempts.append("channel page: no FIRECRAWL_API_KEY in .env")

        raise RuntimeError("; ".join(attempts))

    try:
        return _parse_rss(raw)
    except ET.ParseError as exc:
        raise RuntimeError(f"feed fetched but could not be parsed ({exc})")
