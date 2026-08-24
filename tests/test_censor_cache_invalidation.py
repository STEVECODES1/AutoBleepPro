"""A config change that "did nothing" - because the cache never noticed.

    censor_mute_whole_segment defaulting True -> False was supposed to
    turn a whole-sentence mute into a 740-900ms word-level one.

But censor_video's cache key was built from ONLY bleep_method and
model_name:

    cache_key = f"{bleep_method}-{model_name}"

padding_ms and mute_whole_segment were never in it. So a video censored
once under the OLD settings kept its cached filename forever - the very
first check in censor_video is "does this file already exist?", and it
did, so every later run reused the stale, over-muted copy without ever
re-reading padding_ms or mute_whole_segment again. The fix landed in the
code and changed nothing on disk for any video that had already been
through a censor pass - including a full stream, and every clip cut from
it, which is exactly what a user reported as "the audio sometimes cuts
the whole part and then starts playing again" persisting after the fix
that was supposed to prevent it.

The module's own comment already stated the principle this violated:
"The censoring settings are part of the cache key: a copy made with
bleep_method='beep' must NOT be silently reused after switching to
'silence'... Without this, changing the config appears to do nothing on
any file that was already processed." - true for the two settings it
listed, and silently false for the two it forgot.

Verified here with real ffmpeg and a real audio splice, not a mock: two
runs over the SAME source, differing only in mute_whole_segment, produce
two DIFFERENT files with DIFFERENT audio - proving a real re-render
happened, not a coincidence of filenames.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
for _path in (_REPO, _UPLOADER):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from utils import censor  # noqa: E402


def _have_ffmpeg() -> bool:
    from shutil import which
    return which("ffmpeg") is not None and which("ffprobe") is not None


# ── the fingerprint itself ────────────────────────────────────────────────

def test_mute_whole_segment_changes_the_fingerprint():
    a = censor._settings_fingerprint(250, True, (), ())
    b = censor._settings_fingerprint(250, False, (), ())

    assert a != b


def test_padding_ms_changes_the_fingerprint():
    a = censor._settings_fingerprint(250, False, (), ())
    b = censor._settings_fingerprint(500, False, (), ())

    assert a != b


def test_only_categories_changes_the_fingerprint():
    a = censor._settings_fingerprint(250, False, (), ())
    b = censor._settings_fingerprint(250, False, ("hate_speech",), ())

    assert a != b


def test_custom_words_changes_the_fingerprint():
    a = censor._settings_fingerprint(250, False, (), ())
    b = censor._settings_fingerprint(250, False, (), ("newword",))

    assert a != b


def test_identical_settings_produce_the_identical_fingerprint():
    a = censor._settings_fingerprint(250, False, ("hate_speech",), ("x",))
    b = censor._settings_fingerprint(250, False, ("hate_speech",), ("x",))

    assert a == b


def test_the_order_of_categories_and_words_does_not_matter():
    """A config file rewritten with the same values in a different order
    must not look like a settings change and force a needless re-render."""
    a = censor._settings_fingerprint(250, False, ("a", "b"), ("x", "y"))
    b = censor._settings_fingerprint(250, False, ("b", "a"), ("y", "x"))

    assert a == b


def test_bleep_method_and_model_are_still_in_the_full_key():
    """Guards the two settings that already worked - this fix must add
    to that, not replace it."""
    source = open(os.path.join(_UPLOADER, "utils", "censor.py"),
                  encoding="utf-8").read()
    spot = source.index("cache_key = (")

    assert "bleep_method" in source[spot:spot + 300]
    assert "model_name" in source[spot:spot + 300]
    assert "_settings_fingerprint" in source[spot:spot + 300]


# ── end to end, with real ffmpeg: proving a real re-render happens ───────

@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not installed")
def test_a_changed_setting_forces_a_real_re_render_with_different_audio(
        tmp_path):
    source = str(tmp_path / "stream.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=20",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", source], check=True)

    work_dir = str(tmp_path / "work")
    os.makedirs(work_dir, exist_ok=True)
    basename = "stream"

    # One flagged hate-speech word at second 10, inside a long segment -
    # so mute_whole_segment=True has a whole sentence to expand into,
    # and mute_whole_segment=False does not.
    words = [{"word": " ok", "start": float(i), "end": float(i) + 0.4,
              "probability": 0.95} for i in range(20) if i != 10]
    words.insert(10, {"word": " nigger", "start": 10.0, "end": 10.4,
                      "probability": 0.95})
    segments = [{"id": 0, "start": 0.0, "end": 20.0,
                 "text": "a very long single segment of speech",
                 "words": words}]
    cache_path = censor.words_cache_path(work_dir, basename)
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump({"segments": segments, **censor._source_stamp(source)}, fh)

    def mean_db(path, start, end):
        out = subprocess.run(
            ["ffmpeg", "-v", "info", "-ss", str(start), "-to", str(end),
             "-i", path, "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True).stderr
        found = re.search(r"mean_volume:\s*(-?[\d.]+|-inf) dB", out)
        return float("-inf") if found.group(1) == "-inf" else float(found.group(1))

    first = censor.censor_video(source, work_dir, bleep_method="silence",
                                mute_whole_segment=True)
    second = censor.censor_video(source, work_dir, bleep_method="silence",
                                 mute_whole_segment=False)

    assert first.output_path != second.output_path, (
        "changing mute_whole_segment produced the SAME cached filename - "
        "the exact bug this test exists to catch")
    assert os.path.exists(first.output_path)
    assert os.path.exists(second.output_path)

    # mute_whole_segment=True: the WHOLE 20s segment is silenced, second
    # 2 included, nowhere near the flagged word at second 10.
    wide_check = mean_db(first.output_path, 2.0, 3.0)
    assert wide_check < -60, (
        "mute_whole_segment=True should have silenced the whole segment, "
        "including a second nowhere near the flagged word")

    # mute_whole_segment=False: the SAME second is untouched now - this
    # is the actual bug proof. Before this fix, the second run would have
    # reused run one's cached file (same cache_key either way) and this
    # would still read silent.
    narrow_far = mean_db(second.output_path, 2.0, 3.0)
    assert narrow_far > -60, (
        "mute_whole_segment=False still muted audio a full 7+ seconds "
        "from the flagged word - either word-level muting is not working, "
        "or the stale, whole-segment-muted file from run one got reused")

    # And right around the flagged word, second 10, both runs agree:
    # muted either way, because both settings mute the word itself.
    for output in (first.output_path, second.output_path):
        assert mean_db(output, 9.9, 10.5) < -60


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not installed")
def test_identical_settings_still_reuse_the_cache(tmp_path, capsys):
    """The performance the caching exists for must not be lost while
    fixing its correctness - two runs with the SAME settings should not
    re-transcribe or re-render."""
    source = str(tmp_path / "s.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=5",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", source], check=True)

    work_dir = str(tmp_path / "work")
    os.makedirs(work_dir, exist_ok=True)
    words = [{"word": " shit", "start": 2.0, "end": 2.4, "probability": 0.95}]
    segments = [{"id": 0, "start": 0.0, "end": 5.0, "text": "shit",
                "words": words}]
    with open(censor.words_cache_path(work_dir, "s"), "w", encoding="utf-8") as fh:
        json.dump({"segments": segments, **censor._source_stamp(source)}, fh)

    first = censor.censor_video(source, work_dir, bleep_method="silence",
                                mute_whole_segment=False)
    capsys.readouterr()
    second = censor.censor_video(source, work_dir, bleep_method="silence",
                                 mute_whole_segment=False)
    printed = capsys.readouterr().out

    assert first.output_path == second.output_path
    assert "Reusing existing censored copy" in printed


# ── the log text has to match what actually happened ─────────────────────

def _fake_violation(category="hate_speech", start=10.0):
    from autoreel.compliance import Violation
    return Violation(word="nigger", category=category, start=start,
                     end=start + 0.4)


def test_the_log_says_whole_sentence_only_when_that_is_what_happened(capsys):
    censor._report_risk([_fake_violation()], mute_whole_segment=True)
    printed = capsys.readouterr().out

    assert "whole sentence muted" in printed


def test_the_log_says_word_muted_when_that_is_what_actually_happens(capsys):
    """This is the fix: it used to print "(whole sentence muted)"
    unconditionally, which became false the moment mute_whole_segment
    started defaulting to False - reading a log that claims a whole
    sentence was muted while the audio only dips for a second is exactly
    how "the fix didn't do anything" gets reported."""
    censor._report_risk([_fake_violation()], mute_whole_segment=False)
    printed = capsys.readouterr().out

    assert "word muted" in printed
    assert "whole sentence muted" not in printed


def test_report_risk_defaults_to_the_accurate_word_level_wording(capsys):
    """The default here has to match censor_video's own default
    (mute_whole_segment=False) - if they ever drift apart, the log lies
    again by default."""
    censor._report_risk([_fake_violation()])
    printed = capsys.readouterr().out

    assert "word muted" in printed
