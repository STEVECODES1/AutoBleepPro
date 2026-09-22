"""
"No videos found" while three recordings sit one folder down.

A drag that lands one level too deep - or a file manager that makes a
"Video" folder on the way - puts the recordings in
watch_folder/Video/, where the batch does not look. What it printed was

    [Batch] No videos found in ...\\watch_folder
            Looked for: .mp4, .mov, .avi, .mkv, .flv, .wmv, .ts

with a 4.2 GB .ts file twelve inches away. That reads as the tool being
broken rather than as a path problem.
"""

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (_REPO, os.path.join(_REPO, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

from main import videos_one_level_down  # noqa: E402

FORMATS = (".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".ts")


def test_the_recordings_one_level_down_are_found(tmp_path):
    hidden = tmp_path / "Video"
    hidden.mkdir()
    (hidden / "Stackswopo - IDK (FULL STREAM).ts").write_bytes(b"x")
    (hidden / "clip 01.mp4").write_bytes(b"x")
    (hidden / "notes.txt").write_bytes(b"x")

    found = videos_one_level_down(str(tmp_path), FORMATS)

    assert len(found) == 1
    folder, names = found[0]
    assert folder == str(hidden)
    assert names == ["Stackswopo - IDK (FULL STREAM).ts", "clip 01.mp4"]


def test_a_half_written_download_is_not_offered(tmp_path):
    """The same guard the batch itself uses - a .part or .ytdl file is
    not a video yet, and naming it would send someone to upload a file
    that is still being written."""
    hidden = tmp_path / "Video"
    hidden.mkdir()
    (hidden / "stream.ts.part").write_bytes(b"x")
    (hidden / "stream.f299.mp4").write_bytes(b"x")
    (hidden / "real.mp4").write_bytes(b"x")

    _, names = videos_one_level_down(str(tmp_path), FORMATS)[0]
    assert "real.mp4" in names
    assert not any(n.endswith(".part") for n in names)


def test_it_only_looks_one_level_down(tmp_path):
    """Walking the whole tree would mean walking uploaded/ and clips/
    too, and offering to re-upload what is already done."""
    deep = tmp_path / "Video" / "deeper"
    deep.mkdir(parents=True)
    (deep / "buried.mp4").write_bytes(b"x")

    assert videos_one_level_down(str(tmp_path), FORMATS) == []


def test_an_empty_tree_offers_nothing(tmp_path):
    (tmp_path / "empty_sub").mkdir()
    assert videos_one_level_down(str(tmp_path), FORMATS) == []
    assert videos_one_level_down(str(tmp_path / "nope"), FORMATS) == []


def test_files_beside_the_folders_are_not_counted(tmp_path):
    """Those are what the batch already looked at and did not find."""
    (tmp_path / "top.mp4").write_bytes(b"x")
    assert videos_one_level_down(str(tmp_path), FORMATS) == []


def test_the_batch_tells_you_what_to_run():
    body = open(os.path.join(_REPO, "auto_uploader", "main.py"),
                encoding="utf-8").read()
    assert "videos_one_level_down" in body
    assert "ARE sitting in" in body
    assert "Move them up into" in body
