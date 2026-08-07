"""
Transcript-export and audio-censoring tests for bleep_engine.

No whisper model, no GPU, no network. pydub is used to synthesise audio in
memory (its Sine generator and `AudioSegment.silent` need no ffmpeg), and
skipped cleanly if pydub isn't installed.
"""

from __future__ import annotations

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bleep_engine as engine  # noqa: E402
from bleep_engine import (  # noqa: E402
    METHOD_BEEP,
    METHOD_SILENCE,
    ProcessOptions,
    bleeps_to_srt,
    group_into_cues,
    sidecar_path,
    words_to_srt,
    words_to_txt,
)

pydub = pytest.importorskip("pydub", reason="pydub is required for the audio tests")
from pydub import AudioSegment  # noqa: E402
from pydub.generators import Sine  # noqa: E402

SRT_TIME = re.compile(
    r"^\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}$")


def transcript(*words: tuple[str, float, float]) -> dict:
    return {"segments": [
        {"words": [{"word": w, "start": s, "end": e} for w, s, e in words]}]}


SAMPLE = transcript(
    ("hello", 0.0, 0.40),
    ("there", 0.45, 0.80),
    ("this", 0.90, 1.10),
    ("is", 1.15, 1.30),
    ("shit", 1.40, 1.80),
    ("right", 3.50, 3.90),   # 1.7s gap: forces a new cue
    ("now", 3.95, 4.20),
)


# ═════════════════════════════════════════════════════════════════════════════
# SRT
# ═════════════════════════════════════════════════════════════════════════════

def test_srt_written_and_wellformed(tmp_path):
    out = words_to_srt(SAMPLE, tmp_path / "cap.srt")
    assert out.exists()
    blocks = [b for b in out.read_text(encoding="utf-8").split("\n\n") if b.strip()]
    assert blocks, "SRT must not be empty"

    for expected_index, block in enumerate(blocks, 1):
        lines = block.splitlines()
        assert lines[0] == str(expected_index)      # sequential index
        assert SRT_TIME.match(lines[1]), lines[1]   # HH:MM:SS,mmm --> ...
        assert " --> " in lines[1]
        assert lines[2].strip()                     # non-empty body


def test_srt_returns_the_path_it_wrote(tmp_path):
    target = tmp_path / "x.srt"
    assert words_to_srt(SAMPLE, target) == target


def test_srt_accepts_a_flat_word_list(tmp_path):
    flat = SAMPLE["segments"][0]["words"]
    out = words_to_srt(flat, tmp_path / "flat.srt")
    assert "hello" in out.read_text(encoding="utf-8")


def test_srt_accepts_str_path(tmp_path):
    out = words_to_srt(SAMPLE, str(tmp_path / "s.srt"))
    assert out.exists()


def test_srt_splits_on_a_long_gap(tmp_path):
    text = words_to_srt(SAMPLE, tmp_path / "cap.srt").read_text(encoding="utf-8")
    assert "2\n" in text, "the 1.7s silence should start a second cue"
    assert "right now" in text


def test_srt_timestamps_are_ordered(tmp_path):
    text = words_to_srt(SAMPLE, tmp_path / "cap.srt").read_text(encoding="utf-8")
    starts = re.findall(r"^(\d{2}:\d{2}:\d{2},\d{3}) -->", text, re.M)
    assert starts == sorted(starts)


def test_srt_handles_hours():
    assert engine._srt_timestamp(3725.5) == "01:02:05,500"
    assert engine._srt_timestamp(0) == "00:00:00,000"
    assert engine._srt_timestamp(-3) == "00:00:00,000"


def test_empty_transcript_writes_an_empty_file(tmp_path):
    out = words_to_srt({"segments": []}, tmp_path / "empty.srt")
    assert out.exists() and out.read_text(encoding="utf-8").strip() == ""


def test_srt_creates_missing_parent_directories(tmp_path):
    out = words_to_srt(SAMPLE, tmp_path / "nested" / "deep" / "c.srt")
    assert out.exists()


def test_zero_length_cue_gets_a_floor(tmp_path):
    out = words_to_srt(transcript(("hi", 2.0, 2.0)), tmp_path / "z.srt")
    line = out.read_text(encoding="utf-8").splitlines()[1]
    start, end = line.split(" --> ")
    assert end > start


# ═════════════════════════════════════════════════════════════════════════════
# TXT
# ═════════════════════════════════════════════════════════════════════════════

def test_txt_is_timestamped(tmp_path):
    out = words_to_txt(SAMPLE, tmp_path / "t.txt")
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert lines
    for line in lines:
        assert re.match(r"^\[\d{1,2}:\d{2}(:\d{2})?\] \S", line), line
    assert "hello there" in lines[0]


def test_txt_empty_input(tmp_path):
    out = words_to_txt({}, tmp_path / "e.txt")
    assert out.exists() and out.read_text(encoding="utf-8") == ""


def test_bleeps_only_srt_lists_reasons(tmp_path):
    hits = engine.find_profanity_v2(SAMPLE, [])
    out = bleeps_to_srt(hits, tmp_path / "b.srt")
    text = out.read_text(encoding="utf-8")
    assert "shit" in text
    assert "[Profanity detected]" in text
    assert "hello" not in text


def test_group_into_cues_respects_max_words():
    words = [{"word": f"w{i}", "start": i * 0.2, "end": i * 0.2 + 0.1}
             for i in range(20)]
    cues = group_into_cues(words, max_words=5, max_duration=99, max_gap=99)
    assert all(len(c["text"].split()) <= 5 for c in cues)
    assert sum(len(c["text"].split()) for c in cues) == 20


def test_sidecar_path_swaps_the_extension():
    assert str(sidecar_path("/a/b/clip_CLEAN.mp4", ".srt")) == "/a/b/clip_CLEAN.srt"
    assert str(sidecar_path("/a/b/clip_CLEAN.mp4", "txt")) == "/a/b/clip_CLEAN.txt"


# ═════════════════════════════════════════════════════════════════════════════
# Censoring audio
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def tone():
    """5 seconds of 220 Hz, 16 kHz mono - same shape as extracted audio."""
    return Sine(220).to_audio_segment(duration=5000) \
                    .set_frame_rate(16000).set_channels(1)


def test_silence_method_preserves_length(tone):
    out = engine.apply_bleeps(tone, [{"start": 1.0, "end": 1.5}],
                              method=METHOD_SILENCE)
    assert len(out) == len(tone)


def test_silence_method_is_actually_silent(tone):
    out = engine.apply_bleeps(tone, [{"start": 1.0, "end": 2.0}],
                              method=METHOD_SILENCE)
    assert out[1000:2000].max == 0, "censored region must contain no tone"
    assert out[3000:4000].max > 0, "the rest must be untouched"


def test_silence_leaves_surrounding_audio_byte_identical(tone):
    """Outside the censored span nothing is re-encoded or shifted - the
    bytes are the original ones. The span now starts DEFAULT_PADDING_MS
    early, because muting the exact reported word leaves its leading
    syllable audible."""
    out = engine.apply_bleeps(tone, [{"start": 2.0, "end": 3.0}],
                              method=METHOD_SILENCE)
    untouched_ms = 2000 - engine.DEFAULT_PADDING_MS
    cut = untouched_ms * tone.frame_rate // 1000 * tone.frame_width
    assert out.raw_data[:cut] == tone.raw_data[:cut]
    assert out[untouched_ms:3000].max == 0, "the padded span must be silent"


def test_short_span_does_not_stretch_the_track(tone):
    """The v2.2 desync bug: sub-50ms hits used to lengthen the audio."""
    hits = [{"start": 1.0, "end": 1.01}, {"start": 2.0, "end": 2.005}]
    out = engine.apply_bleeps(tone, hits, method=METHOD_SILENCE)
    assert len(out) == len(tone)


def test_overlapping_hits_preserve_length(tone):
    hits = [{"start": 1.0, "end": 2.0}, {"start": 1.5, "end": 2.5}]
    out = engine.apply_bleeps(tone, hits, method=METHOD_SILENCE)
    assert len(out) == len(tone)


def test_no_hits_returns_input_unchanged(tone):
    assert engine.apply_bleeps(tone, [], method=METHOD_SILENCE) is tone


def test_beep_method_preserves_length_and_is_audible(tone):
    out = engine.apply_bleeps(tone, [{"start": 1.0, "end": 1.5}],
                              method=METHOD_BEEP, freq_hz=1000)
    assert len(out) == len(tone)
    assert out[1000:1500].max > 0


# ── make_bleep_segment ───────────────────────────────────────────────────────

@pytest.mark.parametrize("duration", [1, 50, 250, 1000, 4321])
def test_make_bleep_segment_exact_length_from_tone(duration):
    assert len(engine.make_bleep_segment(duration, 1000)) == duration


@pytest.fixture
def custom_wav(tmp_path):
    path = tmp_path / "beep.wav"
    Sine(880).to_audio_segment(duration=300).export(path, format="wav")
    return str(path)


@pytest.mark.parametrize("duration", [100, 300, 1000, 2500])
def test_custom_wav_is_looped_or_trimmed_to_exact_length(custom_wav, duration):
    assert len(engine.make_bleep_segment(duration, 1000, custom_wav)) == duration


def test_custom_wav_is_actually_used(custom_wav):
    """A 880 Hz sample must differ from the 1000 Hz generated tone."""
    from_custom = engine.make_bleep_segment(300, 1000, custom_wav).raw_data
    from_tone = engine.make_bleep_segment(300, 1000, None).raw_data
    assert from_custom != from_tone


def test_missing_custom_wav_falls_back_to_the_tone(tmp_path):
    seg = engine.make_bleep_segment(500, 1000, str(tmp_path / "nope.wav"))
    assert len(seg) == 500
    assert seg.raw_data == engine.make_bleep_segment(500, 1000, None).raw_data


def test_unreadable_custom_wav_falls_back_to_the_tone(tmp_path):
    bad = tmp_path / "bad.wav"
    bad.write_text("this is not audio")
    seg = engine.make_bleep_segment(500, 1000, str(bad))
    assert len(seg) == 500
    assert seg.raw_data == engine.make_bleep_segment(500, 1000, None).raw_data


def test_apply_bleeps_with_custom_wav_preserves_length(tone, custom_wav):
    out = engine.apply_bleeps(tone, [{"start": 1.0, "end": 1.4}],
                              method=METHOD_BEEP, custom_wav=custom_wav)
    assert len(out) == len(tone)


def test_validate_beep_wav(custom_wav, tmp_path):
    ok, reason = engine.validate_beep_wav(custom_wav)
    assert ok and "beep.wav" in reason

    ok, reason = engine.validate_beep_wav(str(tmp_path / "missing.wav"))
    assert not ok and "not found" in reason

    ok, reason = engine.validate_beep_wav(None)
    assert not ok


# ═════════════════════════════════════════════════════════════════════════════
# Defaults / options
# ═════════════════════════════════════════════════════════════════════════════

def test_default_method_is_mute_not_beep():
    assert engine.DEFAULT_METHOD == METHOD_SILENCE
    assert ProcessOptions().method == METHOD_SILENCE


def test_process_options_defaults_are_conservative():
    opts = ProcessOptions()
    assert opts.write_video is True
    assert opts.write_srt is False and opts.write_txt is False
    assert opts.sensitivity == engine.DEFAULT_SENSITIVITY
    assert opts.custom_beep_wav is None


def test_process_options_is_immutable():
    opts = ProcessOptions()
    with pytest.raises(Exception):
        opts.method = METHOD_BEEP  # type: ignore[misc]


def test_list_videos_skips_generated_output(tmp_path):
    for name in ("a.mp4", "b.mkv", "c_CLEAN.mp4", "notes.txt"):
        (tmp_path / name).write_bytes(b"")
    found = [os.path.basename(p) for p in engine.list_videos(tmp_path)]
    assert found == ["a.mp4", "b.mkv"]


# ═════════════════════════════════════════════════════════════════════════════
# Take the word, leave the sentence
#
# This engine had no padding at all: it muted the exact span Whisper
# reported, and those timings are 100-300ms out, so the leading syllable
# routinely survived. Padding fixes that and creates the opposite risk -
# clipping the words either side - so the pad expands into silence and
# stops when it reaches speech.
# ═════════════════════════════════════════════════════════════════════════════

def test_the_leading_syllable_is_covered(tone):
    """The reason padding exists. Without it the moment just before the
    reported start stayed audible, and that is where the word actually
    begins."""
    out = engine.apply_bleeps(tone, [{"start": 2.0, "end": 3.0}],
                              method=METHOD_SILENCE)
    assert out[1_900:2_000].max == 0


def test_padding_stops_at_the_neighbouring_words(tone):
    """The other half: with speech packed either side, the pad shrinks
    instead of clipping it."""
    all_words = [{"word": "oh", "start": 1.8, "end": 1.95},
                 {"word": "shit", "start": 2.0, "end": 3.0},
                 {"word": "man", "start": 3.05, "end": 3.4}]
    out = engine.apply_bleeps(tone, [all_words[1]], method=METHOD_SILENCE,
                              all_words=all_words)
    assert out[1_800:1_820].max > 0, '"oh" was clipped'
    assert out[3_300:3_400].max > 0, '"man" was clipped'
    assert out[2_100:2_900].max == 0, "the flagged word survived"


def test_full_padding_is_used_where_there_is_silence(tone):
    """Muting a gap between words costs nothing, so nothing is given up
    where there is room for it."""
    all_words = [{"word": "oh", "start": 0.5, "end": 0.9},
                 {"word": "shit", "start": 2.0, "end": 3.0}]
    out = engine.apply_bleeps(tone, [all_words[1]], method=METHOD_SILENCE,
                              all_words=all_words)
    assert out[1_760:1_990].max == 0, "the pad was not used despite the gap"


def test_the_transcript_is_optional(tone):
    """apply_bleeps is called from two places and one of them may not
    have the full word list; padding still applies, just unclamped."""
    out = engine.apply_bleeps(tone, [{"start": 2.0, "end": 3.0}],
                              method=METHOD_SILENCE, all_words=None)
    assert out[1_800:2_000].max == 0


def test_padding_can_be_turned_off(tone):
    out = engine.apply_bleeps(tone, [{"start": 2.0, "end": 3.0}],
                              method=METHOD_SILENCE, padding_ms=0)
    assert out[1_900:1_990].max > 0


def test_padding_never_changes_the_track_length(tone):
    """Any drift desyncs the audio from the video."""
    out = engine.apply_bleeps(tone, [{"start": 0.05, "end": 0.1},
                                     {"start": 59.9, "end": 59.99}],
                              method=METHOD_SILENCE)
    assert len(out) == len(tone)
