"""What this channel actually says, remembered between runs.

WHY
---
Hotwords tell Whisper which words to expect, and the censor cannot mute
a word the transcript does not contain - a slur Whisper never wrote is a
slur that ships. So which words get into that list is a safety decision,
not a tuning one.

The list is capped at MAX_HOTWORDS because it shares the prompt region
with the verbatim instruction, and the compliance engine knows hundreds
of flagged words. Until now the 32 that made the cut were simply the
first 32 the category dictionaries happened to yield - an arbitrary
slice, fixed forever, with no relationship to what this streamer says.
A channel that says one slur four hundred times a week and has never
once said another was biasing the decode toward both equally.

This is the memory that fixes that. Every censor pass writes down the
flagged words it actually found; the next pass spends its hotword budget
on those, most-said first. The words the streamer really uses are the
ones Whisper is told to listen for.

It also answers the plain version of the question - "already know what
word that is next time" - because the second run over the same kind of
speech starts out expecting it.

WHAT IS STORED, AND WHAT IS NOT
-------------------------------
A word, how many times it has been heard, its category, and when it was
last heard. That is the whole file.

No chat. No usernames. No viewers. No timestamps within a stream, no
sentence it came from, no clip it belongs to - nothing that could
reconstruct who said what or when. These are words the account owner
themself said into their own microphone, kept so their own censor can
hear them better, and the file lives beside the censor's own cache.

IT CANNOT BREAK A RUN
---------------------
Reading returns an empty vocabulary on any problem and writing swallows
its errors. A vocabulary file that cannot be read costs a slightly less
accurate transcript; it must never cost the censor pass.
"""

from __future__ import annotations

import json
import os
import time
from typing import Iterable, Optional

LEDGER_NAME = "vocabulary.json"

# Words heard fewer times than this stay out of the hotword list. One
# occurrence is as likely to be Whisper mishearing something as it is to
# be a word this channel uses, and biasing the next decode toward a
# mistake is how a mistake becomes permanent.
MIN_SIGHTINGS = 2

# Nothing that has not been heard in this long counts any more. A
# vocabulary is a description of how somebody talks now.
STALE_AFTER_DAYS = 120

# A ceiling so the file cannot grow without limit on a channel that
# swears creatively. Far more than the hotword cap can use, so the
# ordering still has something to choose from.
MAX_REMEMBERED = 400


def ledger_path(work_dir: str = "") -> str:
    """Where the vocabulary lives - beside the censor's own cache."""
    return os.path.join(str(work_dir or "."), LEDGER_NAME)


def _now() -> float:
    return time.time()


def load(path: str) -> dict:
    """{word: {"count": int, "category": str, "seen": float}} or {}."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return {}
    words = raw.get("words") if isinstance(raw, dict) else None
    if not isinstance(words, dict):
        return {}
    out = {}
    for word, entry in words.items():
        if not isinstance(word, str) or not isinstance(entry, dict):
            continue
        try:
            out[word.lower()] = {
                "count": max(0, int(entry.get("count", 0))),
                "category": str(entry.get("category", "") or ""),
                "seen": float(entry.get("seen", 0.0)),
            }
        except (TypeError, ValueError):
            continue
    return out


def save(path: str, words: dict) -> bool:
    """Write it, newest and most-said first. False on any problem."""
    try:
        folder = os.path.dirname(os.path.abspath(path))
        if folder:
            os.makedirs(folder, exist_ok=True)
        ordered = sorted(words.items(),
                         key=lambda item: (-item[1].get("count", 0), item[0]))
        payload = {"version": 1,
                   "words": {word: entry for word, entry in
                             ordered[:MAX_REMEMBERED]}}
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=1)
        os.replace(temporary, path)
        return True
    except OSError:
        return False


def _clean_word(raw: str) -> str:
    """A bare lowercase word, or "" if it is not one.

    Whisper hands back words carrying their punctuation - "nigga," and
    "shit." - and remembering those as separate entries would spend the
    hotword budget three times on the same word.
    """
    word = str(raw or "").strip().lower().strip(".,!?;:\"'()[]…-")
    if not word or len(word) > 40:
        return ""
    # A hotword is a word. Anything with whitespace in it is a phrase
    # and biases the decode toward producing the phrase - see
    # hotwords.flagged_vocabulary.
    return "" if " " in word else word


def remember(path: str, words: Iterable[str],
             categories: Optional[dict] = None) -> int:
    """Record the flagged words a pass just found. Returns how many.

    Counts INSTANCES, not distinct words: a slur said four hundred times
    should outrank one said twice when the budget is spent, and that is
    the entire point of keeping this.
    """
    try:
        known = load(path)
        categories = categories or {}
        added = 0
        for raw in words or ():
            word = _clean_word(raw)
            if not word:
                continue
            entry = known.setdefault(
                word, {"count": 0, "category": "", "seen": 0.0})
            entry["count"] = int(entry.get("count", 0)) + 1
            entry["seen"] = _now()
            if not entry.get("category") and categories.get(word):
                entry["category"] = str(categories[word])
            added += 1
        if not added:
            return 0
        save(path, known)
        return added
    except Exception:
        # A notebook that cannot be written must never cost the run the
        # censoring it just did.
        return 0


def learned(path: str, limit: int = 0,
            min_sightings: int = MIN_SIGHTINGS) -> list:
    """The words this channel actually says, most-said first.

    Stale and one-off entries are left out - see MIN_SIGHTINGS and
    STALE_AFTER_DAYS for why each is a correctness decision rather than
    tidiness.
    """
    cutoff = _now() - STALE_AFTER_DAYS * 86400
    usable = [(entry.get("count", 0), word)
              for word, entry in load(path).items()
              if entry.get("count", 0) >= min_sightings
              and entry.get("seen", 0.0) >= cutoff]
    usable.sort(key=lambda pair: (-pair[0], pair[1]))
    words = [word for _count, word in usable]
    return words[:limit] if limit and limit > 0 else words


def summary(path: str) -> str:
    """One line for a human, for --vocabulary and the console."""
    known = load(path)
    if not known:
        return ("Nothing learned yet - the first censor pass writes this "
                "file, and the run after it is the one that benefits.")
    heard = sum(entry.get("count", 0) for entry in known.values())
    top = learned(path, limit=5)
    return (f"{len(known)} distinct word(s), {heard} sighting(s). "
            f"Most said: {', '.join(top)}." if top else
            f"{len(known)} distinct word(s), {heard} sighting(s).")
