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


# ═════════════════════════════════════════════════════════════════════════════
# THE SAME BUG, IN THE HOOK
#
# The fix above only ever touched the scrolling phrases. The hook - a
# transcript quote or LLM title pinned across the top of the frame for the
# clip's ENTIRE length - went through none of it, because it is not built
# from `words_in_range`/`censor_words` at all: it is a raw string handed
# straight to `build_ass` and only ASS-escaped. A real clip went out with
# "GET DOWN LIKE JAMES BROWN, NIGGA" burned across the top, full size, for
# all 45 seconds - worse exposure than the scrolling case this file was
# named for, and on every platform that clip's file reaches, not just
# Rumble.
# ═════════════════════════════════════════════════════════════════════════════

def test_a_slur_in_the_hook_never_reaches_the_screen(tmp_path):
    path = str(tmp_path / "clip.ass")
    segments = [{"start": 0.0, "end": 3.0, "words": _words("get", "down")}]

    written = caption_file_for_clip(
        path, segments, 0.0, 3.0,
        hook="Get down like James Brown, nigga")

    assert written
    with open(written, encoding="utf-8") as handle:
        body = handle.read()
    hook_line = next(l for l in body.splitlines() if ",Hook,," in l)
    assert "nigga" not in hook_line.lower()
    assert "n***" not in hook_line.lower(), "a starred stump still reads as the word"
    assert "GET DOWN LIKE JAMES BROWN" in hook_line, "the rest of the line survives"


def test_ordinary_profanity_in_the_hook_is_masked_not_dropped(tmp_path):
    path = str(tmp_path / "clip.ass")

    written = caption_file_for_clip(
        path, [], 0.0, 3.0, hook="holy shit that was insane")

    with open(written, encoding="utf-8") as handle:
        body = handle.read()
    hook_line = next(l for l in body.splitlines() if ",Hook,," in l)
    assert "SHIT" not in hook_line
    assert "S***" in hook_line
    assert "INSANE" in hook_line


def test_a_hook_that_is_only_a_slur_burns_nothing(tmp_path):
    """Dropping the one word it had leaves nothing worth pinning - better
    than an empty banner is no banner, same as no hook at all."""
    path = str(tmp_path / "clip.ass")

    written = caption_file_for_clip(path, [], 0.0, 3.0, hook="nigga")

    assert written is None


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
    thumbnails read "VOULD HAVE BE" and "MEET HIM IN REA".

    Bounded PER LINE. A phrase may use MAX_LINES of these; what has to
    stay inside the frame is one rendered line."""
    from autoreel.captions import (DEFAULT_FONT_SIZE, MAX_CHARS_PER_LINE,
                                   fits_in_frame)

    assert fits_in_frame(MAX_CHARS_PER_LINE, DEFAULT_FONT_SIZE), (
        f"{MAX_CHARS_PER_LINE} chars at {DEFAULT_FONT_SIZE}px runs off the "
        f"edge - the two numbers are a pair")
    assert not fits_in_frame(28, 84), "the old pair has to stay a failure"


def test_no_rendered_line_is_wider_than_the_frame_allows():
    """The phrase may be two lines long; neither of them may be too wide.

    Checked on the BROKEN lines rather than the whole phrase - a 40
    character phrase is fine, a 40 character line is not."""
    from autoreel.captions import MAX_CHARS_PER_LINE, break_after, group_words

    words = [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.25}
             for i, w in enumerate(
                 "absolutely everybody watching this right now".split())]

    for phrase in group_words(words):
        parts = phrase.text.split()
        split_at = break_after(parts)
        first = parts if split_at < 0 else parts[:split_at + 1]
        rest = [] if split_at < 0 else parts[split_at + 1:]
        for line in (" ".join(first), " ".join(rest)):
            assert len(line) <= MAX_CHARS_PER_LINE, f"too wide: {line!r}"


def test_two_long_words_do_not_overflow_onto_a_third_line():
    """A character total cannot catch this: "aa" plus two eighteen
    character words is 40 characters - inside the budget - and needs
    three lines to render. The budget is counted in LINES for exactly
    this reason."""
    from autoreel.captions import lays_out_in_lines

    assert not lays_out_in_lines(["aa", "c" * 18, "d" * 18])
    assert lays_out_in_lines(["i", "haven't", "figured", "it", "out", "yet"])
    # One word wider than a line has nowhere better to go - it must not
    # be refused forever, or the phrase containing it never closes.
    assert lays_out_in_lines(["supercalifragilisticexpialidocious"])


def test_a_phrase_can_now_hold_a_whole_short_sentence():
    """The bug this fixes: one line of 20 characters cut sentences in
    half. "I HAVEN'T FIGURED IT" went out with "out" on the next
    caption, and a fragment reads as a transcription error even when the
    transcript was right."""
    from autoreel.captions import group_words

    said = "i haven't figured it out yet".split()
    words = [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.25}
             for i, w in enumerate(said)]

    phrases = group_words(words)

    assert len(phrases) == 1, \
        f"still fragmenting: {[p.text for p in phrases]}"
    assert phrases[0].text == "i haven't figured it out yet"


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


def test_every_platform_bleeps_slurs_and_leaves_the_swearing():
    """Shorts and TikTok were "all" - every ordinary swear muted too.

    On a channel whose speech is mostly ordinary swearing that is a hole
    in the audio every few seconds, and the clips came back unwatchable.
    Slurs are the line that actually costs a channel and that line is
    still held; the rest is the voice people are there for."""
    import sys

    sys.path.insert(0, os.path.join(_REPO, "auto_uploader"))
    from autoreel.compliance import ComplianceEngine
    from utils.clip_queue import CENSOR_AUDIO_DEFAULTS, _CENSOR_SCOPES

    for platform, mode in CENSOR_AUDIO_DEFAULTS.items():
        assert mode == "slurs", f"{platform} is back to muting everything"
        engine = ComplianceEngine(only_categories=_CENSOR_SCOPES[mode])
        assert engine._flag_reason("nigga") == "hate_speech", platform
        assert engine._flag_reason("faggot") == "hate_speech", platform
        assert engine._flag_reason("shit") is None, platform
        assert engine._flag_reason("fuck") is None, platform


def test_the_stream_and_its_clips_agree_on_what_counts():
    """The full VOD passed no category filter at all, so it meant "all"
    with no way to say otherwise - a stream muted every swear while a
    clip cut from that same stream muted only slurs."""
    from utils.clip_queue import scope_categories
    from utils.config import GeneralConfig

    assert GeneralConfig.censor_categories == "slurs"
    assert scope_categories("slurs") == ("hate_speech",)
    # A typo must over-censor and be noticed, never quietly publish one.
    assert scope_categories("sluurs") == ()
    assert scope_categories("") == ()


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
