"""
YouTube is the heavily guarded platform: it earns from ads, and a strike
there costs the channel. Rumble is the uncensored one.

"ad_safe" mutes every swear word, slur and flagged phrase on the YouTube
copy - except the mild words YouTube's advertiser-friendly guidelines
allow (damn, hell) and ordinary words the filter flags anyway (god,
kill, stupid). Muting those put 1135 mutes in one stream.
"""

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (os.path.join(_REPO, "auto_uploader"), _REPO):
    if path not in sys.path:
        sys.path.insert(0, path)

from autoreel.compliance import MILD_WORDS, ComplianceEngine  # noqa: E402


def _engine():
    from utils.clip_queue import scope_allow, scope_categories

    return ComplianceEngine(only_categories=scope_categories("ad_safe"),
                            allow_words=scope_allow("ad_safe"))


def test_every_swear_word_and_slur_is_muted():
    engine = _engine()
    for word in ("fuck", "fucking", "shit", "bitch", "ass", "nigga",
                 "faggot"):
        assert engine._flag_reason(word), word


def test_the_mild_words_youtube_allows_are_left_alone():
    engine = _engine()
    for word in ("damn", "hell", "god", "stupid", "killed", "dead"):
        assert engine._flag_reason(word) is None, word


def test_slurs_mode_is_unchanged():
    from utils.clip_queue import scope_allow, scope_categories

    engine = ComplianceEngine(only_categories=scope_categories("slurs"),
                              allow_words=scope_allow("slurs"))
    assert engine._flag_reason("fuck") is None
    assert engine._flag_reason("nigga")


def test_ad_safe_is_the_default_for_streams_and_shorts():
    from utils.clip_queue import CENSOR_AUDIO_DEFAULTS
    from utils.config import GeneralConfig

    assert GeneralConfig.__dataclass_fields__[
        "censor_categories"].default == "ad_safe"
    assert CENSOR_AUDIO_DEFAULTS["youtube_shorts"] == "ad_safe"


def test_a_copy_made_under_old_settings_keeps_its_cache_key():
    """Only "ad_safe" changes the key - a copy made before it is still
    found under its old name rather than re-rendered."""
    from utils.censor import _settings_fingerprint

    old = _settings_fingerprint(250, False, ("hate_speech",), ())
    assert _settings_fingerprint(250, False, ("hate_speech",), (), ()) == old
    assert _settings_fingerprint(250, False, (), (), MILD_WORDS) != \
        _settings_fingerprint(250, False, (), (), ())


def test_the_youtube_title_is_cleaned_and_rumbles_is_not_touched():
    import main

    assert main.youtube_safe_title("This shit crazy") == "This s*** crazy"
    assert main.youtube_safe_title("what up nigga today") == "what up today"
    assert main.youtube_safe_title("KAI CENAT ALLEGATIONS") == \
        "KAI CENAT ALLEGATIONS"
    assert main.youtube_safe_title("DAMN") == "DAMN"


def test_spicy_is_not_a_slur():
    """A real stream: "spicy x2" under HIGH RISK hate speech, muted."""
    engine = ComplianceEngine(only_categories=("hate_speech",))
    for word in ("spicy", "spices", "spiced", "tardy"):
        assert engine._flag_reason(word) is None, word
    for word in ("spic", "spics", "tards", "nigga"):
        assert engine._flag_reason(word) == "hate_speech", word


def test_a_whole_sentence_mute_cannot_silence_a_whole_clip():
    """A real Reel went up with almost no audio: Whisper returned 20-30s
    "segments", and muting the whole segment around two slurs took most
    of a 40 second clip."""
    from autoreel.compliance import WHOLE_SEGMENT_REACH_S, Violation

    engine = ComplianceEngine(mute_whole_segment=True, padding_ms=0)
    slur = Violation(word="nigga", start=15.0, end=15.4,
                     category="hate_speech",
                     segment_start=0.0, segment_end=30.0)
    spans = engine.mute_spans([slur], total_ms=40_000)

    assert len(spans) == 1
    start_ms, end_ms = spans[0][:2]
    assert start_ms == int((15.0 - WHOLE_SEGMENT_REACH_S) * 1000)
    assert end_ms == int((15.4 + WHOLE_SEGMENT_REACH_S) * 1000)
    assert end_ms - start_ms < 6_000


def test_a_short_sentence_is_still_muted_whole():
    from autoreel.compliance import Violation

    engine = ComplianceEngine(mute_whole_segment=True, padding_ms=0)
    slur = Violation(word="nigga", start=5.0, end=5.4,
                     category="hate_speech",
                     segment_start=4.0, segment_end=7.0)
    start_ms, end_ms = engine.mute_spans([slur], total_ms=40_000)[0][:2]
    assert (start_ms, end_ms) == (4000, 7000)


def test_instagram_gets_the_original_to_mute_by_its_own_rule():
    """Not the stream's YouTube-censored copy on top of its own."""
    import inspect
    import main

    body = inspect.getsource(main)
    start = body.index("def instagram_clip_path")
    func = body[start:body.index("\n    def ", start + 10)]
    assert "upload_path_for(True)" not in func
    assert "vertical_path(video_path)" in func
