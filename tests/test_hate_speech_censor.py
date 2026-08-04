"""
High-severity censoring: slurs.

The motivating incident: a stream was uploaded with profanity muted and
YouTube removed it under the hate speech policy anyway. Two reasons the
old censor couldn't have prevented that - it muted the exact Whisper span
(leaving the leading syllable audible) and it only ever muted the single
word, leaving the sentence around it intact.

Nothing here claims policy compliance; these pin the muting behaviour.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autoreel.compliance import (  # noqa: E402
    HIGH_SEVERITY_CATEGORIES,
    ComplianceEngine,
    Violation,
)


def segs():
    """A slur mid-sentence, and ordinary profanity in a later sentence."""
    return [
        {"start": 10.0, "end": 14.0, "words": [
            {"word": "these", "start": 10.0, "end": 10.3},
            {"word": "nigga", "start": 11.2, "end": 11.6},
            {"word": "people", "start": 11.7, "end": 12.1}]},
        {"start": 20.0, "end": 22.0, "words": [
            {"word": "oh", "start": 20.0, "end": 20.2},
            {"word": "shit", "start": 20.3, "end": 20.7}]},
    ]


# ── Classification ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("word", [
    "nigger", "nigga", "niggas", "faggot", "fag", "tranny",
    "retard", "retarded", "kike", "spic", "chink", "gook", "wetback",
])
def test_slurs_classify_as_hate_speech(word):
    """Not merely 'profanity'.

    better_profanity knows most of these, and it used to be consulted
    first - so they came back as plain profanity and never triggered the
    high-severity path they exist for.
    """
    assert ComplianceEngine()._flag_reason(word) == "hate_speech"


@pytest.mark.parametrize("word", ["shit", "fuck", "damn", "bitch"])
def test_ordinary_profanity_stays_ordinary(word):
    reason = ComplianceEngine()._flag_reason(word)
    assert reason == "profanity"
    assert reason not in HIGH_SEVERITY_CATEGORIES


@pytest.mark.parametrize("word", ["hello", "classic", "assignment", "shoot"])
def test_clean_words_are_not_flagged(word):
    assert ComplianceEngine()._flag_reason(word) is None


def test_high_severity_flag_on_the_violation():
    hits = ComplianceEngine().scan_segments(segs())
    by_word = {h.word: h for h in hits}
    assert by_word["nigga"].is_high_severity is True
    assert by_word["shit"].is_high_severity is False


def test_segment_bounds_are_recorded():
    """Needed so censor_audio can widen to the sentence without being
    handed the transcript separately."""
    hit = [h for h in ComplianceEngine().scan_segments(segs()) if h.is_high_severity][0]
    assert hit.segment_start == 10.0 and hit.segment_end == 14.0


# ── Padding ──────────────────────────────────────────────────────────────────

def test_padding_widens_the_mute_both_ways():
    """Whisper's timings run 100-300ms out; the exact span leaves the
    first syllable audible."""
    v = Violation("shit", 20.3, 20.7, "profanity")
    engine = ComplianceEngine(padding_ms=250, mute_whole_segment=False)
    assert engine.mute_spans([v], 60_000) == [(20_050, 20_950)]


def test_zero_padding_reproduces_the_old_behaviour():
    v = Violation("shit", 20.3, 20.7, "profanity")
    engine = ComplianceEngine(padding_ms=0, mute_whole_segment=False)
    assert engine.mute_spans([v], 60_000) == [(20_300, 20_700)]


def test_padding_is_clamped_at_the_track_edges():
    engine = ComplianceEngine(padding_ms=500, mute_whole_segment=False)
    start = engine.mute_spans([Violation("x", 0.1, 0.2, "profanity")], 60_000)
    assert start == [(0, 700)]
    end = engine.mute_spans([Violation("x", 59.9, 60.0, "profanity")], 60_000)
    assert end == [(59_400, 60_000)]


# ── Whole-segment muting ─────────────────────────────────────────────────────

def test_slur_mutes_the_whole_sentence():
    hits = ComplianceEngine().scan_segments(segs())
    spans = ComplianceEngine().mute_spans(hits, 60_000)
    # 10.0-14.0 widened by the padding, not just the 11.2-11.6 word.
    assert (9_750, 14_250) in spans


def test_ordinary_profanity_does_not_mute_the_sentence():
    hits = ComplianceEngine().scan_segments(segs())
    spans = ComplianceEngine().mute_spans(hits, 60_000)
    assert (20_050, 20_950) in spans, "should be word+padding, not 20.0-22.0"


def test_whole_segment_muting_can_be_switched_off():
    hits = ComplianceEngine(mute_whole_segment=False).scan_segments(segs())
    spans = ComplianceEngine(mute_whole_segment=False).mute_spans(hits, 60_000)
    assert (10_950, 11_850) in spans
    assert (9_750, 14_250) not in spans


def test_missing_segment_bounds_fall_back_to_the_word():
    """A caller using scan_words directly has no segment to widen to."""
    v = Violation("nigga", 11.2, 11.6, "hate_speech")
    assert ComplianceEngine().mute_spans([v], 60_000) == [(10_950, 11_850)]


# ── Span merging ─────────────────────────────────────────────────────────────

def test_overlapping_spans_merge():
    """Padding makes nearby hits overlap, which would corrupt the rebuild."""
    hits = [Violation("a", 1.0, 1.2, "profanity"), Violation("b", 1.3, 1.5, "profanity")]
    assert ComplianceEngine().mute_spans(hits, 60_000) == [(750, 1_750)]


def test_spans_come_back_sorted():
    hits = [Violation("late", 30.0, 30.2, "profanity"),
            Violation("early", 5.0, 5.2, "profanity")]
    spans = ComplianceEngine().mute_spans(hits, 60_000)
    assert spans == sorted(spans)


def test_no_violations_is_empty():
    assert ComplianceEngine().mute_spans([], 60_000) == []


# ── Audio output ─────────────────────────────────────────────────────────────

pydub = pytest.importorskip("pydub")
from pydub.generators import Sine  # noqa: E402


@pytest.fixture
def tone():
    return Sine(220).to_audio_segment(duration=60_000) \
                    .set_frame_rate(16_000).set_channels(1)


def test_slur_sentence_is_actually_silent(tone):
    engine = ComplianceEngine()
    out = engine.censor_audio(tone, engine.scan_segments(segs()), method="silence")
    assert out[10_500:13_500].max == 0, "the sentence around the slur must be muted"
    assert out[16_000:19_000].max > 0, "unrelated audio must survive"


def test_length_is_preserved(tone):
    engine = ComplianceEngine()
    out = engine.censor_audio(tone, engine.scan_segments(segs()), method="silence")
    assert len(out) == len(tone), "any drift desyncs the audio from the video"


def test_leading_syllable_is_covered(tone):
    """With zero padding the moment just before the word stayed audible."""
    v = [Violation("nigga", 11.2, 11.6, "hate_speech")]
    padded = ComplianceEngine(mute_whole_segment=False).censor_audio(
        tone, v, method="silence")
    unpadded = ComplianceEngine(padding_ms=0, mute_whole_segment=False).censor_audio(
        tone, v, method="silence")
    assert padded[11_000:11_150].max == 0
    assert unpadded[11_000:11_150].max > 0, "this is the syllable that used to leak"


def test_no_violations_returns_the_input(tone):
    assert ComplianceEngine().censor_audio(tone, [], method="silence") is tone


def test_beep_method_still_works(tone):
    engine = ComplianceEngine()
    out = engine.censor_audio(tone, engine.scan_segments(segs()), method="beep")
    assert len(out) == len(tone)
    assert out[11_000:11_500].max > 0, "beep should be audible, not silent"


def test_many_violations_stay_fast_and_correct(tone):
    """The old rebuild was O(n^2) - one full copy per violation."""
    import time
    hits = [Violation("x", i * 0.5, i * 0.5 + 0.1, "profanity") for i in range(1, 100)]
    started = time.perf_counter()
    out = ComplianceEngine().censor_audio(tone, hits, method="silence")
    assert time.perf_counter() - started < 5.0
    assert len(out) == len(tone)
