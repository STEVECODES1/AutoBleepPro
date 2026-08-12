"""
Captions must not print what the audio pass just muted.

A clip went out on Rumble with a racial slur rendered in yellow,
forty-eight point, in the middle of the frame - while the same word was
silenced in the audio. Censoring one channel and broadcasting the other
is worse than doing neither, because the mute makes it look handled.
"""

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from autoreel.captions import (STYLE_WORD, build_ass, caption_file_for_clip,
                               censor_words, group_words, mask_word)


def _words(*spoken):
    return [{"word": text, "start": i * 0.5, "end": i * 0.5 + 0.4}
            for i, text in enumerate(spoken)]


def test_a_slur_never_reaches_the_screen():
    """The exact failure: burned in, in yellow, and auto-posted."""
    out = [w["word"] for w in censor_words(_words("what", "nigga?", "bro"))]

    assert out[1] != "nigga?"
    assert "nigga" not in " ".join(out).lower()


def test_profanity_is_masked_too():
    out = [w["word"] for w in censor_words(_words("holy", "shit", "dude"))]

    assert out == ["holy", "s***", "dude"]


def test_ordinary_words_are_left_exactly_alone():
    """A caption track full of asterisks is its own kind of broken."""
    spoken = ("that", "was", "actually", "insane", "bro")

    assert [w["word"] for w in censor_words(_words(*spoken))] == list(spoken)


def test_the_word_is_masked_not_dropped():
    """A missing word makes the line read wrong, and the timing of every
    word after it in the phrase comes from the word list."""
    original = _words("i", "cannot", "fucking", "believe", "it")
    censored = censor_words(original)

    assert len(censored) == len(original)
    assert censored[2]["start"] == original[2]["start"]
    assert censored[2]["end"] == original[2]["end"]


def test_masking_keeps_the_first_letter_and_the_punctuation():
    """"sh*t" is the convention short-form uses - it is what this
    channel's own Rumble titles already do."""
    assert mask_word("shit") == "s***"
    assert mask_word("fuck!") == "f***!"
    assert mask_word('"damn"') == '"d***"'
    assert mask_word("...") == "..."


def test_censoring_happens_inside_the_caption_writer(tmp_path):
    """Not at the call site. A renderer that can be asked for uncensored
    captions will eventually be asked for them."""
    path = str(tmp_path / "clip.ass")
    segments = [{"start": 0.0, "end": 3.0,
                 "words": _words("yo", "shit", "happened")}]

    written = caption_file_for_clip(path, segments, 0.0, 3.0,
                                    style=STYLE_WORD, uppercase=True)

    assert written
    with open(written, encoding="utf-8") as handle:
        body = handle.read()
    assert "SHIT" not in body and "shit" not in body
    assert "S***" in body.upper()


def test_a_broken_compliance_import_is_not_swallowed(monkeypatch):
    """Failing open here means captions render uncensored, which is the
    bug. It has to blow up rather than quietly print the word."""
    import autoreel.captions as captions

    class Boom:
        def __init__(self):
            raise RuntimeError("no model")

    monkeypatch.setattr("autoreel.compliance.ComplianceEngine", Boom)

    try:
        censor_words(_words("shit"))
    except RuntimeError:
        return
    raise AssertionError("a failed checker was swallowed and the word "
                         "would have been printed")
