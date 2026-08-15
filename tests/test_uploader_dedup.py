"""
Duplicate-detection rules for the auto-uploader.

A false positive here is the expensive direction: the upload is silently
cancelled, the log points at somebody else's video, and the file is
recorded as done so it never retries. These tests pin the exact case that
caused that in practice - two streams published on the same date.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

import pytest

_UPLOADER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto_uploader")
sys.path.insert(0, _UPLOADER)

from utils.duplicate_checker import DuplicateChecker  # noqa: E402
from utils.youtube_checker import (  # noqa: E402
    ExistingVideo,
    find_existing_video,
    find_same_date_videos,
)

AUG3 = datetime(2026, 8, 3)
MAR20 = datetime(2026, 3, 20)

LOL_NO = ExistingVideo('"LOL NO" 8/3/26 Stackswopo Stream',
                       "0H5W1E7tBA0", "https://www.youtube.com/watch?v=0H5W1E7tBA0")
# Old manual upload, different title era: zero-padded date, asterisks.
HOWL = ExistingVideo("*!howl* - 03/20/26 - Stackswopo FULL YT Stream",
                     "abc123", "https://www.youtube.com/watch?v=abc123")
CHANNEL = [LOL_NO, HOWL]


# ── The regression: two streams, same date ───────────────────────────────────

def test_second_stream_same_day_is_not_mistaken_for_the_first():
    """The real failure: "DAMN" was skipped because "LOL NO" shared its date.

    The upload never happened, and the log reported LOL NO's URL as if it
    were DAMN's.
    """
    assert find_existing_video(CHANNEL, AUG3, "DAMN") is None


def test_the_matching_stream_is_still_found():
    assert find_existing_video(CHANNEL, AUG3, "LOL NO") is LOL_NO


def test_same_date_videos_are_still_discoverable_for_the_warning():
    same = find_same_date_videos(CHANNEL, AUG3)
    assert same == [LOL_NO]


def test_no_video_on_that_date_at_all():
    assert find_existing_video(CHANNEL, datetime(2026, 1, 1), "ANYTHING") is None
    assert find_same_date_videos(CHANNEL, datetime(2026, 1, 1)) == []


# ── Backfill must keep working across title eras ─────────────────────────────

def test_backfill_matches_the_old_title_style():
    # '!howl' vs '*!howl* - 03/20/26 - ...': punctuation and padding differ.
    assert find_existing_video(CHANNEL, MAR20, "!howl") is HOWL


def test_backfill_is_case_insensitive():
    assert find_existing_video(CHANNEL, MAR20, "HOWL") is HOWL


def test_date_only_lookup_keeps_the_old_behaviour():
    # Callers that pass no title still get a date match.
    assert find_existing_video(CHANNEL, AUG3) is LOL_NO


# ── Guards against loose matching ────────────────────────────────────────────

@pytest.mark.parametrize("title", ["no", "gg", "a", ""])
def test_too_generic_a_title_never_matches(title):
    """"no" appears inside "LOL NO" - matching on it would cancel a real
    upload, so short fragments are refused outright."""
    assert find_existing_video(CHANNEL, AUG3, title) is None


def test_unrelated_title_on_a_busy_date():
    assert find_existing_video(CHANNEL, AUG3, "completely different") is None


def test_first_matching_video_wins_when_several_share_a_title():
    dupe = ExistingVideo('"LOL NO" 8/3/26 Stackswopo Stream (reupload)',
                         "zzz", "https://www.youtube.com/watch?v=zzz")
    assert find_existing_video([LOL_NO, dupe], AUG3, "LOL NO") is LOL_NO


def test_empty_channel():
    assert find_existing_video([], AUG3, "DAMN") is None


# ── forget(): recovering from a wrong record ─────────────────────────────────

@pytest.fixture
def checker(tmp_path):
    c = DuplicateChecker(str(tmp_path / "hashes.json"))
    c.record_platform_result("H", "damn.mp4", "youtube", "https://youtu.be/WRONG",
                             title='"DAMN" 8/3/26 Stackswopo Stream')
    c.record_platform_result("H", "damn.mp4", "rumble", "https://rumble.com/vOK",
                             title='"DAMN" 8/3/26 Stackswopo Stream')
    return c


def test_forget_one_platform_leaves_the_other(checker):
    assert checker.is_fully_uploaded("H") is True
    assert checker.forget("H", "youtube") is True
    assert checker.is_fully_uploaded("H") is False
    assert checker.get_platform_result("H", "youtube") is None
    assert checker.get_platform_result("H", "rumble") == "https://rumble.com/vOK"


def test_forget_persists_to_disk(checker):
    checker.forget("H", "youtube")
    reloaded = DuplicateChecker(checker.store_path)
    assert reloaded.get_platform_result("H", "youtube") is None
    assert reloaded.get_platform_result("H", "rumble") == "https://rumble.com/vOK"


def test_forget_everything(checker):
    assert checker.forget("H") is True
    assert checker.get_platform_result("H", "rumble") is None


def test_forget_unknown_hash_is_a_no_op(checker):
    assert checker.forget("NOPE") is False


def test_forget_unknown_platform_is_a_no_op(checker):
    assert checker.forget("H", "twitch") is False
    assert checker.is_fully_uploaded("H") is True


# ── The "no link" marker must not trigger a re-upload ────────────────────────

def test_upload_without_a_link_still_counts_as_done(tmp_path):
    from utils.rumble_uploader import UPLOADED_NO_URL
    c = DuplicateChecker(str(tmp_path / "h.json"))
    c.record_platform_result("H2", "x.mp4", "youtube", "https://youtu.be/ok")
    c.record_platform_result("H2", "x.mp4", "rumble", UPLOADED_NO_URL)
    assert c.is_fully_uploaded("H2") is True, \
        "the video IS on Rumble; re-uploading would create a duplicate"


def test_a_real_failure_does_not_count_as_done(tmp_path):
    c = DuplicateChecker(str(tmp_path / "h.json"))
    c.record_platform_result("H3", "x.mp4", "youtube", "https://youtu.be/ok")
    c.record_platform_result("H3", "x.mp4", "rumble", "FAILED: timeout")
    assert c.is_fully_uploaded("H3") is False


def test_a_placeholder_title_is_not_used_to_match():
    """A stream whose real title could not be read gets the configured
    default, and EVERY such stream gets the same one - so the second one
    matches the first in local history and is skipped as already
    uploaded. That is how a stream reached YouTube and never reached
    Rumble: Rumble has no feed, so the title is all its dedup has."""
    import main

    assert main.is_placeholder_title("Gaming Stream", "Gaming Stream")
    assert main.is_placeholder_title("  gaming   stream ", "Gaming Stream")
    assert main.is_placeholder_title("", "Gaming Stream")


def test_a_real_title_still_matches():
    """The title check is what catches the same stream arriving as a
    re-encoded file, and that has to keep working."""
    import main

    assert not main.is_placeholder_title(
        "Copyrighting All Yall Plug Channels", "Gaming Stream")
    assert not main.is_placeholder_title("monkey n gamble howl", "Gaming Stream")


def test_no_default_configured_means_no_placeholder():
    import main

    assert not main.is_placeholder_title("anything", "")


def test_an_unconfirmed_rumble_upload_is_recorded_as_a_failure():
    """Rumble sometimes finishes without showing a link, and this used to
    be recorded as a success on the assumption the video had landed. It
    had not: a full VOD was marked uploaded, the dedup store believed it,
    every retry was skipped, and the stream simply never appeared.

    A duplicate can be deleted in ten seconds. A stream that silently
    never published is gone until someone notices weeks later."""
    import main

    assert main.RUMBLE_UNCONFIRMED.startswith("FAILED:"), \
        "dedup only skips on a non-FAILED result - this has to read as a failure"
    assert "retry" in main.RUMBLE_UNCONFIRMED.lower()


def test_a_confirmed_url_passes_straight_through():
    import main

    class Cfg:
        class rumble:
            rss_url = "https://rumble.com/user/BinScripts/index.xml"

    real = "https://rumble.com/v7abc12-a-real-video.html"
    assert main._confirm_on_rumble(real, "any title", Cfg) == real


def test_verification_finds_the_video_by_its_slug(monkeypatch):
    """Rumble builds the slug from the title, and nothing else survives
    the round trip reliably."""
    import sys

    from utils import channel_vods

    page = ('<a href="/v7abc12-copyrighting-all-yall-plug-channels-081326.html">x</a>'
            '<a href="/v7zzz99-some-other-stream.html">y</a>')
    monkeypatch.setattr(channel_vods, "_fetch_html", lambda url: (page, ""))

    found = channel_vods.find_on_channel(
        "https://rumble.com/user/BinScripts",
        '"copyrighting all yall plug channels" 08.13.26 Stackswopo Stream')

    assert found.endswith("copyrighting-all-yall-plug-channels-081326.html")


def test_a_generic_title_is_not_matched(monkeypatch):
    """Claiming a match on a title with nothing distinctive in it would
    mark a stream published because a DIFFERENT one was."""
    from utils import channel_vods

    page = '<a href="/v7abc12-some-stream.html">x</a>'
    monkeypatch.setattr(channel_vods, "_fetch_html", lambda url: (page, ""))

    assert channel_vods.find_on_channel(
        "https://rumble.com/user/BinScripts", "Full Live Stream") == ""


def test_a_missing_video_is_reported_as_missing(monkeypatch):
    from utils import channel_vods

    monkeypatch.setattr(channel_vods, "_fetch_html",
                        lambda url: ('<a href="/v7zzz99-unrelated.html">y</a>', ""))

    assert channel_vods.find_on_channel(
        "https://rumble.com/user/BinScripts",
        '"copyrighting all yall plug channels" Stackswopo Stream') == ""


# --- finding a video by its title, not its path ------------------------
# The tool moves a stream from watch_folder to uploaded/ by itself, so
# --forget "some title" used to fail with "File not found" on a video
# sitting right there.

class _Gen:
    def __init__(self, watch, uploaded):
        self.watch_folder = str(watch)
        self.uploaded_folder = str(uploaded)
        self.censored_folder = ""
        self.supported_formats = [".ts", ".mp4"]


class _Cfg:
    def __init__(self, watch, uploaded):
        self.general = _Gen(watch, uploaded)


@pytest.fixture
def library(tmp_path):
    watch = tmp_path / "watch"
    uploaded = tmp_path / "uploaded"
    watch.mkdir()
    uploaded.mkdir()
    for name in (
        "Stackswopo 'copyrighting all yall plug channels' 08.13.26 Full Live Stream.ts",
        "Stackswopo 'Gaming Stream' 08.14.26 Full Live Stream.ts",
        "Stackswopo 'reaction' 08.11.26 Full Live Stream.ts",
    ):
        (uploaded / name).write_bytes(b"x")
    (watch / "notes.txt").write_text("not a video")
    return _Cfg(watch, uploaded)


def test_a_title_finds_the_video_it_names(library):
    from main import _find_video

    found = _find_video(library, "copyrighting all yall plug channels")
    assert len(found) == 1
    assert "copyrighting" in found[0]


def test_a_real_path_is_returned_as_given(library, tmp_path):
    from main import _find_video

    path = os.path.join(library.general.uploaded_folder,
                        "Stackswopo 'reaction' 08.11.26 Full Live Stream.ts")
    assert _find_video(library, path) == [path]


def test_a_title_matching_nothing_finds_nothing(library):
    from main import _find_video

    assert _find_video(library, "minecraft speedrun") == []


def test_words_shared_by_every_recording_match_everything(library):
    """So the caller can refuse rather than forget the wrong stream."""
    from main import _find_video

    assert len(_find_video(library, "Full Live Stream")) == 3


def test_non_video_files_are_never_matched(library):
    from main import _find_video

    assert _find_video(library, "notes") == []


def test_short_words_are_not_required(library):
    """'all' and 'yall' are noise; the distinctive words carry the match."""
    from main import _find_video

    assert len(_find_video(library, "copyrighting plug channels")) == 1


# --- a command that says how but not what ------------------------------

class _Args:
    def __init__(self, **kw):
        self.only = self.mode = self.title = None
        self.keep_source = self.trim_silence = False
        for key, value in kw.items():
            setattr(self, key, value)


class _Parser:
    def __init__(self):
        self.helped = False

    def print_help(self):
        self.helped = True


def test_only_without_a_target_names_the_missing_word(capsys):
    from main import _no_target

    parser = _Parser()
    assert _no_target(parser, _Args(only="rumble")) == 1
    out = capsys.readouterr().out
    assert "--only says how to upload, not what" in out
    assert "--file" in out and "--batch" in out and "--watch" in out
    assert not parser.helped, "sixty lines of help is what we replaced"


def test_the_suggested_commands_keep_the_platform(capsys):
    from main import _no_target

    _no_target(_Parser(), _Args(only="rumble"))
    out = capsys.readouterr().out
    assert "--batch --only rumble" in out


def test_no_arguments_at_all_still_prints_the_help(capsys):
    from main import _no_target

    parser = _Parser()
    assert _no_target(parser, _Args()) == 0
    assert parser.helped


def test_every_modifier_given_is_listed(capsys):
    from main import _no_target

    _no_target(_Parser(), _Args(only="youtube", mode="full_rumble_clean_youtube",
                                keep_source=True))
    out = capsys.readouterr().out
    for flag in ("--only", "--mode", "--keep-source"):
        assert flag in out


def test_the_same_video_in_two_folders_is_not_ambiguous(library):
    """watch_folder and uploaded/ hold the same file - this tool put it
    in both. Upload history is keyed on the filename, so either copy
    clears the same record and there is nothing for the user to decide."""
    from main import _find_video

    name = "Stackswopo 'copyrighting all yall plug channels' 08.13.26 Full Live Stream.ts"
    watch = os.path.join(library.general.watch_folder, name)
    with open(watch, "wb") as handle:
        handle.write(b"x")

    found = _find_video(library, "copyrighting all yall plug channels")
    assert len(found) == 2
    assert len({os.path.basename(p) for p in found}) == 1


def test_two_genuinely_different_videos_stay_ambiguous(library):
    from main import _find_video

    other = os.path.join(
        library.general.watch_folder,
        "Stackswopo 'copyrighting plug channels again' 08.15.26 Full Live Stream.ts")
    with open(other, "wb") as handle:
        handle.write(b"x")

    found = _find_video(library, "copyrighting plug channels")
    assert len({os.path.basename(p) for p in found}) == 2
