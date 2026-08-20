"""Tidying up old logs must never be able to stop a recording.

Four recorders run at once - youtube, twitch, kick and a second channel -
and every one of them prunes the same folder. The mtime was read inside a
sort key, so a file deleted by one of them between another's listdir and
its getmtime raised where nothing caught it:

    logs.sort(key=lambda path: os.path.getmtime(path), reverse=True)
    FileNotFoundError: [WinError 2] The system cannot find the file
    specified: '...\\Stackswopo youtube live 2026-08-17 16_31.log'

The traceback took the whole recorder down before it had watched
anything, so a live stream went by with nothing running.
"""

from __future__ import annotations

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (_REPO, os.path.join(_REPO, "tools")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import record_stream as rec  # noqa: E402
from record_stream import prune_logs  # noqa: E402


def _logs(folder, count):
    import time

    made = []
    for index in range(count):
        path = folder / f"stream {index:02d}.log"
        path.write_text("x")
        os.utime(path, (time.time() - index * 60, time.time() - index * 60))
        made.append(path)
    return made


def test_a_log_deleted_mid_prune_does_not_crash(tmp_path, monkeypatch):
    """The exact race: another recorder removes one between the listing
    and the mtime read."""
    made = _logs(tmp_path, 10)
    real = os.path.getmtime
    vanished = made[3]

    def flaky(path):
        if os.path.basename(str(path)) == vanished.name:
            vanished.unlink(missing_ok=True)
            raise FileNotFoundError(2, "The system cannot find the file", path)
        return real(path)

    monkeypatch.setattr(rec.os.path, "getmtime", flaky)

    prune_logs(str(tmp_path), keep=2)   # must not raise


def test_the_ones_that_are_left_are_still_pruned(tmp_path):
    _logs(tmp_path, 10)

    removed = prune_logs(str(tmp_path), keep=3)

    assert removed == 7
    assert len(list(tmp_path.glob("*.log"))) == 3


def test_the_newest_are_the_ones_kept(tmp_path):
    _logs(tmp_path, 5)

    prune_logs(str(tmp_path), keep=2)

    left = sorted(p.name for p in tmp_path.glob("*.log"))
    assert left == ["stream 00.log", "stream 01.log"]


def test_a_log_that_cannot_be_deleted_is_skipped(tmp_path, monkeypatch):
    """Windows holds an open file. That is one log kept, not a crash."""
    _logs(tmp_path, 6)

    def refuse(path):
        raise PermissionError(13, "in use", path)

    monkeypatch.setattr(rec.os, "remove", refuse)

    assert prune_logs(str(tmp_path), keep=2) == 0


def test_a_missing_folder_is_not_a_crash(tmp_path):
    assert prune_logs(str(tmp_path / "gone")) == 0


def test_nothing_to_prune_is_not_a_crash(tmp_path):
    assert prune_logs(str(tmp_path), keep=5) == 0


def test_only_logs_are_touched(tmp_path):
    _logs(tmp_path, 5)
    keeper = tmp_path / "Stackswopo youtube live.part01.ts"
    keeper.write_text("a recording, not a log")

    prune_logs(str(tmp_path), keep=1)

    assert keeper.exists(), "it deleted a recording"
