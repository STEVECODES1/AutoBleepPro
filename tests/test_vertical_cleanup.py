"""
The 9:16 re-frames nobody ever deleted.

vertical_path writes "_vertical_<clip>.mp4" into censored/ so Rumble and
Instagram share one encode instead of paying for it twice. Nothing has
ever removed them. Each is a full re-encode of a clip, so a machine that
clips daily grows a folder of them forever - and this project runs off an
external drive, where a full disk stops the RECORDER, not just the
posting.

The reason it is not simply "delete them after posting": the QUEUE stores
the re-framed path, not the original. A clip deferred for X's hourly
spacing is still pointing at that file hours later, and deleting it out
from under a pending post loses the post with "the clip is gone" - which
is the exact class of silent failure this project keeps finding days
late.
"""

import os
import sys
import time

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
for _path in (_REPO, _UPLOADER):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from utils.cleanup import VERTICAL_MIN_AGE_S, prune_vertical_copies


class _Cfg:
    def __init__(self, folder, queue_path):
        class _General:
            censored_folder = str(folder)
        self.general = _General()
        self.posting = {"queue_path": str(queue_path)}


def _old(path, seconds=VERTICAL_MIN_AGE_S + 3600):
    when = time.time() - seconds
    os.utime(path, (when, when))


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "censored").mkdir()
    return tmp_path / "censored"


def test_a_finished_reframe_is_deleted(folder, tmp_path):
    stale = folder / "_vertical_Wifi Cooked - Clip 01.mp4"
    stale.write_bytes(b"x" * 2048)
    _old(str(stale))

    freed = prune_vertical_copies(_Cfg(folder, tmp_path / "jobs.json"))

    assert not stale.exists()
    assert freed > 0


def test_a_reframe_a_queued_post_is_waiting_on_is_kept(folder, tmp_path):
    """THE reason this cannot just delete everything. A clip deferred for
    X's hourly spacing still points at this file."""
    from job_queue import JobQueue

    waiting = folder / "_vertical_Wifi Cooked - Clip 02.mp4"
    waiting.write_bytes(b"x" * 2048)
    _old(str(waiting))

    queue = JobQueue(path=str(tmp_path / "jobs.json"))
    queue.enqueue("zernio_twitter", str(waiting), "caption")

    prune_vertical_copies(_Cfg(folder, tmp_path / "jobs.json"))

    assert waiting.exists(), \
        "it deleted a clip a pending post was still waiting on"


def test_a_recent_reframe_is_kept(folder, tmp_path):
    """A job about to be created must not be raced."""
    fresh = folder / "_vertical_Wifi Cooked - Clip 03.mp4"
    fresh.write_bytes(b"x" * 2048)

    prune_vertical_copies(_Cfg(folder, tmp_path / "jobs.json"))

    assert fresh.exists()


def test_only_reframes_are_touched(folder, tmp_path):
    """censored/ also holds the bleeped copies and the transcript cache."""
    for name in ("Wifi Cooked_CENSORED_beep.mp4", "Wifi Cooked.words.json",
                 "some other video.mp4"):
        made = folder / name
        made.write_bytes(b"x" * 2048)
        _old(str(made))

    prune_vertical_copies(_Cfg(folder, tmp_path / "jobs.json"))

    assert len(list(folder.iterdir())) == 3, "it deleted something else"


def test_an_unreadable_queue_deletes_nothing(folder, tmp_path):
    """Doubt has to read as "every file is spoken for". The other way
    round loses posts."""
    stale = folder / "_vertical_a.mp4"
    stale.write_bytes(b"x" * 2048)
    _old(str(stale))

    broken = tmp_path / "jobs.json"
    broken.write_text("{ not json at all")

    prune_vertical_copies(_Cfg(folder, broken))

    assert stale.exists(), "it deleted files while blind to the queue"


def test_no_censored_folder_is_not_an_error(tmp_path):
    assert prune_vertical_copies(
        _Cfg(tmp_path / "nope", tmp_path / "jobs.json")) == 0.0
