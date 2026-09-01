"""Take the word. Give the next line back immediately.

"too many cut audio it should just be that word and thats it quickly pick
up the next phase without leaving the audience boreing"

Two things were making mutes longer than the word.

The first was a default. GeneralConfig.censor_mute_whole_segment was
True, and a config.json written before that key existed inherited it
silently. On, a flagged hate-speech word takes the whole WHISPER SEGMENT
with it - and a segment is a sentence, not a word. One slur silenced
several seconds: the word went, and so did the setup and the punchline
around it. The shipped config.json had said false for a while; the
default is what actually won.

The second was that nothing bounded a single mute at all. A bad word
timestamp, a merged run of flagged words, or that segment expansion could
each produce a span far longer than any word, and there was no backstop.
On a sixty-second clip, a second of dead air is a sixtieth of the whole
thing spent on nothing.

What must NOT change: a mute still has to cover the word completely.
Whisper's word timings are 100-300ms out, and a slur whose first syllable
survives is not censored - which is a strike, not a rough edit.
"""

from __future__ import annotations

import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (_REPO, os.path.join(_REPO, "auto_uploader")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from autoreel.compliance import (MAX_MUTE_MS, ComplianceEngine,  # noqa: E402
                                 Violation)

WORD_START, WORD_END = 8.0, 8.4          # a 400ms word


def _engine(**kwargs):
    kwargs.setdefault("padding_ms", 250)
    kwargs.setdefault("mute_whole_segment", False)
    return ComplianceEngine(**kwargs)


def _span(engine, violation, total_ms=20_000):
    spans = engine.mute_spans([violation], total_ms)
    assert len(spans) == 1
    return spans[0]


def _length(engine, violation, total_ms=20_000):
    start, end = _span(engine, violation, total_ms)
    return end - start


# ── the word, and not much else ──────────────────────────────────────────

def test_a_word_in_open_air_is_not_silenced_for_a_second_and_a_half():
    """Padding into silence is free, but it is still what the audience
    hears as a gap."""
    violation = Violation(word="murder", category="violence",
                          start=WORD_START, end=WORD_END,
                          prev_end=7.2, next_start=8.9)

    assert _length(_engine(), violation) <= 1_000


def test_tight_speech_does_not_eat_the_neighbouring_words():
    violation = Violation(word="murder", category="violence",
                          start=WORD_START, end=WORD_END,
                          prev_end=7.95, next_start=8.45)

    length = _length(_engine(), violation)

    assert length <= 800, f"{length}ms swallowed the words either side"


def test_the_word_itself_is_always_fully_covered():
    """The whole point. A slur whose first syllable survives is a strike,
    not a rough edit."""
    violation = Violation(word="murder", category="violence",
                          start=WORD_START, end=WORD_END,
                          prev_end=7.95, next_start=8.45)

    start, end = _span(_engine(), violation)

    assert start <= WORD_START * 1000
    assert end >= WORD_END * 1000


def test_whispers_timing_error_is_still_covered():
    """Word timings run 100-300ms out; the mute has to start before the
    word does."""
    violation = Violation(word="murder", category="violence",
                          start=WORD_START, end=WORD_END,
                          prev_end=7.2, next_start=8.9)

    start, _end = _span(_engine(), violation)

    assert WORD_START * 1000 - start >= 100


# ── nothing runs longer than a word plausibly can ────────────────────────

def test_no_single_word_mute_outlasts_the_cap():
    """A backstop for a bad timestamp or a merged run - either of which
    can produce a span no word could justify."""
    runaway = Violation(word="slur", category="hate_speech",
                        start=2.0, end=13.5)

    assert _length(_engine(), runaway) <= MAX_MUTE_MS


def test_the_cap_trims_the_tail_not_the_start():
    """The word's start is what the timing is anchored to, so its padding
    is kept; what gets handed back is the tail running into the next
    line."""
    runaway = Violation(word="slur", category="hate_speech",
                        start=2.0, end=13.5)

    start, end = _span(_engine(), runaway)

    assert start <= 2.0 * 1000
    assert end == start + MAX_MUTE_MS


def test_an_explicit_whole_sentence_mute_is_left_alone():
    """The cap is a backstop for runaway timings, not an off switch.
    mute_whole_segment is somebody asking for the sentence on purpose -
    for a platform that acts on context rather than the audible word -
    and capping it would turn the feature off while looking like it
    still worked. It is off by default; asking for it still gets it."""
    violation = Violation(word="slur", category="hate_speech",
                          start=WORD_START, end=WORD_END,
                          segment_start=2.0, segment_end=14.0)

    assert _length(_engine(mute_whole_segment=True), violation) > MAX_MUTE_MS


def test_an_ordinary_swear_never_takes_the_segment():
    """Only high-severity words were ever expanded, and now not even
    those escape the cap."""
    violation = Violation(word="murder", category="violence",
                          start=WORD_START, end=WORD_END,
                          segment_start=2.0, segment_end=14.0,
                          prev_end=7.9, next_start=8.5)

    # 840ms for a 400ms word with a 100ms gap either side: the word, both
    # gaps, and the bleed that covers Whisper's timing error. Against the
    # 12,500ms the segment would have taken.
    assert _length(_engine(mute_whole_segment=True), violation) <= 1_000


# ── the default that silently won ────────────────────────────────────────

def test_a_config_without_the_key_mutes_only_the_word():
    """This is the one that was wrong: config.json files written before
    the key existed inherited True from the dataclass."""
    from utils.config import GeneralConfig
    import dataclasses

    field = {f.name: f for f in dataclasses.fields(GeneralConfig)}[
        "censor_mute_whole_segment"]

    assert field.default is False


def test_the_loader_agrees_with_the_dataclass():
    """Two defaults in two places is how one of them goes stale."""
    source = open(os.path.join(_REPO, "auto_uploader", "utils", "config.py"),
                  encoding="utf-8").read()

    assert ('censor_mute_whole_segment=bool(gen.get('
            '"censor_mute_whole_segment", False))' in source)


def test_the_shipped_config_says_so_too():
    import json

    with open(os.path.join(_REPO, "auto_uploader", "config.json"),
              encoding="utf-8") as handle:
        shipped = json.load(handle)

    assert shipped["general"]["censor_mute_whole_segment"] is False


# ── the clips that need clean audio get it ───────────────────────────────

def test_shorts_and_tiktok_bleep_slurs_not_every_swear():
    """Both were "all". YouTube does demonetise over spoken language and
    TikTok's For You standards do discourage it - but neither BANS it,
    and muting every swear on a channel made of swearing put a hole in
    the audio every few seconds. The clips came back unwatchable, which
    costs more reach than the language does. Slurs are the line that
    actually takes a channel away, and that line still holds."""
    from utils.clip_queue import CENSOR_AUDIO_DEFAULTS

    assert CENSOR_AUDIO_DEFAULTS["youtube_shorts"] == "slurs"
    assert CENSOR_AUDIO_DEFAULTS["zernio_tiktok"] == "slurs"


def test_rumble_is_still_untouched():
    """It is the uncensored channel; that is the whole point of the
    split."""
    from utils.clip_queue import CENSOR_AUDIO_DEFAULTS

    assert "rumble" not in CENSOR_AUDIO_DEFAULTS


def test_silence_is_still_the_method():
    """Asked for explicitly and repeatedly: mute it, do not beep it."""
    import json

    with open(os.path.join(_REPO, "auto_uploader", "config.json"),
              encoding="utf-8") as handle:
        shipped = json.load(handle)

    assert shipped["general"]["censor_bleep_method"] == "silence"
