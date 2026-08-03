"""
Detection unit tests for bleep_engine.

Deliberately dependency-light: no GPU, no ffmpeg, no whisper model, no
audio decoding. Transcription results are plain dicts in whisper's shape,
so these run on any CI runner with `better_profanity` installed.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bleep_engine import (  # noqa: E402
    build_output_path,
    check_word,
    find_profanity_v2,
    is_generated_output,
    merge_spans,
)


def flagged(word: str, context: list[str] | None = None,
            custom: list[str] | None = None, fuzzy: bool = True) -> bool:
    is_bad, _ = check_word(word, context or [], custom or [], fuzzy=fuzzy)
    return is_bad


def make_result(*words: tuple[str, float, float]) -> dict:
    """Build a whisper-shaped result from (word, start, end) triples."""
    return {
        "segments": [
            {"words": [{"word": w, "start": s, "end": e} for w, s, e in words]}
        ]
    }


# ── Clean words ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("word", [
    "hello", "the", "computer", "streaming", "keyboard", "yesterday",
    "class", "pass", "assassin", "grass", "shoreline",
])
def test_clean_words_not_flagged(word):
    assert not flagged(word)


# ── Direct profanity ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("word", ["fuck", "shit", "bitch", "ass", "damn"])
def test_direct_profanity_flagged(word):
    assert flagged(word)


@pytest.mark.parametrize("word", ["fucking", "fuckin", "motherfucker", "bullshit"])
def test_inflected_profanity_flagged(word):
    assert flagged(word)


# ── Leet speak ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("word", ["sh1t", "f@ck", "a$$", "f*ck", "sh*t", "b1tch", "5hit"])
def test_leet_flagged(word):
    assert flagged(word)


def test_leet_reason_is_specific():
    _, reason = check_word("sh1t", [], [])
    assert "leet" in reason.lower()


# ── Symbol bypass ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("word", ["f**k", "s**t", "b***h", "f***"])
def test_symbol_bypass_flagged(word):
    assert flagged(word)


def test_symbol_bypass_reason():
    _, reason = check_word("s**t", [], [])
    assert "bypass" in reason.lower()


# ── Homophones / minced oaths ────────────────────────────────────────────────

@pytest.mark.parametrize("word", ["fudge", "shoot", "dang", "darn", "frick", "heck"])
def test_non_context_homophones_flagged(word):
    assert flagged(word)


@pytest.mark.parametrize("word", ["fudge", "shoot", "dang"])
def test_homophones_suppressed_when_fuzzy_off(word):
    assert not flagged(word, fuzzy=False)


# ── Context-only homophones ──────────────────────────────────────────────────

def test_beach_alone_not_flagged():
    assert not flagged("beach")


def test_beach_after_son_of_a_is_flagged():
    assert flagged("beach", ["son", "of", "a"])


def test_beach_with_unrelated_context_not_flagged():
    # "what the" points at hell, not bitch - it must not drag "beach" in.
    assert not flagged("beach", ["what", "the"])


@pytest.mark.parametrize("word,context", [
    ("sheet", ["holy"]),
    ("rich", ["son", "of", "a"]),
    ("bass", ["dumb"]),
])
def test_other_context_only_homophones(word, context):
    assert not flagged(word)
    assert flagged(word, context)


# ── Whisper mishears ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("word", ["duck", "shirt", "batch", "cluck"])
def test_mishears_flagged(word):
    assert flagged(word)


@pytest.mark.parametrize("word", ["shot", "truck", "luck", "stuck"])
def test_context_only_mishears_need_context(word):
    assert not flagged(word)
    assert flagged(word, ["fucking"])


# ── Custom words ─────────────────────────────────────────────────────────────

def test_custom_single_word_match():
    assert flagged("pepsi", custom=["pepsi"])


def test_custom_word_no_false_match():
    assert not flagged("water", custom=["pepsi"])


def test_custom_multiword_phrase_matches_across_context():
    # The GUI's own placeholder advertises multi-word entries; a phrase can
    # never match a single token, so it's tested against the context window.
    assert flagged("brand", ["some", "rival"], custom=["rival brand"])


def test_custom_multiword_phrase_no_false_match():
    assert not flagged("brand", ["some", "other"], custom=["rival brand"])


# ── Edge cases ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("word", ["", "   ", "\t", "...", "!!!", ",", "-", "?!"])
def test_empty_and_punctuation_only_not_flagged(word):
    assert not flagged(word)


def test_no_crash_on_unicode():
    assert not flagged("café")
    assert not flagged("日本語")


def test_accented_profanity_still_caught():
    assert flagged("fück")


# ── find_profanity_v2 ────────────────────────────────────────────────────────

def test_finds_only_the_bad_word():
    result = make_result(("hello", 0.0, 0.4), ("shit", 0.5, 0.9), ("world", 1.0, 1.4))
    found = find_profanity_v2(result, [])
    assert [f["word"] for f in found] == ["shit"]
    assert found[0]["start"] == 0.5
    assert found[0]["end"] == 0.9


def test_dedupes_same_start_timestamp():
    # Same token twice at the same start: only one bleep should be emitted.
    result = {
        "segments": [
            {"words": [{"word": "shit", "start": 1.0, "end": 1.5}]},
            {"words": [{"word": "shit", "start": 1.0, "end": 1.5}]},
        ]
    }
    assert len(find_profanity_v2(result, [])) == 1


def test_distinct_starts_are_kept():
    result = make_result(("shit", 1.0, 1.4), ("shit", 2.0, 2.4))
    assert len(find_profanity_v2(result, [])) == 2


def test_empty_result_shapes():
    assert find_profanity_v2({}, []) == []
    assert find_profanity_v2({"segments": []}, []) == []
    assert find_profanity_v2({"segments": [{"words": []}]}, []) == []
    assert find_profanity_v2({"segments": [{}]}, []) == []


def test_missing_keys_do_not_crash():
    result = {"segments": [{"words": [{"word": "shit"}]}]}
    found = find_profanity_v2(result, [])
    assert found and found[0]["start"] == 0.0


def test_context_is_taken_from_preceding_words():
    result = make_result(("son", 0.0, 0.2), ("of", 0.3, 0.4),
                         ("a", 0.5, 0.6), ("beach", 0.7, 1.0))
    assert [f["word"] for f in find_profanity_v2(result, [])] == ["beach"]

    lone = make_result(("the", 0.0, 0.2), ("beach", 0.3, 0.6))
    assert find_profanity_v2(lone, []) == []


def test_fuzzy_flag_threads_through():
    result = make_result(("fudge", 0.0, 0.4))
    assert len(find_profanity_v2(result, [], fuzzy=True)) == 1
    assert find_profanity_v2(result, [], fuzzy=False) == []


# ── Span maths (the O(n^2)/desync fix) ───────────────────────────────────────

def test_short_spans_widen_around_centre_not_by_stretching():
    spans = merge_spans([{"start": 1.0, "end": 1.01}], total_ms=10_000, min_ms=50)
    assert len(spans) == 1
    start, end = spans[0]
    assert end - start == 50
    assert start < 1005 < end          # still centred on the original word


def test_overlapping_spans_are_merged():
    spans = merge_spans(
        [{"start": 1.0, "end": 2.0}, {"start": 1.5, "end": 2.5}], total_ms=10_000)
    assert spans == [(1000, 2500)]


def test_spans_are_clamped_to_track_length():
    spans = merge_spans([{"start": 9.9, "end": 12.0}], total_ms=10_000)
    assert spans == [(9900, 10_000)]


def test_spans_sorted_regardless_of_input_order():
    spans = merge_spans(
        [{"start": 5.0, "end": 5.5}, {"start": 1.0, "end": 1.5}], total_ms=10_000)
    assert spans == [(1000, 1500), (5000, 5500)]


def test_reversed_timestamps_are_tolerated():
    assert merge_spans([{"start": 2.0, "end": 1.0}], total_ms=10_000) == [(1000, 2000)]


def test_span_out_of_range_is_dropped():
    assert merge_spans([{"start": 20.0, "end": 21.0}], total_ms=10_000) == []


# ── Output paths ─────────────────────────────────────────────────────────────

def test_build_output_path_defaults_next_to_input(tmp_path):
    video = tmp_path / "stream.mp4"
    video.write_bytes(b"")
    out = build_output_path(str(video), None)
    assert out == str(tmp_path / "stream_CLEAN.mp4")


def test_build_output_path_honours_out_dir(tmp_path):
    dest = tmp_path / "out"
    dest.mkdir()
    out = build_output_path(str(tmp_path / "stream.mp4"), str(dest))
    assert out == str(dest / "stream_CLEAN.mp4")


def test_build_output_path_does_not_overwrite(tmp_path):
    (tmp_path / "stream_CLEAN.mp4").write_bytes(b"existing")
    out = build_output_path(str(tmp_path / "stream.mp4"), None)
    assert out == str(tmp_path / "stream_CLEAN_1.mp4")


def test_build_output_path_can_overwrite_when_asked(tmp_path):
    (tmp_path / "stream_CLEAN.mp4").write_bytes(b"existing")
    out = build_output_path(str(tmp_path / "stream.mp4"), None, avoid_overwrite=False)
    assert out == str(tmp_path / "stream_CLEAN.mp4")


@pytest.mark.parametrize("name,expected", [
    ("stream_CLEAN.mp4", True),
    ("stream_CLEAN_2.mp4", True),
    ("stream.mp4", False),
    ("CLEAN_stream.mp4", False),
])
def test_is_generated_output(name, expected):
    assert is_generated_output(name) is expected
