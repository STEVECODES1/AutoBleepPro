"""Words Whisper is told to expect, so it stops guessing at them.

Taken from FunClip (modelscope), which exposes SeACo-Paraformer's hotword
feature - "specify certain entity words, names, etc. as hotwords during
the ASR process to enhance recognition results". faster-whisper has the
same lever and this project was not using it.

It matters here more than it does for a general transcriber, because a
missed word is not a typo:

  * THE CENSOR CANNOT MUTE A WORD THE TRANSCRIPT DOES NOT CONTAIN. Fast,
    shouted, overlapping gameplay speech is exactly where Whisper drops
    or softens a slur, and a slur it never wrote is a slur that ships to
    Instagram and YouTube. Biasing the decode toward the flagged
    vocabulary is the cheapest accuracy this pipeline can buy.
  * Names come out wrong. "Stackswopo", "BinScripts" and "Stizz" are not
    English words; Whisper renders them a different way every time, and
    those strings end up in burned-in captions and in titles.

How it lands, from faster-whisper's get_prompt: hotwords are encoded into
the same sot_prev region as the previous-text prompt, each truncated
separately at max_length // 2. So this does NOT displace VERBATIM_PROMPT,
which is what stops Whisper tidying swearing in the first place - the two
work together. It does share a budget with it, which is why the list is
kept short and capped rather than being every word we know.
"""

from __future__ import annotations

from typing import Iterable, Optional

# Kept small on purpose. The prompt region is finite and shared with the
# verbatim prompt; a hundred rare words would crowd out the instruction
# that makes the whole censor pass work.
MAX_HOTWORDS = 48

# The channel's own vocabulary. In code rather than config.json, because
# that file is gitignored - a default that only exists in the shipped
# template reaches a fresh checkout and no machine that already runs this.
DEFAULT_NAMES = (
    "Stackswopo", "BinScripts", "Stizz", "Wopo",
)


def _clean(words: Iterable[str]) -> list:
    """Trimmed, de-duplicated, order preserved, case-insensitively unique."""
    seen, out = set(), []
    for raw in words or ():
        word = str(raw or "").strip()
        if not word or len(word) > 40:
            continue
        key = word.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(word)
    return out


def flagged_vocabulary(engine=None, limit: int = 32) -> list:
    """The words the censor is looking for.

    Single words only. A multi-word phrase like "kill you" is matched on
    the transcript afterwards, and feeding a whole phrase as a hotword
    biases the decode toward producing that phrase rather than toward
    hearing its parts.
    """
    if engine is None:
        from autoreel.compliance import ComplianceEngine

        engine = ComplianceEngine()
    words = []
    for phrases in (getattr(engine, "categories", None) or {}).values():
        for phrase in phrases or ():
            text = str(phrase or "").strip()
            if text and " " not in text:
                words.append(text)
    words += list(getattr(engine, "custom_words", ()) or ())
    return _clean(words)[:limit]


def names_from(config: Optional[dict] = None) -> list:
    """Channel names, from config if it says any, else the defaults."""
    clips = ((config or {}).get("clips", {}) or {})
    configured = clips.get("hotwords")
    if configured is None:
        return list(DEFAULT_NAMES)
    if isinstance(configured, str):
        configured = [part for part in configured.split(",")]
    return _clean(configured)


def build(config: Optional[dict] = None, engine=None,
          include_profanity: bool = True, limit: int = MAX_HOTWORDS) -> str:
    """The hotword string to hand faster-whisper, or "" for none.

    Names come first: they are the ones a wrong guess makes visible, in a
    caption or a title, and if anything has to be dropped by the cap it
    should be the tail of the profanity list rather than the channel's
    own name.
    """
    words = names_from(config)
    if include_profanity:
        words += flagged_vocabulary(engine)
    words = _clean(words)[:max(0, int(limit))]
    return " ".join(words)
