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
from typing import Optional

# Long enough for a multi-hour VOD on a domestic line.
_TIMEOUT = 60 * 180

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
    """
    try:
        import curl_cffi  # noqa: F401
    except Exception:
        return False
    return True


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
        return [], (hint + " Install the browser fingerprint it wants:\n"
                    "         python -m pip install -U curl_cffi yt-dlp")
    if not browser:
        return [], (hint + " The browser fingerprint did not get past it "
                    "either, so try it signed in - add --browser chrome "
                    "(or firefox/edge), using a browser you are logged into "
                    "Rumble on.")
    return [], (hint + " Signed-in cookies did not work either. The channel "
                "page may need a different browser, or Rumble is blocking "
                "this machine for now - try again later.")
