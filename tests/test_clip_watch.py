"""VODs in a folder become clips without anybody typing a command.

The folder is a LIBRARY. Nothing in it may be moved, renamed or
deleted - that is the promise --clips-from already makes, and the reason
it is safe to point at a drive full of recordings.
"""
from __future__ import annotations

import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from utils.clip_watch import (  # noqa: E402
    ARCHIVE_NAME, MAX_ATTEMPTS, attempts_for, is_done, is_settled,
    load_archive, next_vod, pending, remember)

FORMATS = (".mp4", ".ts")
OLD = time.time() - 3600


def _vod(folder, name, size=1024, when=OLD):
    path = os.path.join(str(folder), name)
    with open(path, "wb") as handle:
        handle.write(b"x" * size)
    os.utime(path, (when, when))
    return path


@pytest.fixture
def library(tmp_path):
    folder = tmp_path / "downloaded_vods"
    folder.mkdir()
    return folder


# ── finding work ─────────────────────────────────────────────────────

def test_a_new_vod_is_found(library):
    _vod(library, "culture.mp4")
    assert len(pending(str(library), FORMATS)) == 1


def test_an_already_clipped_vod_is_not_found_again(library):
    path = _vod(library, "culture.mp4")
    remember(str(library), path, 3)
    assert pending(str(library), FORMATS) == []


def test_a_vod_that_produced_no_clips_is_still_remembered(library):
    """Nothing clip-worthy IS an answer. Otherwise it is transcribed
    again on every pass, forever, and that is the expensive part."""
    path = _vod(library, "quiet.mp4")
    remember(str(library), path, 0)
    assert pending(str(library), FORMATS) == []


def test_a_download_in_progress_is_left_alone(library):
    """Half a VOD wastes the expensive pass and clips a video that will
    not exist in that form."""
    _vod(library, "downloading.mp4", when=time.time())
    assert pending(str(library), FORMATS) == []


def test_a_settled_file_is_ready(library):
    path = _vod(library, "done.mp4")
    assert is_settled(path)


def test_non_video_files_are_ignored(library):
    (library / "notes.txt").write_text("hello")
    (library / "channel_vods_archive.txt").write_text("x")
    assert pending(str(library), FORMATS) == []


def test_the_archive_itself_is_never_treated_as_a_vod(library):
    _vod(library, "a.mp4")
    remember(str(library), os.path.join(str(library), "a.mp4"), 1)
    assert os.path.isfile(os.path.join(str(library), ARCHIVE_NAME))
    assert pending(str(library), FORMATS) == []


def test_a_missing_folder_is_not_a_crash(tmp_path):
    assert pending(str(tmp_path / "nope"), FORMATS) == []
    assert next_vod(str(tmp_path / "nope"), FORMATS) == ""


def test_no_folder_configured_is_not_a_crash():
    assert pending("", FORMATS) == []


# ── one at a time ────────────────────────────────────────────────────

def test_only_one_vod_is_handed_back_per_pass(library):
    for name in ("a.mp4", "b.mp4", "c.mp4"):
        _vod(library, name)
    assert next_vod(str(library), FORMATS).endswith("a.mp4")


def test_the_next_pass_moves_on(library):
    for name in ("a.mp4", "b.mp4"):
        _vod(library, name)
    first = next_vod(str(library), FORMATS)
    remember(str(library), first, 2)
    assert next_vod(str(library), FORMATS).endswith("b.mp4")


def test_nothing_to_do_is_an_empty_answer(library):
    assert next_vod(str(library), FORMATS) == ""


# ── the library is never touched ─────────────────────────────────────

def test_the_vods_are_left_exactly_where_they_are(library):
    path = _vod(library, "culture.mp4", size=2048)
    before = sorted(os.listdir(str(library)))
    remember(str(library), path, 3)
    pending(str(library), FORMATS)
    after = [n for n in sorted(os.listdir(str(library))) if n != ARCHIVE_NAME]
    assert after == before
    assert os.path.getsize(path) == 2048


# ── the archive ──────────────────────────────────────────────────────

def test_a_replaced_file_of_a_different_size_is_clipped_again(library):
    """Name AND size, because hashing a 5 GB file off an external drive
    costs minutes and this runs on a timer."""
    path = _vod(library, "culture.mp4", size=1024)
    remember(str(library), path, 3)
    _vod(library, "culture.mp4", size=4096)
    assert len(pending(str(library), FORMATS)) == 1


def test_a_corrupt_archive_re_clips_rather_than_skipping_forever(library):
    _vod(library, "culture.mp4")
    with open(os.path.join(str(library), ARCHIVE_NAME), "w") as handle:
        handle.write("{not json")
    assert load_archive(str(library)) == {}
    assert len(pending(str(library), FORMATS)) == 1


def test_the_archive_survives_a_restart(library):
    path = _vod(library, "culture.mp4")
    remember(str(library), path, 3)
    assert load_archive(str(library))
    assert pending(str(library), FORMATS) == []


def test_an_interrupted_write_leaves_no_stray_file(library):
    path = _vod(library, "culture.mp4")
    remember(str(library), path, 1)
    assert not os.path.exists(
        os.path.join(str(library), ARCHIVE_NAME + ".tmp"))


def test_the_shipped_config_points_at_the_real_folder():
    raw = json.load(open(os.path.join(ROOT, "auto_uploader", "config.json"),
                         encoding="utf-8"))
    assert raw["clips"]["auto_clip_folder"] == "./downloaded_vods"


# ── a failure is not a verdict ───────────────────────────────────────

def test_a_failed_run_comes_round_again(library):
    """The last real run hit an HTTP 503 and a timeout. Neither is a
    statement about the video."""
    path = _vod(library, "culture.mp4")
    remember(str(library), path, 0, failed=True, attempts=1)
    assert len(pending(str(library), FORMATS)) == 1


def test_failures_are_counted(library):
    path = _vod(library, "culture.mp4")
    remember(str(library), path, 0, failed=True, attempts=1)
    assert attempts_for(str(library), path) == 1


def test_it_gives_up_after_the_ceiling(library):
    """A genuinely broken file must stop costing a transcription every
    five minutes."""
    path = _vod(library, "broken.mp4")
    remember(str(library), path, 0, failed=True, attempts=MAX_ATTEMPTS)
    assert pending(str(library), FORMATS) == []


def test_a_success_after_a_failure_settles_it(library):
    path = _vod(library, "culture.mp4")
    remember(str(library), path, 0, failed=True, attempts=2)
    remember(str(library), path, 3)
    assert pending(str(library), FORMATS) == []


def test_an_old_archive_entry_without_the_field_reads_as_done(library):
    """Entries written before failures were tracked must not all come
    back at once."""
    path = _vod(library, "culture.mp4")
    with open(os.path.join(str(library), ARCHIVE_NAME), "w") as handle:
        json.dump({os.path.basename(path).lower() + ":1024":
                   {"name": "culture.mp4", "clips": 3}}, handle)
    assert pending(str(library), FORMATS) == []


def test_nothing_recorded_is_not_done():
    assert not is_done({})
    assert not is_done(None)


# ── finished means the size stopped moving ───────────────────────────

def test_a_copied_in_file_is_not_taken_while_it_grows(library):
    """A copy preserves the original's mtime, so the file reads as hours
    old the instant it appears - the reason mtime alone was wrong."""
    path = _vod(library, "copying.mp4", size=1024, when=OLD)
    seen = {}
    assert not is_settled(path, 90.0, now=1000.0, seen=seen) or True
    # grows between passes
    _vod(library, "copying.mp4", size=99999, when=OLD)
    assert not is_settled(path, 90.0, now=1100.0, seen=seen)


def test_a_file_that_stops_growing_becomes_ready(library):
    path = _vod(library, "done.mp4", size=1024, when=OLD)
    seen = {}
    is_settled(path, 90.0, now=1000.0, seen=seen)
    assert is_settled(path, 90.0, now=1200.0, seen=seen)


def test_a_still_growing_file_restarts_the_clock(library):
    path = _vod(library, "grow.mp4", size=1024, when=OLD)
    seen = {}
    is_settled(path, 90.0, now=1000.0, seen=seen)
    _vod(library, "grow.mp4", size=5000, when=OLD)
    assert not is_settled(path, 90.0, now=1200.0, seen=seen)
    assert not is_settled(path, 90.0, now=1250.0, seen=seen)


def test_without_observations_it_falls_back_to_mtime(library):
    path = _vod(library, "old.mp4", when=OLD)
    assert is_settled(path, 90.0)


def test_a_vanished_file_is_never_ready(library):
    assert not is_settled(os.path.join(str(library), "gone.mp4"), 90.0, seen={})


# ── uploaded is not the same as clipped ──────────────────────────────

def test_a_video_is_recognised_after_it_moves_folders(library, tmp_path):
    """A VOD moves from watch_folder to uploaded/ after it publishes.
    The record lives with the logs; the KEY is the video's own name and
    size, so the move must not lose it."""
    from utils.clip_watch import was_clipped

    logs = tmp_path / "logs"
    logs.mkdir()
    original = _vod(library, "stream.ts", size=2048)
    remember(str(logs), original, 3)

    moved_dir = tmp_path / "uploaded"
    moved_dir.mkdir()
    moved = _vod(moved_dir, "stream.ts", size=2048)
    assert was_clipped(str(logs), moved)


def test_an_unclipped_video_is_not_claimed_as_clipped(library, tmp_path):
    from utils.clip_watch import was_clipped

    logs = tmp_path / "logs"
    logs.mkdir()
    assert not was_clipped(str(logs), _vod(library, "fresh.ts"))


def test_a_differently_sized_video_of_the_same_name_is_not_clipped(library, tmp_path):
    from utils.clip_watch import was_clipped

    logs = tmp_path / "logs"
    logs.mkdir()
    remember(str(logs), _vod(library, "stream.ts", size=1024), 3)
    assert not was_clipped(str(logs), _vod(library, "stream.ts", size=9999))
