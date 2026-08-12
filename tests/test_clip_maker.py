"""
Clip rendering: the crop, the caption file, and the ffmpeg command.

The parts worth testing without ffmpeg present are the ones that are
easy to get silently wrong and expensive to notice: a filter string that
crops to the wrong aspect, a Windows path that ffmpeg reports as "file
not found" when the file is right there, and caption timings that are
still relative to the whole VOD instead of the clip.

Rendering itself is exercised against a real ffmpeg only when one is
installed, so the suite still runs everywhere.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from autoreel.captions import (  # noqa: E402
    Phrase,
    build_ass,
    caption_file_for_clip,
    group_words,
    words_in_range,
)
from autoreel.clip_maker import (  # noqa: E402
    VERTICAL_HEIGHT,
    VERTICAL_WIDTH,
    ClipError,
    ClipMaker,
    ClipSpec,
    build_filter,
    clip_filename,
    crop_filter,
    escape_filter_path,
    render_clip,
    specs_from_segments,
)
from autoreel.crop_strategy import CROP_CENTER, CROP_FACE  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None
# ffprobe ships with ffmpeg but is packaged separately often enough that
# assuming one implies the other skips nothing and fails loudly instead.
HAS_FFPROBE = shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
needs_ffprobe = pytest.mark.skipif(
    not (HAS_FFMPEG and HAS_FFPROBE), reason="ffprobe not installed")


def _words(*triples):
    return [{"word": w, "start": s, "end": e} for w, s, e in triples]


# ═════════════════════════════════════════════════════════════════════════════
# The crop filter
# ═════════════════════════════════════════════════════════════════════════════

def test_center_crop_targets_vertical():
    chain = crop_filter(CROP_CENTER)
    assert f"scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}" in chain
    assert "crop=" in chain


def test_crop_is_expressed_relative_to_the_input_size():
    """Hardcoded pixel numbers would be wrong for anything but 1080p."""
    chain = crop_filter(CROP_CENTER)
    assert "iw" in chain and "ih" in chain
    assert "1920:1080" not in chain


def test_crop_cannot_exceed_the_source_dimensions():
    """Cropping to a width larger than the input is an ffmpeg error, so a
    tall source must clamp rather than ask for pixels that don't exist."""
    chain = crop_filter(CROP_CENTER)
    assert "min(iw," in chain and "min(ih," in chain


def test_face_strategy_is_refused_by_the_static_renderer():
    """Face tracking moves the window per frame; silently rendering a
    centre crop instead would look like the tracker simply did nothing."""
    with pytest.raises(ClipError):
        crop_filter(CROP_FACE)


def test_filter_without_captions_has_no_subtitles_stage():
    assert "subtitles" not in build_filter(CROP_CENTER, None)


def test_filter_with_captions_appends_subtitles_last():
    chain = build_filter(CROP_CENTER, "/clips/a.ass")
    assert chain.index("scale=") < chain.index("subtitles="), \
        "captions must be drawn after the scale, or they scale with it"


# ═════════════════════════════════════════════════════════════════════════════
# Path escaping - the classic Windows failure
# ═════════════════════════════════════════════════════════════════════════════

def test_windows_drive_colon_is_escaped():
    """Unescaped, the colon reads as the start of the next filter option
    and ffmpeg reports a missing file for a file that is right there."""
    escaped = escape_filter_path(r"D:\clips\a.ass")
    assert r"\:" in escaped
    assert "\\c" not in escaped, "backslash separators must become forward slashes"


def test_backslashes_become_forward_slashes():
    assert "\\" not in escape_filter_path(r"D:\a\b\c.ass").replace(r"\:", "")


def test_quotes_and_brackets_are_escaped():
    escaped = escape_filter_path("/clips/it's [1].ass")
    assert r"\'" in escaped and r"\[" in escaped


# ═════════════════════════════════════════════════════════════════════════════
# Caption grouping
# ═════════════════════════════════════════════════════════════════════════════

def test_words_are_grouped_into_phrases_not_shown_one_at_a_time():
    phrases = group_words(_words(
        ("what", 0.0, 0.2), ("the", 0.2, 0.4), ("hell", 0.4, 0.7),
        ("was", 0.7, 0.9)))
    assert len(phrases) == 1
    assert phrases[0].text == "what the hell was"


def test_a_pause_starts_a_new_phrase():
    phrases = group_words(_words(("yo", 0.0, 0.3), ("bro", 5.0, 5.3)))
    assert len(phrases) == 2


def test_a_phrase_never_outlives_its_words():
    phrases = group_words(_words(("hey", 1.0, 1.4), ("man", 1.4, 1.8)))
    assert phrases[0].start == 1.0 and phrases[0].end == 1.8


def test_sentence_end_breaks_the_phrase():
    phrases = group_words(_words(("go.", 0.0, 0.3), ("now", 0.4, 0.7)))
    assert len(phrases) == 2


def test_word_count_limit_is_respected():
    phrases = group_words(_words(*[(f"w{i}", i * 0.2, i * 0.2 + 0.15)
                                   for i in range(12)]), max_words=3)
    assert all(len(p.text.split()) <= 3 for p in phrases)


def test_words_with_broken_timings_are_dropped_not_crashed_on():
    phrases = group_words([{"word": "ok", "start": None, "end": 1.0},
                           {"word": "fine", "start": 1.0, "end": 1.4}])
    assert len(phrases) == 1 and phrases[0].text == "fine"


def test_empty_words_produce_no_phrases():
    assert group_words([]) == []


# ═════════════════════════════════════════════════════════════════════════════
# Caption timings are rebased to the clip
# ═════════════════════════════════════════════════════════════════════════════

def _segments():
    return [{"words": _words(
        ("before", 5.0, 5.5),
        ("inside", 32.0, 32.4), ("the", 32.4, 32.6), ("clip", 32.6, 33.0),
        ("after", 90.0, 90.5))}]


def test_only_words_inside_the_window_are_kept():
    words = words_in_range(_segments(), 30.0, 40.0)
    assert [w["word"] for w in words] == ["inside", "the", "clip"]


def test_timings_are_rebased_so_the_clip_starts_at_zero():
    """Left absolute, every caption would render long after the clip ended."""
    words = words_in_range(_segments(), 30.0, 40.0)
    assert words[0]["start"] == pytest.approx(2.0)
    assert words[0]["end"] == pytest.approx(2.4)


def test_a_word_straddling_the_cut_is_clamped_not_negative():
    segments = [{"words": _words(("straddle", 29.5, 30.5))}]
    words = words_in_range(segments, 30.0, 40.0)
    assert words[0]["start"] == 0.0, "a negative start would be dropped"


def test_no_captions_for_a_silent_window(tmp_path):
    path = caption_file_for_clip(str(tmp_path / "c.ass"), _segments(), 50.0, 60.0)
    assert path is None, "an empty caption file makes ffmpeg fail for no reason"


def test_caption_file_is_written_for_a_talky_window(tmp_path):
    path = caption_file_for_clip(str(tmp_path / "c.ass"), _segments(), 30.0, 40.0)
    assert path and os.path.exists(path)
    # The words reached the file. Not as one contiguous string: each is
    # wrapped in its own highlight tags as it is spoken, which is the
    # whole point of the word style.
    written = open(path, encoding="utf-8").read().lower()
    for word in ("inside", "the", "clip"):
        assert word in written


# ═════════════════════════════════════════════════════════════════════════════
# The ASS file itself
# ═════════════════════════════════════════════════════════════════════════════

def test_ass_declares_the_vertical_canvas():
    body = build_ass([Phrase(0.0, 1.0, "hi")])
    assert "PlayResX: 1080" in body and "PlayResY: 1920" in body


def test_ass_timestamps_are_hmmsscc():
    body = build_ass([Phrase(61.5, 62.25, "hi")])
    assert "0:01:01.50" in body and "0:01:02.25" in body


def test_ass_braces_are_escaped():
    """A literal brace opens an override block and would eat the caption."""
    body = build_ass([Phrase(0.0, 1.0, "what {the}")])
    assert r"\{" in body and r"\}" in body


def test_zero_length_phrases_are_dropped():
    body = build_ass([Phrase(1.0, 1.0, "blink")])
    assert "blink" not in body


# ═════════════════════════════════════════════════════════════════════════════
# Choosing windows
# ═════════════════════════════════════════════════════════════════════════════

def _talky_segments():
    return [
        {"start": 0.0, "end": 10.0, "text": "just walking around", "words": []},
        {"start": 10.0, "end": 20.0,
         "text": "OH MY GOD what the hell was that bro holy", "words": []},
        {"start": 20.0, "end": 30.0, "text": "anyway", "words": []},
        {"start": 30.0, "end": 40.0,
         "text": "no way dude that was insane holy crap", "words": []},
    ]


def test_specs_are_numbered_from_one_and_in_time_order():
    specs = specs_from_segments(_talky_segments(), count=2)
    assert [s.index for s in specs] == list(range(1, len(specs) + 1))
    assert specs == sorted(specs, key=lambda s: s.start)


def test_specs_respect_the_requested_count():
    assert len(specs_from_segments(_talky_segments(), count=1)) <= 1


def test_a_transcript_with_nothing_interesting_yields_no_clips():
    flat = [{"start": 0.0, "end": 10.0, "text": "and then i walked", "words": []}]
    assert specs_from_segments(flat, count=3) == []


def test_clip_filenames_are_filesystem_safe():
    name = clip_filename('"DAMN" 8/3/26: stream', ClipSpec(0, 10, index=2))
    assert not any(c in name for c in '"/:*?<>|')
    # The clip number has to survive whatever tidying the name gets:
    # ten files that differ only by an index nobody can see are ten
    # files nobody can tell apart.
    assert "02" in name and name.endswith(".mp4")
    assert "  " not in name, "double space from stripped noise"


# ═════════════════════════════════════════════════════════════════════════════
# Rendering guards
# ═════════════════════════════════════════════════════════════════════════════

def test_a_zero_length_clip_is_refused(tmp_path):
    with pytest.raises(ClipError):
        render_clip("in.mp4", ClipSpec(10.0, 10.0), str(tmp_path / "o.mp4"))


def test_clip_maker_defaults_to_center_for_gameplay(tmp_path):
    assert ClipMaker(output_dir=str(tmp_path)).strategy == CROP_CENTER


def test_clip_maker_refuses_face_strategy_with_a_useful_message(tmp_path):
    maker = ClipMaker(output_dir=str(tmp_path),
                      config={"clips": {"crop_strategy": "face"}})
    with pytest.raises(ClipError) as excinfo:
        maker.make("in.mp4", _talky_segments())
    assert "ClipRenderer" in str(excinfo.value)


def test_no_segments_means_no_clips_and_no_error(tmp_path):
    assert ClipMaker(output_dir=str(tmp_path)).make("in.mp4", []) == []


# ═════════════════════════════════════════════════════════════════════════════
# Against a real ffmpeg
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def source_video(tmp_path):
    """8 seconds of 1280x720 colour bars with a tone."""
    path = str(tmp_path / "source.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=8",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", path],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return path


def _dimensions(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", path],
        capture_output=True, text=True, check=True).stdout.strip()
    return tuple(int(n) for n in out.split("x"))


@needs_ffprobe
def test_rendered_clip_is_actually_vertical(tmp_path, source_video):
    output = str(tmp_path / "out.mp4")
    render_clip(source_video, ClipSpec(1.0, 4.0), output, CROP_CENTER,
                preset="ultrafast")
    assert _dimensions(output) == (VERTICAL_WIDTH, VERTICAL_HEIGHT)


@needs_ffprobe
def test_rendered_clip_has_the_requested_duration(tmp_path, source_video):
    output = str(tmp_path / "out.mp4")
    render_clip(source_video, ClipSpec(2.0, 5.0), output, CROP_CENTER,
                preset="ultrafast")
    duration = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", output],
        capture_output=True, text=True, check=True).stdout.strip())
    assert duration == pytest.approx(3.0, abs=0.4)


@needs_ffprobe
def test_captions_are_burned_in_without_breaking_the_render(tmp_path, source_video):
    caption = caption_file_for_clip(
        str(tmp_path / "c.ass"),
        [{"words": _words(("hello", 1.2, 1.6), ("there", 1.6, 2.0))}],
        1.0, 4.0)
    output = str(tmp_path / "out.mp4")
    render_clip(source_video, ClipSpec(1.0, 4.0), output, CROP_CENTER,
                caption_path=caption, preset="ultrafast")
    assert _dimensions(output) == (VERTICAL_WIDTH, VERTICAL_HEIGHT)


@needs_ffmpeg
def test_a_failed_render_leaves_no_partial_file(tmp_path):
    """A truncated .mp4 sitting there looks exactly like a finished clip."""
    output = str(tmp_path / "out.mp4")
    with pytest.raises(ClipError):
        render_clip(str(tmp_path / "does_not_exist.mp4"), ClipSpec(0.0, 2.0),
                    output, CROP_CENTER)
    assert not os.path.exists(output)
    assert [p for p in os.listdir(tmp_path) if "partial" in p] == []


@needs_ffmpeg
def test_clip_maker_end_to_end(tmp_path, source_video):
    maker = ClipMaker(output_dir=str(tmp_path / "clips"), count=1,
                      min_seconds=2.0, max_seconds=5.0, preset="ultrafast")
    segments = [
        {"start": 0.0, "end": 2.0, "text": "walking", "words": []},
        {"start": 2.0, "end": 6.0,
         "text": "OH MY GOD what the hell holy crap bro", "words": []},
    ]
    results = maker.make(source_video, segments, basename="stream")
    assert results, "a clearly clip-worthy segment produced nothing"
    assert all(os.path.exists(r.path) for r in results)
    assert all(r.strategy == CROP_CENTER for r in results)
    # The .ass files are working files, not deliverables.
    assert not [p for p in os.listdir(tmp_path / "clips") if p.endswith(".ass")]


# ═════════════════════════════════════════════════════════════════════════════
# FIT: the re-frame that keeps the whole picture
#
# A centre crop of 16:9 keeps the middle third of the width. On a two-person
# webcam call that is a tight shot of whoever is standing in the middle,
# which is not a framing anybody chose - it is what was left over.
# ═════════════════════════════════════════════════════════════════════════════

def test_fit_is_a_valid_strategy():
    from autoreel.crop_strategy import CROP_FIT, resolve_crop_strategy

    assert resolve_crop_strategy({"clips": {"crop_strategy": "fit"}}) == CROP_FIT


def test_center_is_still_the_default_for_gameplay():
    """Changing the shipped config must not change the code default."""
    from autoreel.crop_strategy import CROP_CENTER, resolve_crop_strategy

    assert resolve_crop_strategy({}, "gameplay") == CROP_CENTER


def test_fit_crops_nothing_away():
    from autoreel.clip_maker import crop_filter

    chain = crop_filter("fit")
    # The frame is scaled to the canvas WIDTH and centred; the only crop
    # in the chain belongs to the blurred background copy.
    assert "scale=1080:-2" in chain
    assert "overlay=(W-w)/2:(H-h)/2" in chain
    assert "gblur" in chain


def test_fit_is_one_input_and_one_output():
    """Otherwise it needs -filter_complex, and render_clip passes -vf."""
    from autoreel.clip_maker import crop_filter

    chain = crop_filter("fit")
    assert not chain.startswith("[")
    assert not chain.endswith("]")


def test_fit_still_takes_burned_captions_when_asked():
    from autoreel.clip_maker import build_filter

    chain = build_filter("fit", "/tmp/x.ass")
    # The watermark goes on after the captions, so this is no longer the
    # end of the chain - only that the captions are in it.
    assert "subtitles='/tmp/x.ass'" in chain


# ═════════════════════════════════════════════════════════════════════════════
# Content profiles
#
# This channel records two different things that want opposite framing: a
# Monkey app call, where the clip is entirely the call window, and GTA RP,
# where the action is centre-screen. One project-wide crop setting cannot
# serve both, and picking either one wrecks the other.
# ═════════════════════════════════════════════════════════════════════════════

def test_the_monkey_profile_cuts_out_the_call_window():
    from autoreel.crop_strategy import (CROP_REGION, resolve_crop_strategy,
                                        resolve_region)

    config = {"clips": {"profile": "monkey"}}
    assert resolve_crop_strategy(config) == CROP_REGION
    region = resolve_region(config)
    assert 0 < region["width"] < 1 and 0 < region["height"] <= 1


def test_the_gta_profile_follows_motion_and_never_faces():
    """The standing rule has two halves and only one of them moved.

    Gameplay must NEVER get face tracking - GTA is full of NPC faces and
    a detector locks onto whichever is nearest the lens. That half is
    permanent and is asserted below and again in the next test.

    The other half, "gameplay is a locked centre crop", was overridden
    on request: a locked crop keeps the crosshair and the HUD and misses
    the fight that made the clip. Motion is frame-to-frame CHANGE, which
    has no opinion about faces at all, and it is speed-capped and
    deadzoned so it drifts rather than chases."""
    from autoreel.crop_strategy import (CROP_FACE, CROP_MOTION,
                                        resolve_crop_strategy)

    resolved = resolve_crop_strategy({"clips": {"profile": "gta"}})

    assert resolved == CROP_MOTION
    assert resolved != CROP_FACE


def test_gameplay_still_defaults_to_centre_without_a_profile():
    """Only the explicitly chosen gta profile moves. A config that names
    no profile gets the same locked crop it always did."""
    from autoreel.crop_strategy import CROP_CENTER, default_for_content

    assert default_for_content("gameplay") == CROP_CENTER


def test_no_profile_can_turn_face_tracking_on():
    from autoreel.crop_strategy import (CROP_FACE, PROFILES,
                                        resolve_crop_strategy)

    for name in PROFILES:
        assert resolve_crop_strategy({"clips": {"profile": name}}) != CROP_FACE


def test_an_explicit_setting_still_beats_the_profile():
    """A one-off override should not require inventing a profile."""
    from autoreel.crop_strategy import CROP_FIT, resolve_crop_strategy

    assert resolve_crop_strategy(
        {"clips": {"profile": "monkey", "crop_strategy": "fit"}}) == CROP_FIT


def test_a_region_measured_in_config_is_what_gets_cut():
    from autoreel.crop_strategy import resolve_region

    region = resolve_region({"clips": {
        "profile": "monkey",
        "profiles": {"monkey": {"crop_region": {
            "x": 0.25, "y": 0.10, "width": 0.40, "height": 0.80}}}}})

    assert region == {"x": 0.25, "y": 0.10, "width": 0.40, "height": 0.80}


def test_a_rectangle_running_off_the_edge_is_pulled_back():
    """crop past the frame is an ffmpeg error, not a crop that clips."""
    from autoreel.crop_strategy import resolve_region

    region = resolve_region({"clips": {
        "profile": "monkey",
        "profiles": {"monkey": {"crop_region": {
            "x": 0.80, "y": 0.90, "width": 0.90, "height": 0.90}}}}})

    assert region["x"] + region["width"] <= 1.0
    assert region["y"] + region["height"] <= 1.0


def test_nonsense_in_the_region_falls_back_instead_of_crashing():
    from autoreel.crop_strategy import resolve_region

    region = resolve_region({"clips": {
        "profile": "monkey",
        "profiles": {"monkey": {"crop_region": {"x": "left", "width": None}}}}})

    assert all(isinstance(v, float) for v in region.values())


def test_an_unknown_profile_name_does_not_crash_the_run():
    from autoreel.crop_strategy import resolve_crop_strategy

    assert resolve_crop_strategy({"clips": {"profile": "does-not-exist"}})


def test_the_region_filter_crops_before_it_frames():
    """Order matters: crop the call window out, THEN fit it to 9:16.
    The other way round frames the whole desktop and crops the result."""
    from autoreel.clip_maker import crop_filter
    from autoreel.crop_strategy import resolve_region

    chain = crop_filter("region", resolve_region({"clips": {"profile": "monkey"}}))
    assert chain.startswith("crop=iw*")
    assert chain.index("crop=iw*") < chain.index("scale=1080")


def test_the_shipped_config_uses_a_profile_that_exists():
    import json
    with open(os.path.join(_REPO, "auto_uploader", "config.json")) as f:
        clips = json.load(f)["clips"]
    from autoreel.crop_strategy import PROFILES
    assert clips.get("profile") in PROFILES


# ═════════════════════════════════════════════════════════════════════════════
# Word-by-word captions
#
# Captions were switched off here because they were a static white slab
# that read like a subtitle track. The phrase now stays on screen and only
# the colour moves, which is the format short-form settled on.
# ═════════════════════════════════════════════════════════════════════════════

def _spoken(*words, step=0.4, hold=0.35):
    return [{"word": w, "start": i * step, "end": i * step + hold}
            for i, w in enumerate(words)]


def test_each_word_lights_up_as_it_is_said():
    from autoreel.captions import build_ass, group_words

    out = build_ass(group_words(_spoken("bro", "he", "actually", "did")))
    events = [l for l in out.splitlines() if l.startswith("Dialogue:")]

    assert len(events) == 4, "one line per word, not one per phrase"
    for line in events:
        assert line.count("\\c&H0000FFFF&") == 1, "exactly one word highlighted"


def test_the_whole_phrase_stays_on_screen():
    """Only the colour moves - nothing has to be re-read."""
    from autoreel.captions import build_ass, group_words

    out = build_ass(group_words(_spoken("bro", "he", "actually", "did")))
    for line in out.splitlines():
        if line.startswith("Dialogue:"):
            for word in ("BRO", "HE", "ACTUALLY", "DID"):
                assert word in line


def test_captions_do_not_blink_out_between_words():
    """A word's slot runs to the START of the next: the silence after a
    word belongs to it, and ending on its own end flickers every gap."""
    from autoreel.captions import build_ass, group_words

    out = build_ass(group_words(_spoken("one", "two", "three")))
    times = []
    for line in out.splitlines():
        if line.startswith("Dialogue:"):
            _, start, end = line.split(",", 3)[:3]
            times.append((start, end))
    for (_, ends), (starts, _) in zip(times, times[1:]):
        assert ends == starts, "a gap between two caption lines"


def test_the_static_style_is_still_available():
    from autoreel.captions import build_ass, group_words

    out = build_ass(group_words(_spoken("bro", "he", "did")), style="block")
    events = [l for l in out.splitlines() if l.startswith("Dialogue:")]

    assert len(events) == 1
    assert "\\c&H0000FFFF&" not in out


def test_casing_can_be_left_alone():
    from autoreel.captions import build_ass, group_words

    out = build_ass(group_words(_spoken("Bro", "he", "did")), uppercase=False)
    assert "Bro" in out and "BRO" not in out


def test_braces_in_speech_cannot_break_the_subtitle_file():
    """A literal brace opens an ASS override block."""
    from autoreel.captions import build_ass, group_words

    out = build_ass(group_words(_spoken("what{", "the}", "hell")))
    assert "\\{" in out and "\\}" in out


def test_a_phrase_with_no_word_timings_still_renders():
    from autoreel.captions import Phrase, build_ass

    out = build_ass([Phrase(0.0, 2.0, "no timings here")])
    assert "NO TIMINGS HERE" in out
