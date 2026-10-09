"""
utils/stream_links.py - where each stream's FULL upload lives, so every
clip cut from it can point there.

A clip is the advert; the full stream is the watch time. A viewer who
liked forty seconds is one tap from three hours - if the tap is there.
Each finished VOD upload is noted here (its YouTube and Rumble links,
by stream title and date), and a clip's description or caption looks up
the stream it came from by the note beside the clip (<clip>_source.json:
stream_title, stream_date).

A live clip posted before its stream has finished uploading finds
nothing yet and posts with the channel links instead - and anything
posted later (X and TikTok are spaced by hours) finds the VOD by then.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Iterable, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_STORE = os.path.join(_ROOT, "logs", "stream_links.json")
# A stream's clips keep posting for days; a title reused weeks later is
# a different stream.
MAX_AGE_S = 14 * 86400


def _normal(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _same_day(a, b) -> bool:
    """One stream date however it was written: "10/8/26" (the old
    date_style) and "10-08-26" (the current one) are the same day, and
    clips cut before the change still carry the old form."""
    a, b = str(a or ""), str(b or "")
    if a == b:
        return True
    da, db = re.findall(r"\d+", a), re.findall(r"\d+", b)
    if len(da) != 3 or len(db) != 3:
        return False
    return ([int(x) for x in da[:2]] + [int(da[2]) % 100]
            == [int(x) for x in db[:2]] + [int(db[2]) % 100])


def _load(store: str) -> list:
    try:
        with open(store, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _url(value) -> str:
    value = str(value or "").strip()
    return value if value.startswith("https://") else ""


def remember(title: str, date: str, results: dict,
             store: str = "") -> bool:
    """Note a finished stream's upload links. True if any were noted."""
    store = store or DEFAULT_STORE
    youtube = _url((results or {}).get("youtube"))
    rumble = _url((results or {}).get("rumble"))
    if rumble:
        # Rumble's upload page carries links to other videos, and one has
        # gone down as a VOD's result before (v7glom0-monkey-trolling-
        # on-omegle for "thotbreaker"). Every clip of the stream would
        # have linked to it.
        try:
            from utils.channel_vods import slug_matches_title
        except ImportError:
            slug_matches_title = None
        if slug_matches_title and slug_matches_title(
                rumble, f"{title} {date}") is False:
            rumble = ""
    if not (youtube or rumble) or not _normal(title):
        return False
    everything = _load(store)
    same = [e for e in everything
            if _normal(e.get("title")) == _normal(title)
            and _same_day(e.get("date"), date)]
    # A one-platform re-upload (--only rumble) brings one link; the other
    # one noted earlier for the same stream stays.
    for earlier in sorted(same, key=lambda e: float(e.get("at", 0))):
        youtube = youtube or _url(earlier.get("youtube"))
        rumble = rumble or _url(earlier.get("rumble"))
    entries = [e for e in everything if e not in same]
    entries.append({"title": title, "date": date, "youtube": youtube,
                    "rumble": rumble, "at": time.time()})
    entries = entries[-200:]
    try:
        os.makedirs(os.path.dirname(store), exist_ok=True)
        with open(store + ".tmp", "w", encoding="utf-8") as handle:
            json.dump(entries, handle, indent=1, ensure_ascii=False)
        os.replace(store + ".tmp", store)
    except OSError:
        return False
    return True


def link_for(stream_title: str, stream_date: str = "", platform: str = "",
             store: str = "", now: Optional[float] = None) -> str:
    """The full stream's link for a clip on `platform`, or "".

    Rumble clips link the Rumble upload (the uncut one, and the same
    site); everywhere else the YouTube one, then Rumble."""
    store = store or DEFAULT_STORE
    wanted = _normal(stream_title)
    if not wanted:
        return ""
    now = time.time() if now is None else now
    found = [e for e in _load(store)
             if _normal(e.get("title")) == wanted
             and now - float(e.get("at", 0)) < MAX_AGE_S
             and (not stream_date or not e.get("date")
                  or _same_day(e.get("date"), stream_date))]
    if not found:
        return ""
    entry = max(found, key=lambda e: float(e.get("at", 0)))
    order = ("rumble", "youtube") if platform == "rumble" else ("youtube", "rumble")
    for key in order:
        if _url(entry.get(key)):
            return entry[key]
    return ""


def source_of(clip_path: str, folders: Iterable[str] = ()) -> dict:
    """The <clip>_source.json note for a clip, wherever it is kept."""
    stem = os.path.splitext(os.path.basename(clip_path or ""))[0]
    if stem.startswith("_vertical_"):
        stem = stem[len("_vertical_"):]
    stem = re.sub(r"_CENSORED_.*$", "", stem)
    places = [os.path.dirname(clip_path or "")] + [f for f in folders if f]
    places.append(os.path.join(_ROOT, "watch_folder"))
    for place in places:
        path = os.path.join(place, stem + "_source.json")
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            continue
    return {}


def link_for_clip(clip_path: str, platform: str = "",
                  folders: Iterable[str] = (),
                  store: str = "") -> str:
    note = source_of(clip_path, folders)
    return link_for(note.get("stream_title", ""), note.get("stream_date", ""),
                    platform, store)
