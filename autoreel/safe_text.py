"""
Titles and captions that will not get a channel struck.

WHY
---
A clip's title is the line actually spoken in it, which is the whole
reason the titles read like a person wrote them. On Rumble that is the
point: the channel is the uncensored one and the audience came for it.

YouTube and Instagram are not that. Both apply their advertiser-friendly
and community rules to the TEXT as well as the video, and neither cares
that the words came from the audio. Real titles this pipeline produced
and posted:

    Fuck up from youtube i'm just steve williams
    Bro, what type of nerd ass faggot shit you got?
    Niggas be with trannies bro

Uploading those to a YouTube channel as titles is how a channel gets
limited or removed - and the Shorts channel took a year to reach 749
subscribers.

WHAT THIS DOES
--------------
Masks flagged words the same way the burned-in captions already do:
first letter kept, the rest starred, punctuation preserved - `f***`.
The sentence still reads, the slur does not.

Slurs are removed rather than masked. A masked slur is still legible as
one, and no platform's reviewer treats `n****` as a different word.

WHAT IT DOES NOT DO
-------------------
Touch Rumble. That channel is the uncensored one on purpose, and running
this over it would flatten the exact thing the audience is there for.
The caller chooses; nothing here is applied globally.

It is also not a guarantee. It is a word filter, and a word filter
cannot judge context. It lowers the odds of a strike; it does not remove
the need for a human to look at what goes out.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

# Words that are not masked but REMOVED. A masked slur is still legible
# as one - nobody reads `n****` as anything else - so leaving a starred
# stump is the appearance of moderation rather than the fact of it.
DROP_ENTIRELY = (
    "nigga", "niggas", "nigger", "niggers", "faggot", "faggots", "fag",
    "tranny", "trannies", "retard", "retarded", "kike", "spic", "chink",
)

# Left alone. The compliance list is tuned for AUDIO, where a bleep on a
# borderline word costs nothing. A title is read, and starring an
# ordinary word makes the channel look like it is hiding something it is
# not.
KEEP_PLAIN = ("hell", "damn", "crap", "god", "sucks", "kill", "dead",
              "stupid", "idiot")

_WORD = re.compile(r"[A-Za-z']+")


def _flagged(word: str, checker=None) -> bool:
    """Is this word one the compliance pass would bleep?"""
    if word.lower() in KEEP_PLAIN:
        return False
    if checker is None:
        return False
    try:
        # _flag_reason returns why a word is flagged, or None. Using the
        # project's OWN list matters: the audio bleep and the title must
        # not disagree about what counts, or a clip goes out bleeped and
        # titled with the same word spelled out.
        return checker._flag_reason(word) is not None
    except Exception:
        return False


def _checker():
    """The project's own profanity list, or None if it cannot load.

    None means "mask nothing", which is the honest failure: this module
    cannot invent a word list, and pretending it cleaned a title it did
    not read would be worse than saying it did nothing.
    """
    try:
        from .compliance import ComplianceEngine

        return ComplianceEngine()
    except Exception:
        return None


def mask(word: str) -> str:
    """f*** - first letter kept, punctuation kept, the rest starred."""
    from .captions import mask_word

    return mask_word(word)


def clean(text: str, checker=None, drop: Iterable[str] = DROP_ENTIRELY,
          counts: Optional[dict] = None) -> str:
    """A title safe to put on YouTube or Instagram.

    Slurs are dropped, other flagged words are masked, everything else is
    left exactly as spoken. `counts`, when given, is filled with how many
    words were masked and how many were dropped - the caller needs the
    difference, because one preserves a sentence and the other does not.
    """
    if not text:
        return ""
    if checker is None:
        checker = _checker()
    dropping = {d.lower() for d in (drop or ())}

    masked = dropped = 0
    out, last = [], 0
    for found in _WORD.finditer(text):
        word = found.group(0)
        out.append(text[last:found.start()])
        lowered = word.lower().strip("'")
        if lowered in dropping:
            out.append("")          # removed, not starred
            dropped += 1
        elif _flagged(word, checker):
            out.append(mask(word))
            masked += 1
        else:
            out.append(word)
        last = found.end()
    if counts is not None:
        counts["masked"], counts["dropped"] = masked, dropped
    out.append(text[last:])

    # Removing a word leaves the spaces and punctuation that surrounded
    # it, and "Bro, what type of nerd ass  shit you got?" reads as a
    # typo rather than as an edit.
    tidied = re.sub(r"\s{2,}", " ", "".join(out))
    tidied = re.sub(r"\s+([,.!?;:])", r"\1", tidied)
    tidied = re.sub(r"^[\s,.;:!?-]+", "", tidied)
    return tidied.strip()


def is_clean(text: str, checker=None) -> bool:
    """True when `clean` would change nothing."""
    return clean(text, checker) == (text or "").strip()


def clean_lines(text: str, checker=None) -> str:
    """The same, over a multi-line caption, keeping the line breaks."""
    if not text:
        return ""
    if checker is None:
        checker = _checker()
    return "\n".join(clean(line, checker) if line.strip() else line
                     for line in text.splitlines())


# A title that lost this much of itself to the filter is no longer the
# line that was spoken - it is wreckage of it.
MAX_REMOVED_FRACTION = 0.34

# Below this a "title" is a fragment, whatever the arithmetic says.
MIN_TITLE_WORDS = 3


def clean_title(text: str, fallback: str = "", checker=None) -> str:
    """A safe title, or `fallback` when cleaning would wreck it.

    A MASKED word keeps its sentence: "F*** up from youtube i\'m just
    steve williams" still reads. A DROPPED word does not - "Niggas be
    with trannies bro" becomes "be with bro", which is not a title
    anybody would write, and the arithmetic does not catch it because
    only two words of eight went.

    So any drop at all disqualifies the line. A slur cannot be masked
    (starring it fools nobody) and cannot be removed without wrecking
    the sentence around it, which means that line simply is not usable
    as a title on a platform that would object to it. Masking is a fix;
    dropping is a demolition.
    """
    counts: dict = {}
    cleaned = clean(text, checker, counts=counts)
    if not cleaned:
        return fallback
    if counts.get("dropped"):
        return fallback or cleaned
    before = len(_WORD.findall(text or ""))
    after = len(_WORD.findall(cleaned))
    if before and (before - after) / before > MAX_REMOVED_FRACTION:
        return fallback or cleaned
    if after < MIN_TITLE_WORDS < before:
        return fallback or cleaned
    return cleaned
