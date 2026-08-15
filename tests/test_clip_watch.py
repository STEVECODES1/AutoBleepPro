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
    ARCHIVE_NAME, is_settled, load_archive, next_vod, pending, remember)

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
    """Otherwise it is transcribed again on every pass, forever, and
    transcription is the expensive part."""
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
