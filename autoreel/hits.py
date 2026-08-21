"""What actually worked on this channel, handed to the model as examples.

The clip picker was choosing against a general idea of funny. It had
sixty candidates, two frames of each, and no notion of what THIS audience
laughs at - so it picked competently and the clips landed flat, and the
answer kept being "your clips aren't funny".

The channel already knows the answer. Twenty posts with real view counts,
from four million down to twenty-nine thousand, and the pattern in them is
not subtle: the big ones are SHORT and they are a single line somebody
would quote back. "Imma switch yo ahh" did four million at twenty
seconds. The eighty-eight second one did a tenth of that.

So the model is shown them. This is the difference between asking
somebody to be funny and showing them the room.

The file is meant to be edited. When something new lands, put it in -
the examples are only as good as the last time somebody updated them.
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

# Enough to show the pattern, few enough to leave the prompt about the
# actual video. The model needs a sense of the room, not a catalogue.
MAX_EXAMPLES = 10

# Below this a "hit" is just a post. Including everything would teach it
# that anything goes, which is what it already believed.
MIN_VIEWS = 25_000


def _clean(text: str) -> str:
    """A caption without its hashtags - they are not the joke."""
    without = re.sub(r"#\w*", " ", str(text or ""))
    return " ".join(without.split()).strip(" -–—…#")


def parse_found(text: str) -> list:
    """Hits out of a clips_found.txt table.

    That file is written by --find-clips and already holds exactly this:
    views, duration, account, caption. Reading it directly means the
    examples can be refreshed by running a command rather than by hand
    editing JSON.
    """
    hits = []
    for line in str(text or "").splitlines():
        found = re.match(
            r"\s*([\d,]{4,})\s+(\d+)s\s+\S+\s+(.+?)\s*$", line)
        if not found:
            continue
        try:
            views = int(found.group(1).replace(",", ""))
            seconds = int(found.group(2))
        except ValueError:
            continue
        caption = _clean(found.group(3))
        if caption:
            hits.append({"views": views, "seconds": seconds,
                         "caption": caption})
    return hits


def load(path: str) -> list:
    """The channel's hits, best first. [] when there is no usable file."""
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            body = handle.read()
    except OSError:
        return []

    hits = []
    try:
        data = json.loads(body)
        raw = data.get("hits") if isinstance(data, dict) else data
        for entry in raw or ():
            if not isinstance(entry, dict):
                continue
            caption = _clean(entry.get("caption", ""))
            if not caption:
                continue
            hits.append({"views": int(entry.get("views", 0) or 0),
                         "seconds": int(entry.get("seconds", 0) or 0),
                         "caption": caption})
    except (ValueError, TypeError):
        # Not JSON: the clips_found.txt table this can also be fed.
        hits = parse_found(body)

    hits = [h for h in hits if h["views"] >= MIN_VIEWS]
    hits.sort(key=lambda h: h["views"], reverse=True)
    return hits


def typical_seconds(hits: list) -> Optional[int]:
    """How long the winners actually are, or None.

    Worth telling the model on its own: this channel's four-million and
    two-and-a-half-million posts are twenty and twenty-eight seconds.
    Length is the one part of "what works here" that a picker can act on
    directly.
    """
    lengths = sorted(h["seconds"] for h in hits[:MAX_EXAMPLES]
                     if h.get("seconds"))
    if not lengths:
        return None
    return lengths[len(lengths) // 2]


def for_prompt(hits: list, limit: int = MAX_EXAMPLES) -> str:
    """The examples block, or "" when there is nothing to show."""
    shown = [h for h in hits if h.get("caption")][:limit]
    if not shown:
        return ""

    lines = ["",
             "THIS CHANNEL'S BIGGEST POSTS, with how many views they got.",
             "Pick moments that would sit alongside these. This is the "
             "audience you are choosing for - not a general one.",
             ""]
    for hit in shown:
        views = f"{hit['views']:,}"
        length = f"{hit['seconds']}s" if hit.get("seconds") else "?"
        lines.append(f"  {views:>10} views  {length:>4}  {hit['caption']}")

    middle = typical_seconds(shown)
    if middle:
        # Only what the numbers actually say. An invented rule - "the
        # longest ones did worst" - is a false statement in a prompt, and
        # the model has no way to check it. The list above IS the
        # evidence; the line below only points at the part of it that a
        # picker can act on.
        best = shown[0]
        lines += ["",
                  f"The biggest one is {best['seconds']}s. Half of these "
                  f"run under {middle} seconds. A moment that lands in "
                  f"one line beats one that needs a build-up."]
    return "\n".join(lines)
