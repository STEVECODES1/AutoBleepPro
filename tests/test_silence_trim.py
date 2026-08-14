"""
Cutting dead air without cutting the good parts.

The failure this module exists to avoid is subtle and expensive: a
transcript gap is NOT silence. Whisper drops laughter, shouting and most
non-speech noise, so the stretches with no words in them are frequently
the best moments in the stream. A trim that cuts on the transcript alone
removes exactly what a clipper would keep.
"""

import os
import subprocess
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (_REPO, os.path.join(_REPO, "auto_uploader")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from autoreel.silence_trim import (DEFAULT_MIN_SILENCE_S, MAX_REMOVED_FRACTION,
                                   TrimError, apply_trim, describe,
                                   find_dead_air, have_ffmpeg, keep_ranges,
                                   quiet_enough, removed_seconds,
                                   select_expression, speech_baseline,
                                   trim_args, trim_filter, wordless_gaps)


def _words(*spans):
    return [{"start": a, "end": b} for a, b in spans]


def _talking(*ranges):
    """One word per second across each range."""
    out = []
    for start, end in ranges:
        t = start
        while t < end:
            out.append({"start": t, "end": t + 0.4})
            t += 1
    return out


# ── what counts as dead air ─────────────────────────────────────────────

def test_a_quiet_wordless_gap_is_cut():
    words = _talking((0, 10), (40, 60))
    levels = [-20.0] * 61
    for t in range(10, 40):
        levels[t] = -55.0

    cuts = find_dead_air(words, 60.0, levels)

    assert len(cuts) == 1
    start, end = cuts[0]
    assert 9 < start < 11 and 39 < end < 41


def test_a_LOUD_wordless_gap_is_kept():
    """Laughter, shouting, a controller thrown, game audio. Whisper
    writes none of it down, and it is frequently the best part."""
    words = _talking((0, 10), (40, 60))
    loud_throughout = [-20.0] * 61

    assert find_dead_air(words, 60.0, loud_throughout) == []


def test_a_natural_pause_is_kept():
    """Speech has gaps of half a second to a second and a half. Cutting
    those makes a person sound interrupted by an editor."""
    words = _talking((0, 10), (11, 20))
    levels = [-60.0] * 21

    assert find_dead_air(words, 20.0, levels) == []


def test_the_threshold_is_conservative():
    assert DEFAULT_MIN_SILENCE_S >= 2.0


def test_dead_air_before_the_first_word_is_found():
    """A stream that opens on two minutes of waiting screen has its
    longest gap before anything is said."""
    words = _talking((120, 140))
    levels = [-60.0] * 141
    for t in range(120, 141):
        levels[t] = -20.0

    cuts = find_dead_air(words, 140.0, levels)

    assert cuts and cuts[0][0] < 1.0


def test_dead_air_after_the_last_word_is_found():
    words = _talking((0, 20))
    levels = [-20.0] * 121
    for t in range(20, 121):
        levels[t] = -60.0

    cuts = find_dead_air(words, 120.0, levels)

    assert cuts and cuts[-1][1] > 119.0


def test_quiet_is_relative_to_this_video():
    """A fixed dB threshold cannot work across a stream with game audio
    under it and one recorded in a silent room."""
    talking = _talking((0, 30))
    loud_room = [-10.0] * 30 + [-45.0] * 30
    quiet_room = [-40.0] * 30 + [-75.0] * 30

    assert quiet_enough(loud_room, 31.0, 59.0,
                        baseline=speech_baseline(loud_room, talking))
    assert quiet_enough(quiet_room, 31.0, 59.0,
                        baseline=speech_baseline(quiet_room, talking))


def test_the_baseline_ignores_the_silence_it_is_measuring():
    """Taking the median of the whole file is the obvious version and it
    is wrong: on a video that is half dead air, half the samples ARE the
    dead air, the median lands in the silence, and nothing is ever quiet
    relative to it. The more silence, the less the check works."""
    words = _talking((0, 10))
    levels = [-15.0] * 10 + [-70.0] * 110

    assert speech_baseline(levels, words) == pytest.approx(-15.0)


def test_both_gap_edges_round_inward():
    """A gap runs from the end of one word to the start of the next, so
    the partial window at each edge contains speech. Including either
    made every gap measure as loud and nothing was ever cut."""
    levels = [-20.0] + [-60.0] * 8 + [-20.0]

    assert quiet_enough(levels, 0.4, 9.6, baseline=-20.0)


def test_no_levels_falls_back_to_the_transcript():
    """An unreadable audio track should not disable a feature that was
    explicitly asked for."""
    words = _talking((0, 10), (40, 50))

    assert find_dead_air(words, 50.0, None)


def test_untimed_words_are_ignored_not_fatal():
    words = [{"start": 0, "end": 1}, {"word": "no timings"},
             {"start": "x", "end": "y"}, {"start": 40, "end": 41}]

    assert isinstance(find_dead_air(words, 50.0, None), list)


# ── turning cuts into one ffmpeg pass ───────────────────────────────────

def test_keeps_are_the_inverse_of_cuts():
    keeps = keep_ranges([(10.0, 20.0), (30.0, 35.0)], 60.0)

    assert keeps == [(0.0, 10.0), (20.0, 30.0), (35.0, 60.0)]


def test_video_and_audio_share_one_expression():
    """Two expressions that could disagree is how a file drifts out of
    sync halfway through."""
    keeps = [(0.0, 10.0), (20.0, 30.0)]
    expression = select_expression(keeps)
    chain = trim_filter(keeps)

    assert chain.count(expression) == 2
    assert "select=" in chain and "aselect=" in chain


def test_timestamps_are_rebuilt():
    """Without setpts the player sits still through every cut."""
    chain = trim_filter([(0.0, 5.0)])

    assert "setpts=N/FRAME_RATE/TB" in chain
    assert "asetpts=N/SR/TB" in chain


def test_it_is_one_ffmpeg_invocation():
    """Segment-and-concat is quadratic and loses sync at every join."""
    args = trim_args("/in.mp4", "/out.mp4", [(0.0, 5.0), (10.0, 15.0)])

    assert args.count("-i") == 1
    assert "-filter_complex" in args
    assert "concat" not in " ".join(args)


def test_nothing_to_cut_returns_the_source_untouched():
    assert apply_trim("/in.mp4", "/out.mp4", [], 60.0) == "/in.mp4"


def test_an_implausible_trim_refuses():
    """Removing most of the video almost always means the speech was
    never transcribed, not that the stream was that quiet."""
    huge = [(0.0, 90.0)]

    with pytest.raises(TrimError) as raised:
        apply_trim("/in.mp4", "/out.mp4", huge, 100.0)

    assert "transcribed" in str(raised.value)
    assert MAX_REMOVED_FRACTION < 1.0


def test_describe_is_readable():
    assert "No dead air" in describe([], 60.0)
    assert "Trimming" in describe([(0.0, 30.0)], 120.0)


# ── it actually renders ─────────────────────────────────────────────────

@pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg not installed")
def test_the_trim_renders_and_keeps_audio_in_sync(tmp_path):
    """The whole point. A filter graph that looks right and produces a
    file that drifts is worse than no feature."""
    source = str(tmp_path / "src.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        source], check=True)

    out = str(tmp_path / "trimmed.mp4")
    apply_trim(source, out, [(5.0, 12.0), (20.0, 25.0)], 30.0,
               preset="ultrafast", crf=32)

    def seconds(path, stream):
        return float(subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", f"{stream}:0",
             "-show_entries", "stream=duration", "-of", "csv=p=0", path],
            capture_output=True, text=True).stdout.strip())

    video, audio = seconds(out, "v"), seconds(out, "a")

    assert 17.0 < video < 19.0, f"expected ~18s, got {video:.2f}s"
    assert abs(video - audio) < 0.3, \
        f"video {video:.2f}s vs audio {audio:.2f}s - the file drifts"


# ── wiring ──────────────────────────────────────────────────────────────

def test_the_engine_defaults_to_not_trimming():
    """Opt-in. The export is what gets published."""
    import bleep_engine

    assert bleep_engine.ProcessOptions().trim_silence is False


def test_the_cli_exposes_it():
    import cli

    parser = cli.build_parser() if hasattr(cli, "build_parser") else None
    text = open(os.path.join(_REPO, "cli.py"), encoding="utf-8").read()
    assert "--trim-silence" in text
    assert "min_silence_s=args.min_silence" in text


def test_the_gui_exposes_it_on_both_tabs():
    text = open(os.path.join(_REPO, "autobleep_pro.py"), encoding="utf-8").read()

    assert "self.trim_silence_var" in text, "missing on the Single Video tab"
    assert "self.batch_trim_silence_var" in text, "missing on the Batch tab"
    assert "value=False" in text


def test_censoring_happens_before_trimming():
    """Trimming first would move every word timing the censor pass
    depends on, and the bleeps would land on the wrong words."""
    text = open(os.path.join(_REPO, "bleep_engine.py"), encoding="utf-8").read()

    censor_at = text.index("censored.export(cleaned_path")
    trim_at = text.index("if options.trim_silence:")
    assert censor_at < trim_at


# ── the dual-upload mode ────────────────────────────────────────────────

def test_the_mode_is_off_by_default():
    """No surprise changes to an existing workflow."""
    import json

    with open(os.path.join(_REPO, "auto_uploader", "config.json"),
              encoding="utf-8") as handle:
        config = json.load(handle)

    assert config["mode"] == ""
    assert config["clips"]["trim_silence"] is False


def test_the_mode_splits_censored_and_uncensored():
    """Uncensored full VOD to Rumble, censored copy to YouTube. These are
    two settings a long way apart in config.json, and getting one wrong
    publishes the wrong audio to the wrong platform."""
    import json

    with open(os.path.join(_REPO, "auto_uploader", "config.json"),
              encoding="utf-8") as handle:
        mode = json.load(handle)["modes"]["full_rumble_clean_youtube"]

    assert mode["rumble_censor_uploads"] is False
    assert mode["youtube_censor_uploads"] is True
    assert "FULL" in mode["rumble_title_format"]


def test_applying_a_mode_does_not_rewrite_config():
    """A one-off run must not silently change what every later run does."""
    text = open(os.path.join(_REPO, "auto_uploader", "main.py"),
                encoding="utf-8").read()
    body = text[text.index("def _apply_mode("):text.index("def _clip_config(")]

    assert "json.dump" not in body and "open(" not in body


def test_an_unknown_mode_names_the_ones_that_exist():
    text = open(os.path.join(_REPO, "auto_uploader", "main.py"),
                encoding="utf-8").read()

    assert "Unknown mode" in text and "Known:" in text


def test_the_mode_cannot_bypass_the_publish_guard():
    """Shorts still has to be enabled, signed in and inside its cap."""
    text = open(os.path.join(_REPO, "auto_uploader", "main.py"),
                encoding="utf-8").read()
    body = text[text.index("def _apply_mode("):text.index("def _clip_config(")]

    for forbidden in ("enabled = True", "max_per_day", "reset_failures",
                      "breaker"):
        assert forbidden not in body, \
            f"the mode touches {forbidden!r} - the guard is not its to change"


def test_only_the_youtube_copy_is_trimmed():
    """Rumble takes the untouched source. That IS the split."""
    text = open(os.path.join(_REPO, "auto_uploader", "main.py"),
                encoding="utf-8").read()

    assert "_trim_dead_air(" in text
    trim_at = text.index('if (cfg.clips or {}).get("trim_silence")')
    censor_at = text.index("censor_result = censor_video(")
    assert censor_at < trim_at, "the trim must run on the CENSORED copy"


def test_no_word_timings_means_no_trim():
    """Loudness alone cannot tell a pause from a laugh, so skipping is
    the correct answer rather than guessing."""
    text = open(os.path.join(_REPO, "auto_uploader", "main.py"),
                encoding="utf-8").read()
    body = text[text.index("def _trim_dead_air("):text.index("def _cached_words(")]

    assert "if not words:" in body
    assert "return path" in body
