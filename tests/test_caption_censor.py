"""
Captions must not print what the audio pass just muted.

A clip went out on Rumble with a racial slur rendered in yellow,
forty-eight point, in the middle of the frame - while the same word was
silenced in the audio. Censoring one channel and broadcasting the other
is worse than doing neither, because the mute makes it look handled.
"""

import os
import sys

import pytest

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


def test_the_longest_allowed_line_fits_in_the_frame():
    """28 characters at 84px is roughly 1400px of text in a 920px space.
    Every clip went out with the first and last words sliced off - the
    thumbnails read "VOULD HAVE BE" and "MEET HIM IN REA"."""
    from autoreel.captions import (DEFAULT_FONT_SIZE, DEFAULT_MAX_CHARS,
                                   fits_in_frame)

    assert fits_in_frame(DEFAULT_MAX_CHARS, DEFAULT_FONT_SIZE), (
        f"{DEFAULT_MAX_CHARS} chars at {DEFAULT_FONT_SIZE}px runs off the "
        f"edge - the two numbers are a pair")
    assert not fits_in_frame(28, 84), "the old pair has to stay a failure"


def test_a_long_line_is_broken_into_phrases():
    from autoreel.captions import group_words

    words = [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.25}
             for i, w in enumerate(
                 "absolutely everybody watching this right now".split())]

    for phrase in group_words(words):
        assert len(phrase.text) <= 24, f"too wide: {phrase.text!r}"


# ═════════════════════════════════════════════════════════════════════════════
# INSTAGRAM REMOVED A POST UNDER HATEFUL CONDUCT
#
# Not for profanity - the same account's swearing broke nothing. A slur was
# burned across the frame as "N****", and a masked slur is still legible as
# one. Starring it is the appearance of moderation rather than the fact of
# it, and the classifier reading the frame is not fooled by asterisks.
#
# Removals of that kind escalate to a disabled account, which is a different
# order of loss from a deleted post.
# ═════════════════════════════════════════════════════════════════════════════

def test_a_slur_never_reaches_the_screen_even_starred():
    from autoreel.captions import censor_words

    words = [{"word": w, "start": i, "end": i + 0.3}
             for i, w in enumerate(["nigga", "you", "got", "call"])]

    shown = " ".join(x["word"] for x in censor_words(words))

    assert "n" not in shown.split()[0].lower(), \
        "the slur is still legible on screen"
    assert "*" not in shown.split()[0], "a starred stump reads as the word"
    assert "you got call" in shown, "it removed the rest of the sentence"


def test_ordinary_swearing_is_still_only_masked():
    """Blanking it would leave holes through every sentence the channel
    is there for, and it breaks no rule anywhere this posts."""
    from autoreel.captions import censor_words

    words = [{"word": w, "start": i, "end": i + 0.3}
             for i, w in enumerate(["this", "shit", "crazy"])]

    shown = " ".join(x["word"] for x in censor_words(words))

    assert "s***" in shown
    assert "—" not in shown


def test_a_dropped_word_keeps_its_slot():
    """An empty word would collapse the phrase's spacing and the timing
    that lights it up."""
    from autoreel.captions import censor_words

    words = [{"word": "retard", "start": 1.0, "end": 1.4}]

    out = censor_words(words)

    assert out[0]["word"].strip(), "the word became empty"
    assert out[0]["start"] == 1.0 and out[0]["end"] == 1.4


# ── the audio, per platform ──────────────────────────────────────────

def test_instagram_bleeps_slurs_and_keeps_the_swearing():
    """Two different policies needing two different answers. Bleeping
    every swear for Instagram would flatten the voice the channel is
    there for; leaving a slur in is how an account is taken away."""
    import sys

    sys.path.insert(0, os.path.join(_REPO, "auto_uploader"))
    from autoreel.compliance import ComplianceEngine
    from utils.clip_queue import CENSOR_AUDIO_DEFAULTS, _CENSOR_SCOPES

    assert CENSOR_AUDIO_DEFAULTS["instagram"] == "slurs"
    engine = ComplianceEngine(
        only_categories=_CENSOR_SCOPES[CENSOR_AUDIO_DEFAULTS["instagram"]])

    assert engine._flag_reason("nigga") == "hate_speech"
    assert engine._flag_reason("faggot") == "hate_speech"
    assert engine._flag_reason("shit") is None
    assert engine._flag_reason("fuck") is None


def test_youtube_still_bleeps_everything():
    """YouTube demonetises over ordinary language too, and a channel is
    harder to get back than a post."""
    import sys

    sys.path.insert(0, os.path.join(_REPO, "auto_uploader"))
    from autoreel.compliance import ComplianceEngine
    from utils.clip_queue import CENSOR_AUDIO_DEFAULTS, _CENSOR_SCOPES

    assert CENSOR_AUDIO_DEFAULTS["youtube_shorts"] == "all"
    engine = ComplianceEngine(
        only_categories=_CENSOR_SCOPES[CENSOR_AUDIO_DEFAULTS["youtube_shorts"]])

    assert engine._flag_reason("shit") == "profanity"
    assert engine._flag_reason("nigga") == "hate_speech"


def test_rumble_is_left_uncensored():
    """The whole point of the split - its audience is there for exactly
    what the other platforms will not take."""
    import sys

    sys.path.insert(0, os.path.join(_REPO, "auto_uploader"))
    from utils.clip_queue import CENSOR_AUDIO_DEFAULTS

    assert "rumble" not in CENSOR_AUDIO_DEFAULTS


def test_a_config_saying_true_still_means_everything():
    """True is the old spelling of "all" and is what any config.json
    already in the wild says."""
    import sys

    sys.path.insert(0, os.path.join(_REPO, "auto_uploader"))
    from utils import clip_queue

    import tempfile
    folder = tempfile.mkdtemp()
    clip = os.path.join(folder, "a.mp4")
    open(clip, "wb").write(b"x")

    seen = {}

    def fake_censor(*_a, **kwargs):
        seen["scope"] = kwargs.get("only_categories")

        class R:
            output_path = clip
            violation_count = 0
        return R()

    import utils.censor
    original = utils.censor.censor_video
    utils.censor.censor_video = fake_censor
    try:
        clip_queue._censored_clip(
            "instagram", clip,
            {"instagram": {"censor_uploads": True}, "general": {}})
    finally:
        utils.censor.censor_video = original

    assert seen["scope"] == (), "True must still mean every category"


# ── the classic substring bug ────────────────────────────────────────
#
# "meth" is inside "so-meth-ing". The category lists were matched with a
# plain `phrase in word` test, so the word "something" was flagged as a
# drug reference: every caption rendered it "s********" and the audio pass
# bleeped it - on a channel where somebody says "something" every few
# sentences. It surfaced from a prompt-building test, not from anyone
# watching a clip.

@pytest.mark.parametrize("word", [
    "something", "somethings", "methodical", "assassin", "class", "bass",
    "therapist", "grass", "analysis", "basement", "passing",
])
def test_an_ordinary_word_is_not_flagged(word):
    from autoreel.compliance import ComplianceEngine

    assert ComplianceEngine()._flag_reason(word) is None, \
        f"{word!r} would be bleeped and starred out"


@pytest.mark.parametrize("word,category", [
    ("meth", "drugs"),
    ("cocaine", "drugs"),
    ("nigga", "hate_speech"),
    ("niggas", "hate_speech"),
    ("retarded", "hate_speech"),
])
def test_the_real_words_are_still_caught(word, category):
    from autoreel.compliance import ComplianceEngine

    assert ComplianceEngine()._flag_reason(word) == category


@pytest.mark.parametrize("word", ["fucking", "fucker", "shitting", "fucks"])
def test_inflections_still_match(word):
    """A word boundary ALONE would have broken this - which is why the
    substring test was there in the first place."""
    from autoreel.compliance import ComplianceEngine

    assert ComplianceEngine()._flag_reason(word) is not None


def test_a_custom_word_is_matched_the_same_way():
    from autoreel.compliance import ComplianceEngine

    engine = ComplianceEngine(custom_words=("bing",))

    assert engine._flag_reason("bing") == "custom_word"
    assert engine._flag_reason("bingo") is None, "custom words match whole words too"


# ── how a word is removed, and how much goes with it ─────────────────
#
# "I don't like bleep, I never wanted muted - if there's a bad word just
# simply mute it and say the next phrase quick."
#
# Two settings, easy to confuse, both wrong for this channel. A beep is
# the loudest thing in a clip that is mostly talking, and muting the whole
# sentence around a slur made the clip lurch to the next line.

def test_the_style_can_be_changed_without_editing_json(tmp_path):
    import importlib.util
    import json
    import sys

    sys.path.insert(0, os.path.join(_REPO, "auto_uploader"))
    spec = importlib.util.spec_from_file_location(
        "_main_censor", os.path.join(_REPO, "auto_uploader", "main.py"))
    main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main)

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"general": {
        "censor_bleep_method": "beep",
        "censor_mute_whole_segment": True}}))

    said = main.set_censor_style(str(path), sound="silence", scope="word")

    assert said and "no tone" in said and "sentence carries on" in said
    general = json.loads(path.read_text())["general"]
    assert general["censor_bleep_method"] == "silence"
    assert general["censor_mute_whole_segment"] is False

    # Idempotent, and honest about a value it does not know.
    assert main.set_censor_style(str(path), sound="silence",
                                 scope="word") is None
    assert "not one of" in main.set_censor_style(str(path), sound="honk")


def test_one_setting_can_change_without_the_other(tmp_path):
    import importlib.util
    import json
    import sys

    sys.path.insert(0, os.path.join(_REPO, "auto_uploader"))
    spec = importlib.util.spec_from_file_location(
        "_main_censor2", os.path.join(_REPO, "auto_uploader", "main.py"))
    main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main)

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"general": {
        "censor_bleep_method": "beep",
        "censor_mute_whole_segment": True}}))

    main.set_censor_style(str(path), sound="silence")

    general = json.loads(path.read_text())["general"]
    assert general["censor_bleep_method"] == "silence"
    assert general["censor_mute_whole_segment"] is True, \
        "changing the sound also changed the scope"


def test_word_scope_leaves_the_sentence_intact():
    """The engine's own switch. mute_whole_segment is what takes the line
    with the word."""
    from autoreel.compliance import ComplianceEngine

    assert ComplianceEngine().mute_whole_segment is False, \
        "the engine default should be the word, not the line"
    assert ComplianceEngine(mute_whole_segment=True).mute_whole_segment
