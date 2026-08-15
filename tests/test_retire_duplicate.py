"""An already-uploaded video must not sit in the watch folder forever.

    [Check] Reading StackswopoVODs.ts (2.7 GB) to see if it has been
            uploaded before - this takes a minute on a big file...
    [SKIP] StackswopoVODs.ts already uploaded to both platforms

Every run. Same file, same 2.7 GB re-hashed, same answer, and the folder
never emptied.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from main import _retire_duplicate  # noqa: E402


class _General:
    def __init__(self, watch, uploaded, action):
        self.watch_folder = str(watch)
        self.uploaded_folder = str(uploaded)
        self.cleanup = {"source_video": action}


class _Cfg:
    def __init__(self, watch, uploaded, action="move"):
        self.general = _General(watch, uploaded, action)


@pytest.fixture
def folders(tmp_path):
    watch = tmp_path / "watch_folder"
    uploaded = tmp_path / "uploaded"
    watch.mkdir()
    uploaded.mkdir()
    return watch, uploaded


def _video(folder, name="stream.ts", data=b"x" * 32):
    path = folder / name
    path.write_bytes(data)
    return str(path)


def test_it_is_moved_out_of_the_watch_folder(folders):
    watch, uploaded = folders
    video = _video(watch)
    _retire_duplicate(_Cfg(watch, uploaded), video)
    assert not os.path.exists(video)
    assert os.path.isfile(os.path.join(str(uploaded), "stream.ts"))


def test_move_is_the_default_because_it_is_reversible(folders):
    """A local copy is the only thing that can re-cut a clip later."""
    watch, uploaded = folders
    video = _video(watch)
    _retire_duplicate(_Cfg(watch, uploaded, action="something odd"), video)
    assert os.path.isfile(os.path.join(str(uploaded), "stream.ts"))


def test_delete_is_honoured_when_asked_for(folders):
    watch, uploaded = folders
    video = _video(watch)
    _retire_duplicate(_Cfg(watch, uploaded, action="delete"), video)
    assert not os.path.exists(video)
    assert os.listdir(str(uploaded)) == []


def test_keep_leaves_it_exactly_where_it_is(folders):
    watch, uploaded = folders
    video = _video(watch)
    _retire_duplicate(_Cfg(watch, uploaded, action="keep"), video)
    assert os.path.isfile(video)


def test_a_video_outside_the_watch_folder_is_not_touched(folders, tmp_path):
    """--batch can be pointed at a library. Moving files out of a folder
    the user named is not this function's business."""
    watch, uploaded = folders
    library = tmp_path / "my_vods"
    library.mkdir()
    video = _video(library)
    _retire_duplicate(_Cfg(watch, uploaded), video)
    assert os.path.isfile(video)


def test_a_copy_already_filed_is_dropped_not_duplicated(folders):
    """Two copies of a published VOD is the thing being cleaned up."""
    watch, uploaded = folders
    _video(uploaded, data=b"already here")
    video = _video(watch)
    _retire_duplicate(_Cfg(watch, uploaded), video)
    assert not os.path.exists(video)
    assert open(os.path.join(str(uploaded), "stream.ts"), "rb").read() == b"already here"


def test_a_missing_file_is_not_a_crash(folders):
    watch, uploaded = folders
    _retire_duplicate(_Cfg(watch, uploaded), str(watch / "gone.ts"))


def test_an_unwritable_destination_does_not_lose_the_video(folders, monkeypatch):
    """Failing to tidy must never cost the file."""
    watch, uploaded = folders
    video = _video(watch)
    monkeypatch.setattr("shutil.move",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("denied")))
    _retire_duplicate(_Cfg(watch, uploaded), video)
    assert os.path.isfile(video)
