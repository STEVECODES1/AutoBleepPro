"""
Filename rules for the auto-uploader.

These decide what gets uploaded unattended, so they're the rules most
likely to cause real damage if they silently regress: a bad title on a
public video, or uploading half a download. No network, no config file.
"""

from __future__ import annotations

import os
import sys

import pytest

_UPLOADER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto_uploader")
sys.path.insert(0, _UPLOADER)

from utils.file_watcher import is_intermediate_download  # noqa: E402
from utils.templating import (  # noqa: E402
    extract_date_from_filename,
    extract_title_from_filename,
    strip_channel_prefix,
    strip_ytdlp_suffix,
)

PREFIXES = ("Stackswopo", "StacksWopo", "StackswopoGames", "BinScripts")


# ── yt-dlp downloads ─────────────────────────────────────────────────────────

def test_ytdlp_name_yields_a_clean_title():
    assert extract_title_from_filename(
        "Stackswopo - LOL  NO -YdH8jO6Vjs.mp4", PREFIXES) == "LOL NO"


def test_ytdlp_bracketed_id_form():
    assert extract_title_from_filename(
        "Some Title [dQw4w9WgXcQ].mp4", PREFIXES) == "Some Title"


def test_only_the_channel_segment_is_stripped():
    # A title that genuinely contains " - " keeps everything after the
    # channel name.
    assert extract_title_from_filename(
        "Stackswopo - Part 1 - the finale-dQw4w9WgXcQ.mp4",
        PREFIXES) == "Part 1 - the finale"


def test_double_spaces_from_youtube_titles_collapse():
    assert "  " not in extract_title_from_filename(
        "Stackswopo - LOL  NO -YdH8jO6Vjs.mp4", PREFIXES)


def test_unknown_channel_prefix_is_left_alone():
    assert extract_title_from_filename(
        "SomeoneElse - My Video-dQw4w9WgXcQ.mp4",
        PREFIXES) == "SomeoneElse - My Video"


def test_strip_ytdlp_suffix_is_a_noop_without_an_id():
    for stem in ("My Awesome Gameplay Stream", "vacation footage", "clip"):
        assert strip_ytdlp_suffix(stem) == stem


def test_strip_ytdlp_suffix_keeps_something():
    # A filename that is *only* an ID must not become empty.
    assert strip_ytdlp_suffix("dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_strip_channel_prefix_needs_a_separator():
    assert strip_channel_prefix("StackswopoVODs", PREFIXES) == "StackswopoVODs"


@pytest.mark.parametrize("name", [
    "My Awesome Gameplay Stream.mp4",
    "vacation footage.mp4",
    "StackswopoVODs_10.ts",
])
def test_ordinary_names_are_not_mistaken_for_ytdlp(name):
    # Falling through to None is correct: better to ask (or use the default)
    # than to silently chop a real word off the end of a title.
    assert extract_title_from_filename(name, PREFIXES) is None


# ── Existing naming conventions must keep working ────────────────────────────

def test_quoted_title_still_wins():
    assert extract_title_from_filename(
        "'do yall forgive me' 8-2-26 Stackswopo Stream.ts", PREFIXES) \
        == "do yall forgive me"


def test_text_before_the_date_still_works():
    assert extract_title_from_filename("!howl 2026-03-19_21_23.mp4", PREFIXES) == "!howl"


def test_date_extraction_is_unaffected():
    assert extract_date_from_filename(
        "'do yall forgive me' 8-2-26 Stackswopo Stream.ts").date().isoformat() \
        == "2026-08-02"


def test_prefixes_default_to_empty():
    # Called without the config list, nothing is stripped but the ID still is.
    assert extract_title_from_filename("Stackswopo - LOL  NO -YdH8jO6Vjs.mp4") \
        == "Stackswopo - LOL NO"


# ── Pre-merge / in-progress downloads ────────────────────────────────────────

@pytest.mark.parametrize("name", [
    "Stackswopo - LOL  NO -YdH8jO6Vjs.f140.mp4",   # audio-only, pre-merge
    "Stackswopo - LOL  NO -YdH8jO6Vjs.f299.mp4",   # video-only, pre-merge
    "clip.f1.mp4",
    "Stream.temp.mp4",
    "Stream.tmp.mkv",
    "Stream.part.mp4",
    "Stream.download.mp4",
    "Stream.ytdl.mp4",
])
def test_pre_merge_fragments_are_rejected(name):
    """yt-dlp downloads each stream in full, THEN muxes.

    So an audio-only .mp4 sits there complete and unchanging for a while -
    it has a real extension and passes a stability check, and would be
    uploaded without this guard.
    """
    assert is_intermediate_download(name) is True


@pytest.mark.parametrize("name", [
    "Stackswopo - LOL  NO -YdH8jO6Vjs.mp4",
    "'do yall forgive me' 8-2-26 Stackswopo Stream.ts",
    "My.Movie.2026.mp4",
    "S01.E04.mkv",
    "finished.mp4",
])
def test_finished_videos_are_accepted(name):
    assert is_intermediate_download(name) is False


def test_intermediate_check_ignores_directories_in_the_path():
    assert is_intermediate_download("/tmp/f140/finished.mp4") is False
    assert is_intermediate_download("/tmp/videos/clip.f140.mp4") is True


# ═════════════════════════════════════════════════════════════════════════════
# Which skips are worth printing
# ═════════════════════════════════════════════════════════════════════════════

def test_code_and_project_files_skip_silently():
    """--batch pointed at a folder that also holds code printed a [SKIP]
    line for every .py and .bat in it, burying the skips that matter."""
    from utils.file_watcher import is_sidecar_file

    for name in ("main.py", "INSTALL.bat", "LICENSE", "README.md",
                 "config.json", ".gitkeep", "Thumbs.db", "notes.txt"):
        assert is_sidecar_file(name), f"{name} should skip silently"


def test_a_plausible_video_is_worth_reporting():
    """These are the ones where the fix might be 'add it to
    supported_formats' rather than 'that is not a video'."""
    from utils.file_watcher import is_sidecar_file, looks_like_a_video_attempt

    for name in ("stream.webm", "clip.m4v", "old.mpg", "cam.mts"):
        assert looks_like_a_video_attempt(name), f"{name} should be reported"
        assert not is_sidecar_file(name)


def test_the_two_helpers_stay_opposites():
    from utils.file_watcher import is_sidecar_file, looks_like_a_video_attempt

    for name in ("a.py", "b.webm", "LICENSE", "c.mpg"):
        assert is_sidecar_file(name) is not looks_like_a_video_attempt(name)


def test_a_mistyped_flag_suggests_the_real_one():
    """argparse's "unrecognized arguments" is accurate and useless."""
    import argparse
    import io
    import contextlib

    from main import _parse_args_helpfully

    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", nargs="?", const="")
    parser.add_argument("--batch", nargs="?", const="")
    parser.add_argument("--file")

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            _parse_args_helpfully(parser, ["--watch_folder"])
        except SystemExit:
            pass
    printed = out.getvalue()
    assert "--watch" in printed and "Did you mean" in printed


def test_a_valid_flag_still_parses():
    import argparse

    from main import _parse_args_helpfully

    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", nargs="?", const="")
    assert _parse_args_helpfully(parser, ["--watch"]).watch == ""


# ═════════════════════════════════════════════════════════════════════════════
# Stream titles: "<name>" <date> Stackswopo Stream
#
# The channel's own convention. It was being broken by the length cap,
# which cut the finished string and so removed the date and the channel
# name - the two parts that are not negotiable - leaving nothing but a
# raw stream name in quotes.
# ═════════════════════════════════════════════════════════════════════════════

TITLE_FORMAT = '"{title}" {date} Stackswopo Stream'


def _title(name, date="8/11/26"):
    import sys
    sys.path.insert(0, _UPLOADER)
    from utils.templating import build_title
    return build_title(name, date, TITLE_FORMAT)


def test_a_short_name_reads_exactly_as_the_channel_titles_them():
    assert _title("shadows", "8/4/26") == '"shadows" 8/4/26 Stackswopo Stream'


def test_a_long_name_loses_the_name_not_the_channel():
    """The regression: this published as a bare quoted stream name with
    the date and "Stackswopo Stream" chopped off the end."""
    long_name = ("stackswopo + gta D10 johnny cox + Lifestyle RP + Windy "
                 "City + Cuffem + Adin Ross")

    out = _title(long_name)

    assert len(out) <= 100
    assert out.endswith(" 8/11/26 Stackswopo Stream")
    assert out.startswith('"stackswopo + gta')


def test_a_long_name_is_cut_at_a_word():
    out = _title("one two three four five six seven eight nine ten eleven "
                 "twelve thirteen fourteen fifteen sixteen seventeen")
    quoted = out.split('"')[1]
    assert quoted.split()[-1] in out
    assert not quoted.endswith(" ")


def test_a_date_the_streamer_left_on_the_name_is_not_said_twice():
    """The recorder names a stream after what the platform called it, and
    that often already ends in a timestamp."""
    out = _title("OMG 2026-08-10 13:56")
    assert out == '"OMG" 8/11/26 Stackswopo Stream'


def test_an_underscore_time_is_stripped_too():
    """yt-dlp writes 06_16 rather than 06:16 - colons are illegal in a
    Windows filename, so that is the shape the recorder produces."""
    out = _title("Stackswopo kick live 2026-08-11 06_16")
    assert out == '"Stackswopo kick live" 8/11/26 Stackswopo Stream'


def test_a_name_that_is_only_a_date_is_left_alone():
    """Stripping everything would leave an empty title, which is worse
    than a redundant one."""
    out = _title("2026-08-11")
    assert '""' not in out


def test_the_cap_is_still_respected_by_an_unusual_format():
    import sys
    sys.path.insert(0, _UPLOADER)
    from utils.templating import build_title

    out = build_title("a" * 200, "8/11/26", "{title} " + "x" * 95)
    assert len(out) <= 100


def test_braces_in_a_stream_name_do_not_crash_the_upload():
    """str.format() reads braces in the DATA as placeholders: a stream
    called "drop the {beat}" raised KeyError and took the upload with
    it."""
    assert _title("drop the {beat} now") == \
        '"drop the {beat} now" 8/11/26 Stackswopo Stream'


def test_an_unknown_token_in_the_format_is_left_alone():
    import sys
    sys.path.insert(0, _UPLOADER)
    from utils.templating import build_title

    out = build_title("shadows", "8/4/26", '"{title}" {date} {mystery}')
    assert out.startswith('"shadows" 8/4/26')


# ═════════════════════════════════════════════════════════════════════════════
# A PLACEHOLDER THAT RESOLVED TO NOTHING TAKES ITS PUNCTUATION WITH IT
#
# Published on the Rumble channel, as a Short:
#
#     'Culture' - - Stackswopo Stream
#
# from a title_format of "{title} - {date} - Stackswopo Stream" and no
# date to put in it. The separators in a format string sit BETWEEN two
# things; with one of them gone they are just noise, and it goes out.
# ═════════════════════════════════════════════════════════════════════════════

def _titled(name, date, fmt):
    sys.path.insert(0, _UPLOADER)
    from utils.templating import build_title

    return build_title(name, date, fmt)


def test_a_missing_date_does_not_publish_a_double_dash():
    assert _titled("'Culture'", "", "{title} - {date} - Stackswopo Stream") \
        == "'Culture' - Stackswopo Stream"


def test_a_date_that_is_there_is_left_exactly_alone():
    assert _titled("'Culture'", "8/17/26",
                   "{title} - {date} - Stackswopo Stream") \
        == "'Culture' - 8/17/26 - Stackswopo Stream"


def test_a_name_that_really_contains_a_double_dash_keeps_it():
    """Tidying only runs when something is actually missing - it must not
    start editing titles that are exactly what was asked for."""
    assert _titled("a--b", "8/17/26", "{title} - {date} - X") \
        == "a--b - 8/17/26 - X"


def test_the_shipped_format_survives_a_missing_date():
    assert _titled("WIFI COOKED", "", '"{title}" {date} Stackswopo Stream') \
        == '"WIFI COOKED" Stackswopo Stream'


def test_tidying_does_not_let_a_title_past_the_cap():
    """The length arithmetic measures the raw fill, so the shortening
    budget cannot be quietly widened by the cleanup."""
    from utils.templating import MAX_TITLE_CHARS

    out = _titled("x" * 300, "", "{title} - {date} - Stackswopo Stream")
    assert len(out) <= MAX_TITLE_CHARS
    assert out.endswith("Stackswopo Stream")
    assert " - - " not in out


def test_a_missing_name_leaves_no_stranded_punctuation_either():
    out = _titled("", "8/17/26", "{title} - {date} - Stackswopo Stream")
    assert out == "8/17/26 - Stackswopo Stream"
