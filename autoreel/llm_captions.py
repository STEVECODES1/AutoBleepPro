"""One caption per platform, written for that platform.

Until now every platform got the same sentence out of one template. That
is not how any of them work: X demotes a post carrying a dozen hashtags
and cuts it off at 280 characters, Instagram rewards them, Facebook reads
as spam with either, and a Short wants something closer to a title than a
caption. Posting the same words to all four is the single most visible
mark of an automated account - the thing every platform's ranking is
tuned to find.

ONE call for all of them, not one per platform. The clip is the same clip;
asking four times costs four times as much, takes four times as long, and
produces four answers that were each written without knowing what the
others said.

Everything here degrades to the existing template on any failure. A
caption that reads a bit generic is a bad post; no caption is no post.
"""

from __future__ import annotations

import json
import os
from typing import Optional

# What each platform actually wants. Not style preferences - these are
# the rules their rankings enforce.
# Hashtags are NEVER the model's job - hashtags_for picks them from the
# clip and TAG_LIMITS sizes them per platform, and every brief here says
# so. Telling the model "no hashtags" for a platform the code then adds
# tags to is a contradiction the model cannot see and the reader can.
PLATFORM_BRIEFS = {
    "zernio_twitter": (
        "X: under 200 characters so nothing is cut off, because two "
        "hashtags are appended afterwards and they need the room. Blunt "
        "and funny; no emoji strings, no 'link in bio'. Do not write any "
        "hashtags yourself."),
    "instagram": (
        "Instagram: one or two short lines, conversational, an emoji or "
        "two is fine. Hashtags are appended afterwards - do not write "
        "any yourself."),
    "facebook": (
        "Facebook: one plain sentence, no emoji. The audience is older "
        "and reads a wall of text as spam. Hashtags are appended "
        "afterwards - do not write any yourself."),
    "youtube_shorts": (
        "YouTube Shorts: a TITLE more than a caption. Under 70 "
        "characters, says what happens. Hashtags are appended "
        "afterwards - do not write any yourself."),
}

# TikTok is deliberately absent. The account is off and staying off, and
# a brief here would put it back in every caption request - five answers
# asked for, four ever read, on every clip forever.

PROMPT = """\
You write captions for clips from a live streamer's channel.

The clip's title, and what is said in it, are below. Write ONE caption
per platform named. Each must be about THIS clip - never generic, never
"check this out", never describing the video as a video.

Match the streamer's own voice: casual, blunt, funny. Do not clean it up
into marketing copy, and do not add slurs or insults that are not in the
clip.

Answer as JSON: {"captions": {"<platform>": "<caption>", ...}}
No other text.

TITLE: %(title)s

WHAT IS SAID: %(transcript)s

PLATFORMS:
%(briefs)s
"""

# Enough of the clip for the model to know what happened, and not so much
# that a two-minute clip costs a page of tokens per platform.
MAX_TRANSCRIPT_CHARS = 1200


def _sidecar(video_path: str) -> str:
    return os.path.splitext(video_path or "")[0] + "_captions.json"


def fingerprint(video_path: str) -> str:
    """What makes this file THIS clip, cheaply.

    The filename does not. One VOD cut four times produces
    "... - Clip 02.mp4" four times, from four completely different
    moments, each one overwriting the last - and everything written
    beside a clip is keyed by that name. So run four's Clip 02 was
    reading run one's caption, written about something else entirely.

    Size and mtime, not a content hash: this is checked on every drain of
    every clip and only has to notice that the bytes CHANGED, which a
    re-cut always does.
    """
    try:
        stat = os.stat(video_path)
    except OSError:
        return ""
    return f"{stat.st_size}-{int(stat.st_mtime)}"


def cached(video_path: str) -> dict:
    """Captions already written for THIS clip, or {}.

    Written once and reused: a clip is offered to each platform at a
    different time, hours apart, and asking again per drain would be one
    API call per platform after all.

    Refused when the file no longer matches the one they were written
    for. A stale caption is worse than no caption - no caption falls back
    to the template, a stale one confidently describes a different clip.
    """
    try:
        with open(_sidecar(video_path), "r", encoding="utf-8") as handle:
            found = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(found, dict):
        return {}
    stamped = str(found.get("_clip", ""))
    if stamped != fingerprint(video_path):
        # Written for a different cut of this filename.
        return {}
    return {str(k): str(v) for k, v in found.items()
            if not k.startswith("_") and isinstance(v, str) and v.strip()}


def remember(video_path: str, captions: dict) -> None:
    """Never fatal: a caption that cannot be cached is still a caption."""
    if not captions:
        return
    body = {k: v for k, v in captions.items() if not k.startswith("_")}
    body["_clip"] = fingerprint(video_path)
    try:
        with open(_sidecar(video_path), "w", encoding="utf-8") as handle:
            json.dump(body, handle, indent=2, ensure_ascii=False)
    except OSError:
        pass


def _parse(raw: str, platforms) -> dict:
    from .llm_highlights import parse_reply  # noqa: F401  (fence handling)
    import re

    if not raw:
        return {}
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    block = data.get("captions")
    if not isinstance(block, dict):
        # A model that answered with the mapping alone has still done the
        # job asked of it.
        block = data
    wanted = set(platforms)
    return {k: v.strip() for k, v in block.items()
            if k in wanted and isinstance(v, str) and v.strip()}


def write_captions(title: str, transcript: str, platforms,
                   provider: str = "", model: str = "",
                   ask=None) -> dict:
    """{platform: caption} from a model, or {} if none could be had."""
    from .llm_highlights import (all_available, api_key, asker_for,
                                 available, resolve_model)

    platforms = [p for p in platforms if p in PLATFORM_BRIEFS]
    if not platforms or not (title or transcript).strip():
        return {}

    briefs = "\n".join(f"- {p}: {PLATFORM_BRIEFS[p]}" for p in platforms)
    prompt = PROMPT % {
        "title": (title or "").strip() or "(none)",
        "transcript": (transcript or "").strip()[:MAX_TRANSCRIPT_CHARS]
                      or "(nothing audible)",
        "briefs": briefs,
    }

    if ask is not None:
        # A caller driving this directly supplies the transport, so there
        # is no key to look up and no model name to resolve.
        try:
            return _parse(ask("", model, prompt), platforms)
        except Exception:
            return {}

    configured = all_available(provider)
    if not configured:
        one, key = available(provider)
        configured = [(one, key)] if one else []

    # Every configured provider, same as the clip ranking: one provider
    # is one point of failure, and here the failure is a whole day of
    # posts going out in one voice.
    for name, key in configured:
        try:
            raw = asker_for(name)(key, resolve_model(name, key, model),
                                  prompt)
        except Exception:
            continue
        found = _parse(raw, platforms)
        if found:
            return found
    return {}
