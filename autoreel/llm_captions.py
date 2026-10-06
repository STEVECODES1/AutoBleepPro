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
    "tiktok": (
        "TikTok: one short line under 100 characters that makes people "
        "want to see how it ends. TikTok search reads captions, so name "
        "what actually happens in plain words (arrested, court, robbery, "
        "GTA RP) rather than only reacting. An emoji is fine. Hashtags "
        "are appended afterwards - do not write any yourself."),
}

PROMPT = """\
You write captions for clips from a live streamer's channel.

The clip's one-line summary, and what is said in it, are below. Write ONE
caption per platform named. Each must be about what HAPPENS in THIS clip,
worked out from what is said - never generic, never "check this out",
never describing the video as a video.

Do not write about the stream itself, its name, its date or how it
opened: the clip is a moment from the middle of it. Numbers in a stream
name (like "10426") are dates, not counts.

Match the streamer's own voice: casual, blunt, funny. Do not clean it up
into marketing copy. Never put a slur in a caption, even one said in the
clip.

Answer as JSON: {"captions": {"<platform>": "<caption>", ...}}
No other text.

TITLE: %(title)s

WHAT IS SAID: %(transcript)s
%(seen)s
PLATFORMS:
%(briefs)s
"""

# Added when stills from the clip go with the words. The transcript is
# half the clip: a fight, a car flipping, who is standing where - none of
# it is in the words, and a caption written blind to it guesses.
FRAMES_NOTE = """
WHAT IS ON SCREEN: the images attached are stills from this clip, in
order. Use them with the words to work out what actually happens - where
it is, who is there, what they do. Say only what the stills and the words
support; do not describe the images as images.
"""

# Stills sent with the words. Four across the clip shows the setup, the
# turn and the payoff; more costs tokens and rarely changes the caption.
CAPTION_FRAMES = 4

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


def _ask_with_frames(name: str, key: str, model: str, prompt: str,
                     frames) -> str:
    """The prompt plus stills, to a provider that can see. "" if it
    cannot, so the caller asks again with the words alone."""
    from .llm_highlights import (ANTHROPIC, _claude, resolve_model,
                                 to_claude_content)
    from .vision_frames import as_inline_data

    if name != ANTHROPIC or not frames:
        return ""
    parts = [{"text": prompt}] + [as_inline_data(f) for f in frames]
    reply, _why = _claude(key, resolve_model(name, key, model), "",
                          to_claude_content(parts), max_tokens=4000,
                          effort="low")
    return reply or ""


def write_captions(title: str, transcript: str, platforms,
                   provider: str = "", model: str = "",
                   ask=None, frames=None) -> dict:
    """{platform: caption} from a model, or {} if none could be had.

    frames: JPEG stills from the clip. Sent to a provider that can see
    them, so the caption is about what happens on screen as well as what
    is said; any other provider gets the words alone, as before."""
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
        "seen": FRAMES_NOTE if frames else "",
    }
    plain_prompt = prompt if not frames else PROMPT % {
        "title": (title or "").strip() or "(none)",
        "transcript": (transcript or "").strip()[:MAX_TRANSCRIPT_CHARS]
                      or "(nothing audible)",
        "briefs": briefs,
        "seen": "",
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
        if frames:
            try:
                found = _parse(_ask_with_frames(name, key, model, prompt,
                                                frames), platforms)
            except Exception:
                found = {}
            if found:
                return found
        try:
            raw = asker_for(name)(key, resolve_model(name, key, model),
                                  plain_prompt)
        except Exception:
            continue
        found = _parse(raw, platforms)
        if found:
            return found
    return {}
