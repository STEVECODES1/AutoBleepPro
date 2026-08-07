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
    BAND_HIGH,
    BAND_LOW,
    BAND_NORMAL,
    DEFAULT_SENSITIVITY,
    build_output_path,
    check_word,
    clamp_sensitivity,
    find_profanity_v2,
    is_generated_output,
    merge_spans,
    sensitivity_band,
)


def flagged(word: str, context: list[str] | None = None,
            custom: list[str] | None = None, fuzzy: bool = True,
            sensitivity: int = DEFAULT_SENSITIVITY) -> bool:
    is_bad, _ = check_word(word, context or [], custom or [],
                           fuzzy=fuzzy, sensitivity=sensitivity)
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


# ── Sensitivity gating ───────────────────────────────────────────────────────

@pytest.mark.parametrize("value,band", [
    (0, BAND_LOW), (15, BAND_LOW), (30, BAND_LOW),
    (31, BAND_NORMAL), (50, BAND_NORMAL), (70, BAND_NORMAL),
    (71, BAND_HIGH), (100, BAND_HIGH),
])
def test_sensitivity_bands(value, band):
    assert sensitivity_band(value) == band


@pytest.mark.parametrize("value,expected", [
    (-10, 0), (0, 0), (100, 100), (250, 100),
    (None, DEFAULT_SENSITIVITY), ("nonsense", DEFAULT_SENSITIVITY), (70.4, 70),
])
def test_clamp_sensitivity(value, expected):
    assert clamp_sensitivity(value) == expected


def test_default_sensitivity_is_the_normal_band():
    assert sensitivity_band(DEFAULT_SENSITIVITY) == BAND_NORMAL


# Low band: real profanity only.
@pytest.mark.parametrize("word", ["fuck", "shit", "bitch"])
def test_low_band_still_flags_real_profanity(word):
    assert flagged(word, sensitivity=10)


@pytest.mark.parametrize("word", ["sh1t", "f@ck", "f*ck"])
def test_low_band_still_flags_leet(word):
    assert flagged(word, sensitivity=10)


@pytest.mark.parametrize("word", ["f**k", "s**t", "b***h"])
def test_low_band_still_flags_symbol_bypass(word):
    assert flagged(word, sensitivity=10)


def test_low_band_still_honours_custom_words():
    # An explicit user instruction must not be gated away by sensitivity.
    assert flagged("pepsi", custom=["pepsi"], sensitivity=0)


@pytest.mark.parametrize("word", ["fudge", "shoot", "dang", "heck", "frick"])
def test_low_band_ignores_minced_oaths(word):
    assert not flagged(word, sensitivity=10)
    assert flagged(word, sensitivity=DEFAULT_SENSITIVITY)


@pytest.mark.parametrize("word", ["duck", "shirt", "batch"])
def test_low_band_ignores_whisper_mishears(word):
    assert not flagged(word, sensitivity=10)
    assert flagged(word, sensitivity=DEFAULT_SENSITIVITY)


def test_low_band_ignores_context_matches():
    assert not flagged("beach", ["son", "of", "a"], sensitivity=10)
    assert flagged("beach", ["son", "of", "a"], sensitivity=DEFAULT_SENSITIVITY)


# High band: context-only candidates fire on weaker context.
def test_high_band_accepts_weak_context_for_homophones():
    # "what the" points at hell, not bitch: normal band rejects, high accepts.
    assert not flagged("beach", ["what", "the"], sensitivity=DEFAULT_SENSITIVITY)
    assert flagged("beach", ["what", "the"], sensitivity=90)


def test_high_band_accepts_profane_neighbour_for_homophones():
    assert not flagged("beach", ["fucking"], sensitivity=DEFAULT_SENSITIVITY)
    assert flagged("beach", ["fucking"], sensitivity=90)


def test_high_band_accepts_weak_context_for_mishears():
    assert not flagged("truck", ["what", "the"], sensitivity=DEFAULT_SENSITIVITY)
    assert flagged("truck", ["what", "the"], sensitivity=90)


def test_high_band_still_needs_some_context():
    # Aggressive is not indiscriminate: a bare context-only word stays clean
    # at every sensitivity, otherwise every "truck" in the video is censored.
    for value in (0, 50, 100):
        assert not flagged("beach", sensitivity=value)
        assert not flagged("truck", sensitivity=value)


@pytest.mark.parametrize("word", ["hello", "computer", "keyboard"])
def test_clean_words_stay_clean_at_max_sensitivity(word):
    assert not flagged(word, sensitivity=100)


def test_fuzzy_false_still_clamps_into_the_low_band():
    # Back-compat: the old fuzzy flag maps onto the low band.
    assert not flagged("fudge", fuzzy=False, sensitivity=100)
    assert flagged("fuck", fuzzy=False, sensitivity=100)


def test_find_profanity_threads_sensitivity_through():
    result = make_result(("fudge", 0.0, 0.4))
    assert find_profanity_v2(result, [], sensitivity=10) == []
    assert len(find_profanity_v2(result, [], sensitivity=50)) == 1
    assert len(find_profanity_v2(result, [], sensitivity=90)) == 1


def test_sensitivity_is_monotonic_over_a_realistic_line():
    """More sensitivity must never flag strictly fewer words."""
    result = make_result(
        ("what", 0.0, 0.2), ("the", 0.3, 0.4), ("beach", 0.5, 0.9),
        ("this", 1.0, 1.2), ("is", 1.3, 1.4), ("fudge", 1.5, 1.9),
        ("shit", 2.0, 2.4), ("truck", 2.5, 2.9),
    )
    counts = [len(find_profanity_v2(result, [], sensitivity=s))
              for s in (0, 50, 100)]
    assert counts == sorted(counts), counts
    assert counts[0] == 1        # only "shit"
    assert counts[-1] > counts[0]


# ═════════════════════════════════════════════════════════════════════════════
# Getting the words into the transcript at all
# ═════════════════════════════════════════════════════════════════════════════

def test_transcription_asks_for_verbatim_profanity():
    """Whisper is trained on cleaned transcripts and sanitises swearing -
    writing "f***" or softening a slur. A word the transcript never
    contains cannot be muted, so it reaches the upload untouched. This is
    the single biggest reason profanity slips through."""
    from autoreel.transcription import VERBATIM_PROMPT

    lowered = VERBATIM_PROMPT.lower()
    assert "verbatim" in lowered
    assert "no censoring" in lowered
    # Explicit examples are what actually bias the decode; a polite
    # request on its own does not.
    assert any(word in lowered for word in ("fuck", "shit"))


def test_the_prompt_is_short_enough_to_be_accepted():
    """Whisper's conditioning window is 224 tokens; an over-long prompt is
    truncated and the examples at the end - the part that matters - are
    the first thing lost."""
    from autoreel.transcription import VERBATIM_PROMPT

    assert len(VERBATIM_PROMPT.split()) < 150


def test_the_shipped_model_is_not_the_tiny_one():
    """base misses a large share of fast, noisy gameplay speech."""
    import json
    import os

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "auto_uploader", "config.json")
    with open(path) as f:
        model = json.load(f)["general"]["censor_model"]
    assert model not in ("tiny", "base"), \
        f"censor_model is {model!r}: too small to catch everything spoken"


# ═════════════════════════════════════════════════════════════════════════════
# Whisper sanitises profanity unless told not to
#
# This is upstream of every rule above: a word the transcript never
# contains cannot be matched, scored or muted, and it reaches the export
# untouched. Whisper is trained on cleaned-up transcripts and will write
# "f***", soften a slur, or drop it entirely.
# ═════════════════════════════════════════════════════════════════════════════

def test_the_decode_is_biased_toward_verbatim():
    from bleep_engine import VERBATIM_PROMPT, transcribe_options

    assert transcribe_options("faster-whisper")["initial_prompt"] == VERBATIM_PROMPT
    assert transcribe_options("openai-whisper")["initial_prompt"] == VERBATIM_PROMPT


def test_the_prompt_actually_contains_the_words_it_is_biasing_toward():
    """A politely-worded prompt asking for uncensored output does not
    work - the bias comes from the register of the text itself."""
    from bleep_engine import VERBATIM_PROMPT

    lowered = VERBATIM_PROMPT.lower()
    assert "fuck" in lowered and "shit" in lowered
    assert "*" not in VERBATIM_PROMPT, "the prompt censors itself"


def test_windows_are_not_conditioned_on_previous_output():
    """Over hours of gameplay one bad window makes the next worse: it
    loops or drifts, and whole minutes come back as repeated filler with
    the real words gone."""
    from bleep_engine import transcribe_options

    for backend in ("faster-whisper", "openai-whisper"):
        assert transcribe_options(backend)["condition_on_previous_text"] is False


def test_word_timestamps_are_always_requested():
    """Without them there is nothing to mute - only whole segments."""
    from bleep_engine import transcribe_options

    for backend in ("faster-whisper", "openai-whisper"):
        assert transcribe_options(backend)["word_timestamps"] is True


def test_beam_search_is_only_sent_to_the_backend_that_takes_it():
    """openai-whisper's transcribe() rejects beam_size as an unexpected
    keyword, which would turn a wider search into a crash."""
    from bleep_engine import transcribe_options

    assert transcribe_options("faster-whisper")["beam_size"] == 5
    assert "beam_size" not in transcribe_options("openai-whisper")


def test_the_most_accurate_model_can_be_chosen():
    """The model is what decides how much profanity is heard at all, and
    the list stopped at turbo - so the cleanest option was unreachable
    from the GUI and the CLI."""
    from bleep_engine import MODEL_CHOICES

    assert "large-v3" in MODEL_CHOICES
