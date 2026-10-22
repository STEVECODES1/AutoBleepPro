"""The two lines every public surface carries.

Why this is a module and not a string in config.json: the owner asked
(10-22) that every channel say plainly that it is fan-run and that the
people on screen are actors. A line that lives in one template gets
forgotten by the next code path that builds a description - recaps,
weeklies, Shorts, social captions and Rumble all build their own text -
so it lives here and every builder calls it.

Two lengths:

* ``ACTORS_LINE`` for descriptions and the channel About, where there is
  room for a sentence.
* ``SHORT_ACTORS_LINE`` for captions, where X cuts at 280 characters.

Both are idempotent: adding one twice changes nothing.
"""

from __future__ import annotations

FAN_LINE = ("Fan channel - not Stackswopo's own channel. "
            "His channel: https://www.youtube.com/@stackswopo_")

ACTORS_LINE = ("⚠️ DISCLAIMER: All people in these videos are actors. "
               "GTA content is GTA roleplay; anything else still features actors. "
               "Nothing shown is real.")

SHORT_ACTORS_LINE = "⚠️ Everyone in this video is an actor. GTA RP - not real."

ABOUT_NOTICE = FAN_LINE + "\n\n" + ACTORS_LINE

_ACTORS_MARKERS = ("all people in these videos are actors",
                   "everyone in this video is an actor")


def has_disclaimer(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _ACTORS_MARKERS)


def _insert_before_tags(text: str, block: str) -> str:
    """Put ``block`` before a trailing hashtag run, or at the end."""
    lines = text.rstrip().splitlines()
    start = len(lines)
    while start > 0:
        line = lines[start - 1].strip()
        if not line or line.startswith("#"):
            start -= 1
            continue
        break
    if start < len(lines) and any(l.strip().startswith("#") for l in lines[start:]):
        head = "\n".join(lines[:start]).rstrip()
        tail = "\n".join(lines[start:]).strip("\n")
        return f"{head}\n\n{block}\n\n{tail}".strip()
    return f"{text.rstrip()}\n\n{block}".strip()


def for_description(text: str, short: bool = False) -> str:
    """A description with the disclaimer before the hashtags.

    Idempotent, and safe on empty text (returns just the disclaimer).
    """
    line = SHORT_ACTORS_LINE if short else ACTORS_LINE
    if has_disclaimer(text):
        return text
    return _insert_before_tags(text, line)


def for_caption(text: str) -> str:
    """A social caption with the short line after its first paragraph.

    Placed before the hashtags rather than at the end so X's 280-character
    cut cannot take the disclosure out while keeping the tags.
    """
    if has_disclaimer(text):
        return text
    parts = (text or "").split("\n\n")
    if len(parts) == 1:
        return f"{text.rstrip()}\n\n{SHORT_ACTORS_LINE}".strip()
    parts.insert(1, SHORT_ACTORS_LINE)
    return "\n\n".join(parts)


def for_about(existing: str) -> str:
    """A channel About with the fan + actors notice at the top.

    Replaces an older fan-only notice (the 10-19 wording) instead of
    stacking a second one, and leaves the rest of the About alone.
    """
    text = (existing or "").replace("\r\n", "\n").strip()
    if text.startswith(ABOUT_NOTICE):
        return text
    old_legacy = ("Fan-run channel - not Stackswopo's own channel. Clips and VODs are posted with "
                  "his permission. His official channel: https://www.youtube.com/@stackswopo_")
    for old in (FAN_LINE, old_legacy, ACTORS_LINE):
        text = text.replace(old, "").strip()
    return (ABOUT_NOTICE + ("\n\n" + text if text else ""))[:1000]
