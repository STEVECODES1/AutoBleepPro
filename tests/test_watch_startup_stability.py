"""A four-hour stream uploaded as fifty-four minutes.

The file was still downloading. --watch's startup sweep - videos that
were already in the folder when it started - called the processor
directly, skipping the wait for the file to stop growing that every
ARRIVING file goes through.

Nothing in the log said the video had been truncated, because from the
uploader's side it had not been: fifty-four minutes was genuinely all
there was at the moment it looked.
"""

from __future__ import annotations

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
for _path in (_REPO, _UPLOADER):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from utils.file_watcher import FolderWatcher  # noqa: E402


def _watcher(tmp_path, seconds=1):
    """A RUNNING watcher - consider() hands to the same queue that the
    drain worker empties, exactly as it does in main.py."""
    done = []
    watcher = FolderWatcher(str(tmp_path), (".ts", ".mp4"), seconds,
                            done.append)
    watcher.start()
    return watcher, done


def test_a_file_still_growing_is_not_processed(tmp_path):
    """The exact failure: a 7 GB download in progress."""
    import time

    video = tmp_path / "stream.ts"
    video.write_bytes(b"x" * 1000)
    watcher, done = _watcher(tmp_path, seconds=30)

    watcher.consider(str(video))
    time.sleep(0.3)
    video.write_bytes(b"x" * 5000)     # still downloading
    time.sleep(0.3)

    assert not done, "it processed a file that was still being written"


def test_a_finished_file_is_processed(tmp_path):
    import time

    video = tmp_path / "stream.ts"
    video.write_bytes(b"x" * 1000)
    watcher, done = _watcher(tmp_path, seconds=1)

    watcher.consider(str(video))
    for _ in range(60):
        if done:
            break
        time.sleep(0.1)

    assert done == [str(video)]


def test_the_same_file_is_not_queued_twice(tmp_path):
    import time

    video = tmp_path / "stream.ts"
    video.write_bytes(b"x")
    watcher, done = _watcher(tmp_path, seconds=1)

    watcher.consider(str(video))
    watcher.consider(str(video))
    for _ in range(60):
        if done:
            break
        time.sleep(0.1)

    assert len(done) == 1


def test_a_part_file_is_ignored(tmp_path):
    """Browsers and yt-dlp write .part/.crdownload while downloading."""
    import time

    for name in ("stream.ts.part", "stream.ts.crdownload"):
        video = tmp_path / name
        video.write_bytes(b"x")
        watcher, done = _watcher(tmp_path, seconds=1)
        watcher.consider(str(video))
        time.sleep(0.3)
        assert not done, name


def test_something_that_is_not_a_video_is_ignored(tmp_path):
    import time

    note = tmp_path / "clip_subject.txt"
    note.write_text("monkey")
    watcher, done = _watcher(tmp_path, seconds=1)

    watcher.consider(str(note))
    time.sleep(0.3)

    assert not done


def test_the_startup_sweep_goes_through_the_watcher():
    """Not straight to the processor, which is what skipped the wait."""
    body = open(os.path.join(_UPLOADER, "main.py"), encoding="utf-8").read()

    assert "watcher.consider(os.path.join(watch_folder, name))" in body
    assert "on_ready(os.path.join(watch_folder, name))" not in body
