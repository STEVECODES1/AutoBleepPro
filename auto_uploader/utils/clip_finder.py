"""
Find clips of you across platforms. Lists them; downloads nothing.

This exists because the obvious version of the job - fetch every clip
tagged "stackswopo" and republish it - is not yours to automate. The
footage may be of you, but the cut, the captions and the overlays are
the clipper's work, and a tag search also returns creators with no
connection to you at all. A pipeline doing that at ten a day is what
gets a channel terminated rather than warned.

What IS useful is knowing the clips exist. So this reads the metadata
that every one of these sites publishes - title, who posted it, when,
how many views - and hands back a list. You decide per clip: ask them,
repost with credit, or ignore it. That decision is the part a program
should not be making.

Nothing is downloaded, nothing is posted, and no login is used.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Optional

# yt-dlp is already required for recording; the metadata listing is the
# same tool with --flat-playlist, which fetches the index page only.
_TIMEOUT = 180


def ytdlp_command() -> list:
    """Same resolution the recorder uses - see tools/record_stream.py."""
    try:
        import yt_dlp  # noqa: F401
    except Exception:
        return ["yt-dlp"]
    return [sys.executable, "-m", "yt_dlp"]


def list_args(url: str, limit: int = 20) -> list:
    """Metadata only. --flat-playlist is what keeps this a listing.

    Without it yt-dlp resolves every entry, which is slower and starts
    looking like a scrape rather than reading an index.
    """
    return ytdlp_command() + [
        "--flat-playlist",
        "--dump-single-json",
        "--playlist-end", str(limit),
        "--no-warnings",
        "--ignore-errors",
        "--socket-timeout", "30",
        url,
    ]


def _entries(payload: dict) -> list:
    entries = payload.get("entries")
    if entries is None:
        # A single video URL rather than a listing.
        return [payload] if payload.get("id") else []
    return [e for e in entries if isinstance(e, dict)]


def find(url: str, limit: int = 20) -> tuple:
    """(clips, error). Each clip is a plain dict, ready to print."""
    try:
        completed = subprocess.run(
            list_args(url, limit), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=_TIMEOUT)
    except FileNotFoundError:
        return [], "yt-dlp is not installed"
    except subprocess.TimeoutExpired:
        return [], "timed out"
    except OSError as exc:
        return [], str(exc)

    raw = completed.stdout.decode("utf-8", "replace").strip()
    if not raw:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        # Several of these sites need a login to list anything, which is
        # a real answer rather than a failure to report as a crash.
        return [], (detail.splitlines()[-1] if detail else "nothing returned")

    try:
        payload = json.loads(raw)
    except ValueError:
        return [], "could not read the response"

    clips = []
    for entry in _entries(payload):
        clips.append({
            "title": (entry.get("title") or "").strip() or "(no title)",
            "uploader": (entry.get("uploader") or entry.get("channel")
                         or entry.get("uploader_id") or "?"),
            "url": entry.get("webpage_url") or entry.get("url") or "",
            "views": entry.get("view_count"),
            "duration": entry.get("duration"),
            "date": entry.get("upload_date") or "",
        })
    return clips, ""


def _views(clip: dict) -> int:
    value = clip.get("views")
    return int(value) if isinstance(value, (int, float)) else -1


def rank(clips: list) -> list:
    """Most-viewed first. Unknown view counts sink rather than lead."""
    return sorted(clips, key=_views, reverse=True)


def format_row(clip: dict) -> str:
    views = _views(clip)
    seen = f"{views:>9,}" if views >= 0 else "        ?"
    length = clip.get("duration")
    length = f"{int(length):>4}s" if isinstance(length, (int, float)) else "   ?"
    date = clip.get("date") or ""
    date = f"{date[:4]}-{date[4:6]}-{date[6:8]}" if len(date) == 8 else "         "
    title = clip["title"]
    if len(title) > 58:
        title = title[:57] + "…"
    return (f"  {seen}  {length}  {date}  {clip['uploader'][:18]:<18}  "
            f"{title}\n              {clip['url']}")


def write_report(clips: list, path: str) -> str:
    """A file to work through, since a terminal scrolls away."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Clips found {time.strftime('%Y-%m-%d %H:%M')}\n")
            f.write("Nothing here was downloaded or posted. Decide per clip: "
                    "ask the poster, repost with credit, or ignore.\n\n")
            for clip in clips:
                f.write(format_row(clip) + "\n\n")
    except OSError as exc:
        return f"could not write the report: {exc}"
    return ""


def run(sources: list, limit: int = 20,
        report_path: str = "") -> list:
    """Search every source, print a ranked table, return the clips."""
    found: list = []
    for url in sources:
        print(f"[Find] {url}")
        clips, error = find(url, limit)
        if error:
            print(f"[Find]   skipped - {error}")
            continue
        print(f"[Find]   {len(clips)} found")
        found.extend(clips)

    # The same clip is often on two of these at once.
    seen, unique = set(), []
    for clip in found:
        key = clip["url"] or (clip["uploader"], clip["title"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(clip)

    unique = rank(unique)
    if unique:
        print(f"\n{'views':>11}  {'len':>5}  {'date':<10}  "
              f"{'posted by':<18}  title")
        print("  " + "-" * 96)
        for clip in unique:
            print(format_row(clip))
    else:
        print("\n[Find] Nothing came back. Several of these platforms need a "
              "login to list search results at all, which this deliberately "
              "does not use.")

    if report_path and unique:
        problem = write_report(unique, report_path)
        print(f"\n[Find] {problem}" if problem
              else f"\n[Find] Written to {report_path}")
    return unique
