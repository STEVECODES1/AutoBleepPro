"""Telling Whisper what it is about to hear.

Taken from FunClip, which exposes SeACo-Paraformer's hotword feature for
exactly this. faster-whisper has the same lever and this project was not
using it.

It matters more here than for a general transcriber, because a missed
word is not a typo:

  * The censor cannot mute a word the transcript does not contain. Fast,
    shouted, overlapping gameplay speech is where Whisper drops or softens
    a slur, and a slur it never wrote ships to Instagram and YouTube.
  * "Stackswopo", "BinScripts" and "Stizz" are not English words. Whisper
    renders them differently every time, and those strings end up burned
    into captions and used as titles.

The thing that must NOT break: VERBATIM_PROMPT. It is what stops Whisper
tidying swearing at all, and the whole censor pass is built on it. From
faster-whisper's get_prompt, hotwords and the previous-text prompt share
the sot_prev region and are truncated separately - so they coexist, and
this file pins that they are both still sent.
"""

from __future__ import annotations

import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from autoreel import hotwords as hw  # noqa: E402
from autoreel import transcription  # noqa: E402
from autoreel.transcription import BACKEND_FASTER, Transcriber  # noqa: E402


class FakeInfo:
    language = "en"


class FakeWord:
    word, start, end, probability = " hello", 0.0, 0.4, 0.9


class FakeSegment:
    id, start, end, text = 0, 0.0, 1.0, " hello"

    def __init__(self):
        self.words = [FakeWord()]


class FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio_path, **kwargs):
        self.calls.append(kwargs)
        return iter([FakeSegment()]), FakeInfo()


def _speaker(hotword_string=None):
    speaker = Transcriber(model_name="base", device="cpu",
                          backend=BACKEND_FASTER, hotwords=hotword_string)
    speaker._model = FakeModel()
    speaker._resolved_device = "cpu"
    return speaker


# ── what goes in the list ────────────────────────────────────────────────

def test_the_channels_own_names_are_in_it():
    """These are the ones a wrong guess makes visible - in a caption, or
    in a title."""
    built = hw.build().lower()

    for name in ("stackswopo", "binscripts", "stizz"):
        assert name in built


def test_the_flagged_vocabulary_is_in_it():
    """The censor can only mute what the transcript contains."""
    built = hw.build().lower()
    flagged = hw.flagged_vocabulary()

    assert flagged
    assert flagged[0].lower() in built


def test_multi_word_phrases_are_left_out():
    """"kill you" is matched on the transcript afterwards. Feeding the
    phrase as a hotword biases the decode toward producing that phrase
    rather than toward hearing its parts."""
    for word in hw.flagged_vocabulary():
        assert " " not in word


def test_the_list_is_capped():
    """The prompt region is finite and shared with VERBATIM_PROMPT. A
    hundred rare words would crowd out the instruction that makes the
    censor work at all."""
    assert len(hw.build().split()) <= hw.MAX_HOTWORDS


def test_names_survive_the_cap_ahead_of_profanity():
    """If anything is dropped it should be the tail of the word list, not
    the channel's own name."""
    built = hw.build(limit=4).split()

    assert [w.lower() for w in built] == [n.lower() for n in hw.DEFAULT_NAMES]


def test_nothing_is_repeated():
    built = hw.build().split()

    assert len(built) == len({w.lower() for w in built})


def test_profanity_can_be_left_out():
    assert hw.build(include_profanity=False).split() == list(hw.DEFAULT_NAMES)


# ── config overrides, without needing config.json to change ──────────────

def test_the_defaults_are_in_code_not_only_config():
    """config.json is gitignored, so a default that lives only in the
    shipped template reaches a fresh checkout and no machine that already
    runs this."""
    assert hw.names_from(None) == list(hw.DEFAULT_NAMES)
    assert hw.names_from({}) == list(hw.DEFAULT_NAMES)


def test_a_config_can_name_its_own_words():
    names = hw.names_from({"clips": {"hotwords": ["Monkey", "Gumball"]}})

    assert names == ["Monkey", "Gumball"]


def test_a_comma_separated_string_works_too():
    assert hw.names_from({"clips": {"hotwords": "Monkey, Gumball"}}) == [
        "Monkey", "Gumball"]


def test_an_empty_list_turns_names_off_rather_than_restoring_defaults():
    assert hw.names_from({"clips": {"hotwords": []}}) == []


def test_junk_entries_are_dropped_not_crashed_on():
    names = hw.names_from({"clips": {"hotwords": ["", "  ", "ok", "x" * 100]}})

    assert names == ["ok"]


# ── how it reaches the model ─────────────────────────────────────────────

def test_hotwords_are_passed_to_faster_whisper():
    speaker = _speaker("Stackswopo BinScripts")

    speaker.transcribe("a.wav")

    assert speaker._model.calls[0]["hotwords"] == "Stackswopo BinScripts"


def test_the_verbatim_prompt_is_still_sent_alongside_them():
    """This is the one that must not break. Without it Whisper tidies the
    swearing and there is nothing left to mute."""
    speaker = _speaker("Stackswopo")

    speaker.transcribe("a.wav")

    assert speaker._model.calls[0]["initial_prompt"] == transcription.VERBATIM_PROMPT


def test_no_hotwords_means_the_argument_is_not_sent_at_all():
    """faster-whisper opens a sot_prev block for a non-empty string; an
    empty one would cost prompt space for nothing."""
    speaker = _speaker(None)

    speaker.transcribe("a.wav")

    assert "hotwords" not in speaker._model.calls[0]


def test_the_word_timings_still_come_back():
    """They are what the mute lands on."""
    speaker = _speaker("Stackswopo")

    result = speaker.transcribe("a.wav")

    assert result["segments"][0]["words"][0]["start"] == 0.0


def test_a_transcriber_defaults_to_no_hotwords():
    """Nothing changes for a caller that does not ask."""
    assert Transcriber(model_name="base").hotwords is None
