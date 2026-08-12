"""
Pull your own past VODs off a channel, so they can be clipped.

WHY THIS IS FINE AND THE TAG SEARCH IS NOT
------------------------------------------
This takes videos from a channel YOU own - your own uploads, your own
footage, your own edit. That is a backup of your own work, and clipping
it is the same thing the live pipeline already does with a stream it
recorded an hour ago. It is not the thing this project declines to do,
which is downloading other people's cuts of your footage from a tag
search and reposting them as yours.

So the URL belongs to you. Nothing here searches, and nothing here
discovers channels - it takes the one address it is given.

WHAT IT COSTS
-------------
A VOD is one to three hours and has never been through the censor pass,
so each one costs a download plus a full transcription before any clip
comes out of it. That is why the default limit is small: three VODs is an
evening, three hundred is a week and a full disk. An archive file means a
video is only ever fetched once, however many times this runs.
"""

from __future__ import annotations

import os
import subprocess
import sys
import re
import urllib.parse
import urllib.request
from typing import Optional

# Long enough for a multi-hour VOD on a domestic line.
_TIMEOUT = 60 * 180

# Reading one HTML page, not a video.
_FEED_TIMEOUT = 30

# A channel page holds dozens of videos, so one page covers any sane
# --limit. The cap stops a bad parse from walking a channel forever.
MAX_LISTING_PAGES = 5

# Cloudflare fingerprints the whole header set, not just User-Agent: a
# request claiming to be Chrome while sending none of the headers Chrome
# always sends is a stronger bot signal than an honest urllib one.
_BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/129.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",
    "Connection": "close",
}

ARCHIVE_NAME = "channel_vods_archive.txt"

# A channel holds hundreds. Each one is a download and a full Whisper
# pass, so taking them all by default would fill a disk overnight.
DEFAULT_LIMIT = 3


def ytdlp_command() -> list:
    """Same resolution the recorder uses - see tools/record_stream.py."""
    try:
        import yt_dlp  # noqa: F401
    except Exception:
        return ["yt-dlp"]
    return [sys.executable, "-m", "yt_dlp"]


def is_url(value: str) -> bool:
    return str(value or "").strip().lower().startswith(("http://", "https://"))


def have_impersonation() -> bool:
    """Whether yt-dlp can present a browser TLS fingerprint.

    Rumble sits behind Cloudflare, which answers a plain Python request
    with 403 however correct the URL is. curl_cffi is what makes the
    request look like a browser; without it, no channel page can be read.

    Importable is NOT the test. curl_cffi 0.16 imports perfectly and
    yt-dlp still reports every impersonate target as unavailable, so
    `pip install -U curl_cffi` silently breaks a working setup. Asking
    yt-dlp what it can actually do is the only answer that means
    anything.
    """
    try:
        done = subprocess.run(ytdlp_command() + ["--list-impersonate-targets"],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=60)
    except Exception:
        return False
    listed = done.stdout.decode("utf-8", "replace").lower()
    # A target line that says "unavailable" is the 0.16 symptom exactly.
    return "chrome" in listed and "(unavailable)" not in listed


def download_args(url: str, output_dir: str, archive: str,
                  limit: int = DEFAULT_LIMIT, impersonate: bool = False,
                  browser: str = "") -> list:
    """Newest `limit` videos from the channel, each fetched once ever."""
    return ytdlp_command() + ([
        # Cloudflare rejects the default fingerprint outright. Only added
        # on the retry, because a target that is unavailable makes yt-dlp
        # fail on sites that would have worked without it.
        "--impersonate", "chrome",
    ] if impersonate else []) + ([
        "--cookies-from-browser", browser,
    ] if browser else []) + [
        # Newest first: a channel page lists them that way, and the recent
        # ones are the ones worth clipping.
        "--playlist-end", str(max(1, limit)),
        # The archive is what makes this safe to run daily - a video
        # already taken is skipped without being downloaded again.
        "--download-archive", archive,
        # Emoji and punctuation in a stream title become a filename that
        # Windows cannot open; the recorder learned this the hard way.
        "--restrict-filenames",
        "--no-overwrites",
        "--no-playlist-reverse",
        "--ignore-errors",
        "--no-warnings",
        "--retries", "10",
        "--fragment-retries", "10",
        "--socket-timeout", "30",
        "-o", os.path.join(output_dir, "%(title)s [%(id)s].%(ext)s"),
        url,
    ]


def video_args(url: str, output_dir: str, archive: str,
               impersonate: bool = False, browser: str = "") -> list:
    """One single video, by its own URL. No playlist handling at all.

    This is the same download as `download_args` minus everything that
    walks a listing, because by the time this runs the listing has
    already been read out of the feed.
    """
    return ytdlp_command() + ([
        "--impersonate", "chrome",
    ] if impersonate else []) + ([
        "--cookies-from-browser", browser,
    ] if browser else []) + [
        "--no-playlist",
        "--download-archive", archive,
        "--restrict-filenames",
        "--no-overwrites",
        "--ignore-errors",
        "--no-warnings",
        "--retries", "10",
        "--fragment-retries", "10",
        "--socket-timeout", "30",
        "-o", os.path.join(output_dir, "%(title)s [%(id)s].%(ext)s"),
        url,
    ]


def listing_url(channel_url: str, page: int = 1) -> str:
    """A Rumble channel page address, or "" if this is not one.

    THERE IS NO RSS FEED. Rumble does not publish one - not
    `<page>/index.xml`, not `<page>/rss`, nothing. (config.json's
    `rumble.rss_url` is set to an index.xml address, which means the
    upload-dedup check has been quietly falling back to local history
    this whole time.) Every RSS "solution" for Rumble is a third-party
    site scraping the same HTML this reads directly, and routing your
    channel through someone else's server to get data Rumble already
    serves you is not an improvement.

    Only `/user/<name>` and `/c/<name>` pages have listings. Anything
    else - YouTube, Twitch, a single video - returns "" and the caller
    stays on the yt-dlp route, which works fine everywhere else.
    """
    try:
        parts = urllib.parse.urlsplit(str(channel_url or "").strip())
    except ValueError:
        return ""
    if not parts.netloc.lower().endswith("rumble.com"):
        return ""

    pieces = [piece for piece in parts.path.strip("/").split("/") if piece]
    if len(pieces) != 2 or pieces[0] not in ("user", "c"):
        return ""

    # The share token from the address bar (?e9s=...) is dropped; page is
    # the only query Rumble wants here.
    address = f"https://{parts.netloc}/{pieces[0]}/{pieces[1]}"
    return address if page <= 1 else f"{address}?page={int(page)}"


# Any Rumble video path, wherever it appears: href="/v6abc-x.html",
# href="https://rumble.com/v6abc-x.html", or inside a JSON blob as
# "url":"https:\/\/rumble.com\/v6abc-x.html".
#
# Matching the PATH rather than a particular attribute is deliberate. An
# href="..." pattern is a bet on one specific markup shape, and that bet
# has now lost twice in one evening - Rumble's page is React-rendered and
# the markup moves. The path itself is the part that cannot change
# without breaking every link Rumble has ever published.
#
# The lookahead keeps /videos out; it is the same guard yt-dlp's own
# Rumble matcher uses.
_VIDEO_PATH = re.compile(r'/(v(?!ideos)[0-9a-zA-Z][\w.-]*?\.html)')

# A Rumble ID is short - "v6abcde". Anything longer than this before the
# first dash is not an ID and the match is something else.
_MAX_ID_CHARS = 16

# A challenge page is served as HTTP 200, so the status code proves
# nothing and the body has to be looked at.
_CHALLENGE = ("just a moment", "cf-browser-verification", "challenge-platform",
              "cf_chl_opt")


def _fetch_html(url: str) -> tuple:
    """(text | None, why_not). Plain first, then the browser fingerprint.

    Impersonation is the same mechanism yt-dlp's --impersonate uses, and
    on this machine it demonstrably gets past Cloudflare - it read five
    channel pages. What it could not do was make sense of them, which is
    what the parsing below is for.
    """
    problems = []
    try:
        request = urllib.request.Request(url, headers=_BROWSER_HEADERS)
        with urllib.request.urlopen(request, timeout=_FEED_TIMEOUT) as response:
            html = response.read().decode("utf-8", "replace")
        if not _is_challenge(html):
            return html, ""
        problems.append("direct: Cloudflare served a challenge page")
    except Exception as exc:
        problems.append(f"direct: {exc}")

    html, why = _fetch_impersonated(url)
    if html is not None:
        return html, ""
    problems.append(f"as a browser: {why}")
    return None, "; ".join(problems)


def _is_challenge(html: str) -> bool:
    head = html[:4000].lower()
    return any(marker in head for marker in _CHALLENGE)


def _fetch_impersonated(url: str) -> tuple:
    """(text | None, why_not). curl_cffi presenting a real browser's TLS."""
    try:
        from curl_cffi import requests as cffi_requests
    except Exception as exc:
        return None, f"curl_cffi unavailable ({exc})"
    try:
        response = cffi_requests.get(url, impersonate="chrome",
                                     timeout=_FEED_TIMEOUT)
    except Exception as exc:
        return None, str(exc)
    if response.status_code != 200:
        return None, f"HTTP {response.status_code}"
    html = response.content.decode("utf-8", "replace")
    if _is_challenge(html):
        return None, "Cloudflare served a challenge page"
    return html, ""


def video_links_on(html: str, netloc: str = "rumble.com") -> list:
    """Every video URL on a channel page, in the order the page lists them.

    Page order is newest first, which is the order worth clipping in.
    Deduplicated because a page links the same video from the thumbnail,
    the title and again from the JSON the app is built from.
    """
    # Rumble embeds the same URLs inside escaped JSON as well as in the
    # markup; unescaping first means one pattern reads both.
    text = (html or "").replace("\\/", "/")

    seen = set()
    links = []
    for match in _VIDEO_PATH.finditer(text):
        path = match.group(1)
        if len(short_id(path)) > _MAX_ID_CHARS or len(short_id(path)) < 2:
            continue
        if path in seen:
            continue
        seen.add(path)
        links.append(f"https://{netloc}/{path}")
    return links


def describe_page(html: str) -> str:
    """What the page actually contains, for when nothing parsed out of it.

    Printed instead of leaving you to guess. The whole reason this route
    exists is that a parser went stale without saying so, so when this
    one goes stale it has to hand over the evidence needed to fix it in
    one go rather than one command at a time.
    """
    text = html or ""
    lines = [f"characters: {len(text)}"]

    for label, needle in (("rumble.com/v", "rumble.com/v"),
                          ('href="/v', 'href="/v'),
                          (".html links", ".html"),
                          ("videostream", "videostream"),
                          ("__NEXT_DATA__", "__NEXT_DATA__"),
                          ("sign in wall", "sign in")):
        lines.append(f"{label}: {text.lower().count(needle.lower())}")

    sample = re.findall(r'<a\b[^>]{0,160}?href="[^"]{1,120}"', text)[:5]
    if sample:
        lines.append("first links on the page:")
        lines.extend(f"  {piece}" for piece in sample)
    return "\n".join(f"         {line}" for line in lines)


def short_id(link: str) -> str:
    """`https://rumble.com/v6abcde-some-title.html` -> `v6abcde`.

    Rumble's own ID is the leading token; the rest of the slug is the
    title at the time of posting and can change under you. Matching on
    the leading token is what makes the archive check survive a retitle.
    """
    tail = str(link or "").rstrip("/").rsplit("/", 1)[-1]
    for cut in (".html", ".htm"):
        if tail.endswith(cut):
            tail = tail[: -len(cut)]
    return tail.split("-", 1)[0].split(".", 1)[0]


def _archived_ids(archive: str) -> set:
    """Every video ID yt-dlp has already recorded, however it spelled it.

    Lines look like `rumble v6abcde-some-title`, and the exact shape has
    changed between yt-dlp versions, so only the leading token is
    compared - see short_id.
    """
    taken = set()
    try:
        with open(archive, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                pieces = line.split()
                if len(pieces) >= 2:
                    taken.add(short_id(pieces[1]))
    except OSError:
        return set()
    return taken


def channel_video_urls(channel_url: str, archive: str = "",
                       limit: int = DEFAULT_LIMIT,
                       max_pages: int = MAX_LISTING_PAGES) -> tuple:
    """(video_urls_newest_first, why_not). Reads the channel page itself.

    This exists because yt-dlp's Rumble channel extractor currently walks
    five pages of a real channel and parses zero videos out of them - it
    reports success, so nothing looks broken, and no clip ever appears.
    Individual video downloads were never affected; only the listing was.

    Pages are walked only until `limit` new videos are found, so the
    common daily case is one request.
    """
    if not listing_url(channel_url):
        return [], "not a Rumble channel page"

    taken = _archived_ids(archive) if archive else set()
    netloc = urllib.parse.urlsplit(channel_url).netloc or "rumble.com"
    fresh = []
    seen = set()
    problems = ""

    for page in range(1, max(1, max_pages) + 1):
        html, why = _fetch_html(listing_url(channel_url, page))
        if html is None:
            problems = why
            break

        found = video_links_on(html, netloc)
        if not found:
            # An empty page is the end of the channel, not a failure -
            # unless it was the FIRST page, which means the layout
            # changed again and this parser needs looking at.
            if page == 1:
                problems = ("the channel page loaded but no videos could be "
                            "read out of it - Rumble's page layout has "
                            "changed")
                # Hand over the evidence rather than making you run a
                # diagnostic command to get it.
                print("[VODs] What came back instead:")
                print(describe_page(html))
            break

        for link in found:
            if link in seen:
                continue
            seen.add(link)
            if short_id(link) in taken:
                continue
            fresh.append(link)
            if len(fresh) >= max(1, limit):
                return fresh, ""

    if fresh:
        # Some pages read, enough videos found: a later page failing is
        # not worth throwing away what we have.
        return fresh, ""
    return [], problems


def fetch_via_listing(channel_url: str, output_dir: str, extensions: tuple,
                      limit: int = DEFAULT_LIMIT, archive: str = "",
                      browser: str = "") -> tuple:
    """Download from the parsed channel page instead of yt-dlp's listing.

    Returns (new_paths, why_not) with the same contract as `fetch`.
    """
    os.makedirs(output_dir, exist_ok=True)
    archive = archive or os.path.join(output_dir, ARCHIVE_NAME)

    links, why = channel_video_urls(channel_url, archive, limit)
    if why:
        return [], why
    if not links:
        return [], ""

    print(f"[VODs] Read {len(links)} video(s) off the channel page.")
    before = _videos_in(output_dir, extensions)
    impersonate = have_impersonation()

    for link in links:
        print(f"[VODs] {link}")
        try:
            subprocess.run(video_args(link, output_dir, archive,
                                      impersonate, browser),
                           timeout=_TIMEOUT)
        except FileNotFoundError:
            return [], "yt-dlp is not installed (pip install -U yt-dlp)"
        except subprocess.TimeoutExpired:
            # One slow VOD should not throw away the ones already here.
            print(f"[VODs] Gave up on {link} - it took too long.")
            continue
        except OSError as exc:
            return [], str(exc)

    arrived = sorted(_videos_in(output_dir, extensions) - before)
    return [os.path.join(output_dir, name) for name in arrived], ""


def _videos_in(folder: str, extensions: tuple) -> set:
    try:
        return {name for name in os.listdir(folder)
                if os.path.splitext(name)[1].lower() in extensions}
    except OSError:
        return set()


def _attempts(browser: str) -> list:
    """How to try, in order. Plainest first.

    Impersonation is not the default because a curl_cffi that is present
    but unusable makes yt-dlp fail on channels that plain requests would
    have read. Escalating only after a refusal keeps the common case
    simple and still gets past Cloudflare.
    """
    tries = [{"impersonate": False, "browser": "", "note": ""}]
    if have_impersonation():
        tries.append({"impersonate": True, "browser": "",
                      "note": "as a browser (Cloudflare refused the first try)"})
    if browser:
        tries.append({"impersonate": have_impersonation(), "browser": browser,
                      "note": f"signed in from {browser}"})
    return tries


def fetch(url: str, output_dir: str, extensions: tuple,
          limit: int = DEFAULT_LIMIT,
          archive: str = "", browser: str = "") -> tuple:
    """Download up to `limit` new videos. Returns (new_paths, error).

    `new_paths` is what arrived on THIS run, worked out by comparing the
    folder before and after rather than by parsing yt-dlp's output: the
    output format changes between versions and the folder does not.
    """
    os.makedirs(output_dir, exist_ok=True)
    archive = archive or os.path.join(output_dir, ARCHIVE_NAME)
    before = _videos_in(output_dir, extensions)
    failed = 0

    for attempt in _attempts(browser):
        if attempt["note"]:
            print(f"[VODs] Retrying {attempt['note']}...")
        try:
            done = subprocess.run(
                download_args(url, output_dir, archive, limit,
                              attempt["impersonate"], attempt["browser"]),
                timeout=_TIMEOUT)
        except FileNotFoundError:
            return [], "yt-dlp is not installed (pip install -U yt-dlp)"
        except subprocess.TimeoutExpired:
            return [], "the download took too long and was stopped"
        except OSError as exc:
            return [], str(exc)

        arrived = sorted(_videos_in(output_dir, extensions) - before)
        if arrived:
            return [os.path.join(output_dir, name) for name in arrived], ""
        if done.returncode == 0:
            # Read the page fine, nothing new on it.
            return [], ""
        failed += 1

    hint = ("nothing downloaded - Rumble answered 403, which is Cloudflare "
            "refusing the request rather than a wrong URL.")
    if not have_impersonation():
        return [], (hint + " Install the browser fingerprint it wants - "
                    "PINNED, because 0.16 imports fine and still reports "
                    "every impersonate target unavailable:\n"
                    "         python -m pip install \"curl_cffi==0.15.0\"")
    if not browser:
        return [], (hint + " The browser fingerprint did not get past it "
                    "either, so try it signed in - add --browser chrome "
                    "(or firefox/edge), using a browser you are logged into "
                    "Rumble on.")
    return [], (hint + " Signed-in cookies did not work either. The channel "
                "page may need a different browser, or Rumble is blocking "
                "this machine for now - try again later.")


def fetch_channel(url: str, output_dir: str, extensions: tuple,
                  limit: int = DEFAULT_LIMIT,
                  archive: str = "", browser: str = "") -> tuple:
    """Get new videos off a channel by whichever route works.

    yt-dlp's own listing first, because when it works it is one command
    and it handles every site. Reading the channel page directly second,
    because on Rumble yt-dlp's listing currently succeeds while finding
    nothing: it walks five pages, parses zero videos, and exits 0. That
    is indistinguishable from "nothing new" from the outside, so the
    direct read is tried whenever the listing produced no files - it is
    one cheap GET, and if it also finds nothing new then nothing new is
    the honest answer.
    """
    grabbed, problem = fetch(url, output_dir, extensions, limit,
                             archive, browser)
    if grabbed:
        return grabbed, ""

    if not listing_url(url):
        # Not a Rumble channel - there is nothing this route can add.
        return grabbed, problem

    print("[VODs] yt-dlp listed nothing. Reading the channel page directly...")
    directly, why = fetch_via_listing(url, output_dir, extensions, limit,
                                      archive, browser)
    if directly:
        return directly, ""
    if problem:
        # yt-dlp failed outright; lead with that, and say the page was
        # read too so the next step is not "try reading the page".
        return [], (f"{problem}\n         Reading the channel page directly "
                    f"did not work either: "
                    f"{why or 'it listed nothing new'}.")
    if why:
        return [], f"yt-dlp listed nothing, and {why}"
    return [], ""
