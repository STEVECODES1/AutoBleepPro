"""
A network blip outlived process_file()'s own retries and nothing ever
asked again.

'halfa mill' lost its YouTube upload to a dropped connection at 3 AM.
process_file() retried it 60s/300s/900s, gave up, and left the .ts in
place with a correct, honest message: "left in place (not every platform
succeeded yet) - rerun --file or --batch on it later". Nothing in --watch
mode ever reran it - the watcher only reacts to a file ARRIVING, and the
startup sweep that offers whatever is already there only runs once, at
startup. So the file sat there, correctly diagnosed and permanently
un-retried, until someone came back, read the log and ran --batch by
hand - the exact babysitting this project exists to not need.

Two pieces:

  1. FolderWatcher.consider() is now safe to call on the same path
     repeatedly, including while that path is mid-upload - it is a
     no-op for anything already queued or already running (is_active()).
     Without this, a periodic sweep calling consider() on an eight-hour
     upload every 30 minutes would queue one more redundant pass behind
     it every time, for as long as the real attempt took.

  2. --watch's own loop periodically re-offers every file still in the
     watch folder to the same watcher, the same way the one-time startup
     sweep does. A file that already succeeded is gone (retire_source()
     moved or deleted it) or is skipped for free; a file still missing a
     platform gets exactly the retry a fresh connection needed.
"""

from __future__ import annotations

import os
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
for _path in (_REPO, _UPLOADER):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from utils.file_watcher import FolderWatcher  # noqa: E402


def _watcher(tmp_path, seconds=1, on_ready=None):
    calls = []

    def default(path):
        calls.append(path)

    watcher = FolderWatcher(str(tmp_path), (".ts", ".mp4"), seconds,
                            on_ready or default)
    watcher.start()
    return watcher, calls


# ── is_active() / consider() no-op while queued or running ─────────────

def test_a_file_not_yet_offered_is_not_active(tmp_path):
    watcher, _ = _watcher(tmp_path)
    video = tmp_path / "stream.ts"
    video.write_bytes(b"x" * 100)

    assert watcher.is_active(str(video)) is False


def test_considering_the_same_path_twice_only_processes_it_once(tmp_path):
    """The exact shape of the bug this fixes, scaled down: a retry sweep
    calling consider() again on a path already mid-flight must not queue
    a second, redundant pass."""
    calls = []
    started = __import__("threading").Event()
    finish = __import__("threading").Event()

    def slow_on_ready(path):
        calls.append(path)
        started.set()
        finish.wait(timeout=5)

    watcher, _ = _watcher(tmp_path, on_ready=slow_on_ready)
    video = tmp_path / "stream.ts"
    video.write_bytes(b"x" * 100)

    watcher.consider(str(video))
    assert started.wait(timeout=5), "on_ready never started"

    # The retry sweep's exact move: offer the same path again while the
    # first pass is still running. Long enough for a spurious duplicate
    # stability-wait (1s) to complete and enqueue, if nothing is
    # stopping it - short enough to keep the test fast when it does not.
    for _ in range(5):
        watcher.consider(str(video))
    time.sleep(1.5)

    finish.set()
    time.sleep(0.5)

    assert calls == [str(video)], \
        f"expected exactly one pass, got {len(calls)}: {calls}"


def test_a_path_is_active_while_queued_and_while_running(tmp_path):
    started = __import__("threading").Event()
    finish = __import__("threading").Event()

    def slow_on_ready(path):
        started.set()
        finish.wait(timeout=5)

    watcher, _ = _watcher(tmp_path, on_ready=slow_on_ready)
    video = tmp_path / "stream.ts"
    video.write_bytes(b"x" * 100)

    watcher.consider(str(video))
    assert started.wait(timeout=5)

    assert watcher.is_active(str(video)) is True
    finish.set()
    time.sleep(0.3)
    assert watcher.is_active(str(video)) is False


def test_a_finished_path_can_be_considered_again_later(tmp_path):
    """is_active() must not latch permanently - a genuinely NEW failure
    on the same file, on a later sweep, still has to be retried."""
    watcher, calls = _watcher(tmp_path)
    video = tmp_path / "stream.ts"
    video.write_bytes(b"x" * 100)

    watcher.consider(str(video))
    time.sleep(1.5)
    assert calls == [str(video)]

    watcher.consider(str(video))
    time.sleep(1.5)
    assert calls == [str(video), str(video)]


# ── the sweep itself, as wired into main.py's --watch loop ─────────────

def test_the_retry_sweep_constant_exists_and_is_slower_than_autoclip():
    """Slower on purpose - it re-hashes files to see what is already
    done, and the answer is almost always 'nothing new'."""
    import main

    assert main.RETRY_SWEEP_SECONDS > main.AUTOCLIP_SECONDS


def test_the_watch_loop_sweeps_the_folder_periodically():
    """Read out of main.py itself: the timer block that offers new
    arrivals must also periodically re-offer whatever is already
    sitting in the watch folder, gated the same way the other periodic
    work in this loop is (a `next_*` deadline compared against
    time.time())."""
    path = os.path.join(_UPLOADER, "main.py")
    with open(path, encoding="utf-8") as handle:
        body = handle.read()

    assert "next_retry_sweep" in body
    assert "watcher.consider(path)" in body
    # Not while dry_run - a dry run must not go on retrying next_autoclip
    # forever, and must never queue a real upload attempt.
    start = body.index("next_retry_sweep = time.time() + RETRY_SWEEP_SECONDS")
    loop = body[start:start + 1200]
    assert "not dry_run" in loop
