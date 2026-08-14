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
