"""A video moved into the watch folder already finished is taken in
seconds, not after the full 10-minute stability wait - and one still being
written still gets the whole wait."""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "auto_uploader"))

from utils import file_watcher as fw  # noqa: E402


def _watcher(tmp_path, ready, stability):
    return fw._NewVideoHandler((".ts", ".mp4"), stability, ready.append)


def test_an_old_finished_file_is_taken_quickly(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fw, "QUICK_STABLE_S", 1)
    path = tmp_path / "exposing.ts"
    path.write_bytes(b"x" * 100)
    old = time.time() - 3600
    os.utime(path, (old, old))
    ready = []
    w = _watcher(tmp_path, ready, 600)
    handler = w
    started = time.time()
    t = threading.Thread(target=handler._wait_for_stable, args=(str(path),))
    t.start()
    t.join(timeout=10)
    assert ready == [str(path)]
    assert time.time() - started < 10


def test_a_fresh_file_still_waits_the_full_window(tmp_path, monkeypatch):
    monkeypatch.setattr(fw, "QUICK_STABLE_S", 1)
    path = tmp_path / "growing.ts"
    path.write_bytes(b"x" * 100)
    ready = []
    w = _watcher(tmp_path, ready, 4)
    handler = w
    started = time.time()
    handler._wait_for_stable(str(path))
    assert ready == [str(path)]
    assert time.time() - started >= 4


def test_finished_long_ago():
    now = time.time()
    assert not fw._finished_long_ago("no-such-file.ts", 600, now)
