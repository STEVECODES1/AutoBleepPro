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
import urllib.parse
from typing import Optional

from .rumble_checker import _fetch_plain, _looks_like_feed, _parse_rss

# Long enough for a multi-hour VOD on a domestic line.
_TIMEOUT = 60 * 180

# Reading one XML file, not a video.
_FEED_TIMEOUT = 30

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


def feed_url(channel_url: str) -> str:
    """The RSS address for a Rumble channel page, or "" if there isn't one.

    Rumble publishes every channel as `<page>/index.xml`, which is the
    same convention `rumble.rss_url` in config.json already uses for the
    upload-dedup check. Both `/user/<name>` and `/c/<name>` pages have
    one; anything else (YouTube, Twitch, a bare domain) returns "" and
    the caller stays on the yt-dlp route.
    """
    try:
        parts = urllib.parse.urlsplit(str(channel_url or "").strip())
    except ValueError:
        return ""
    if not parts.netloc.lower().endswith("rumble.com"):
        return ""

    path = parts.path.strip("/")
    if path.endswith("index.xml"):
        # Already a feed - the query string is a share token and the
        # feed does not want it.
        return urllib.parse.urlunsplit((parts.scheme or "https", parts.netloc,
                                        "/" + path, "", ""))

    pieces = [piece for piece in path.split("/") if piece]
    if len(pieces) != 2 or pieces[0] not in ("user", "c"):
        return ""
    return f"https://{parts.netloc}/{pieces[0]}/{pieces[1]}/index.xml"


def _fetch_impersonated(url: str) -> tuple:
    """(raw_bytes | None, why_not). The browser fingerprint, directly.

    Same mechanism yt-dlp's --impersonate uses; borrowed here because the
    feed is one plain GET and does not need yt-dlp at all.
    """
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
    raw = response.content
    if not _looks_like_feed(raw):
        return None, "a challenge page came back instead of the feed"
    return raw, ""


def _fetch_feed(url: str) -> tuple:
    """(raw_bytes | None, why_not). Plainest first, same as everything else."""
    raw, why = _fetch_plain(url)
    if raw is not None:
        return raw, ""
    first = why
    raw, why = _fetch_impersonated(url)
    if raw is not None:
        return raw, ""
    return None, f"direct: {first}; as a browser: {why}"


def short_id(link: str) -> str:
    """`https://rumble.com/v6abcde-some-title.html` -> `v6abcde`.

    Rumble's own ID is the leading token; the rest of the slug is the
    title at the time of posting and can change. Matching on the leading
    token is what makes the archive check survive a rename.
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


def feed_video_urls(channel_url: str, archive: str = "",
                    limit: int = DEFAULT_LIMIT) -> tuple:
    """(video_urls_newest_first, why_not). Reads the channel's RSS feed.

    This exists because yt-dlp's Rumble channel extractor currently walks
    five pages of a real channel and parses zero videos out of them - it
    reports success, so nothing looks broken, and no clip ever appears.
    The feed lists the same videos in the same order and is one request.
    Individual video downloads were never affected; only the listing was.
    """
    address = feed_url(channel_url)
    if not address:
        return [], "not a Rumble channel page"

    raw, why = _fetch_feed(address)
    if raw is None:
        return [], why

    try:
        videos = _parse_rss(raw)
    except Exception as exc:
        return [], f"feed fetched but could not be parsed ({exc})"

    taken = _archived_ids(archive) if archive else set()
    fresh = []
    for video in videos:
        if not video.url:
            continue
        if short_id(video.url) in taken:
            continue
        fresh.append(video.url)
        if len(fresh) >= max(1, limit):
            break
    return fresh, ""


def fetch_via_feed(channel_url: str, output_dir: str, extensions: tuple,
                   limit: int = DEFAULT_LIMIT, archive: str = "",
                   browser: str = "") -> tuple:
    """Download from the RSS listing instead of the channel page.

    Returns (new_paths, why_not) with the same contract as `fetch`.
    """
    os.makedirs(output_dir, exist_ok=True)
    archive = archive or os.path.join(output_dir, ARCHIVE_NAME)

    links, why = feed_video_urls(channel_url, archive, limit)
    if why:
        return [], why
    if not links:
        return [], ""

    print(f"[VODs] The channel feed lists {len(links)} video(s) to take.")
    before = _videos_in(output_dir, extensions)
    impersonate = have_impersonation()

    for link in links:
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

    The channel page first, because when it works it is one request and
    it handles every site. The RSS feed second, because on Rumble the
    page route currently succeeds while finding nothing: yt-dlp reads
    five pages, parses zero videos, and exits 0. That is indistinguishable
    from "nothing new" from the outside, so the feed is tried whenever
    the page produced no files - it is a single cheap GET, and if it also
    finds nothing new then nothing new is the honest answer.
    """
    grabbed, problem = fetch(url, output_dir, extensions, limit,
                             archive, browser)
    if grabbed:
        return grabbed, ""

    if not feed_url(url):
        # No feed to fall back to - whatever fetch said is the answer.
        return grabbed, problem

    print("[VODs] The channel page listed nothing. Trying the channel feed...")
    from_feed, why = fetch_via_feed(url, output_dir, extensions, limit,
                                    archive, browser)
    if from_feed:
        return from_feed, ""
    if problem:
        # The page failed outright; lead with that, and say the feed was
        # tried so the next step is not "try the feed".
        return [], f"{problem}\n         The channel feed did not work either: {why or 'it listed nothing new'}."
    if why:
        return [], (f"the channel page listed nothing and the feed could not "
                    f"be read: {why}")
    return [], ""
