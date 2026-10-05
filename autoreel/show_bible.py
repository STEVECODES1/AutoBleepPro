"""The channel's own context, for every AI step that judges a clip.

A model that has never watched the channel picks against a general idea
of funny. The show bible (auto_uploader/show_bible.md, plain words, meant
to be edited by a person) tells it who Stacks is, the characters he
plays, the running jokes and what his audience actually shares - so a
callback reads as a callback instead of a random line.
"""

import os
from functools import lru_cache

MAX_CHARS = 6000


def bible_path() -> str:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "auto_uploader", "show_bible.md")


@lru_cache(maxsize=1)
def _read(path: str, mtime: float) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()[:MAX_CHARS]
    except OSError:
        return ""


def bible_block(path: str = "") -> str:
    """The bible as a prompt block, or "" when there is no file."""
    path = path or bible_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return ""
    text = _read(path, mtime)
    if not text:
        return ""
    return ("ABOUT THIS CHANNEL (read this before judging anything):\n"
            f"{text}\n")
