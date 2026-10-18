"""A real watcher run: Chrome was listening on 9222, the websocket
connected, and connect_over_cdp still hung for its full 180s - on every
clip, ten in a row, half an hour of nothing. A frozen tab in that window
does exactly this."""

import contextlib
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

from utils import rumble_uploader as ru  # noqa: E402

# The real stop, taken before conftest swaps it out for each test.
REAL_KILL = ru._kill_chrome_on_port

HANG = ("BrowserType.connect_over_cdp: Timeout 60000ms exceeded.\n"
        "Call log:\n  - <ws connected> ws://localhost:9222/devtools/browser/x")


@pytest.fixture
def frozen_chrome(monkeypatch, tmp_path):
    ru._CHROME_DOWN.update(until=0.0, why="")
    attempts = []

    class Chromium:
        def connect_over_cdp(self, url, timeout=None):
            attempts.append(timeout)
            raise RuntimeError(HANG)

    class P:
        chromium = Chromium()

    @contextlib.contextmanager
    def fake_playwright():
        yield P()

    monkeypatch.setattr(ru, "sync_playwright", fake_playwright)
    monkeypatch.setattr("utils.chrome_cdp.ensure_chrome",
                        lambda url: (True, "Chrome already listening on 9222"))
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    uploader = ru.RumbleUploader("", "", "", "",
                                 cdp_url="http://localhost:9222")
    yield uploader, str(video), attempts
    ru._CHROME_DOWN.update(until=0.0, why="")


def test_a_hung_attach_says_what_to_do(frozen_chrome):
    uploader, video, attempts = frozen_chrome
    with pytest.raises(ru.RumbleSetupError, match="frozen tab"):
        uploader.upload(video, "t", "d", [])
    assert attempts == [ru.CDP_ATTACH_TIMEOUT_MS]
    assert ru.CDP_ATTACH_TIMEOUT_MS <= 60_000


def test_the_next_clip_does_not_wait_on_the_same_frozen_chrome(frozen_chrome):
    uploader, video, attempts = frozen_chrome
    with pytest.raises(ru.RumbleSetupError):
        uploader.upload(video, "t", "d", [])
    with pytest.raises(ru.RumbleSetupError, match="not waiting on it again"):
        uploader.upload(video, "t2", "d", [])
    assert len(attempts) == 1


def test_it_tries_chrome_again_once_the_wait_is_over(frozen_chrome):
    uploader, video, attempts = frozen_chrome
    with pytest.raises(ru.RumbleSetupError):
        uploader.upload(video, "t", "d", [])
    ru._CHROME_DOWN["until"] = 0.0
    with pytest.raises(ru.RumbleSetupError):
        uploader.upload(video, "t2", "d", [])
    assert len(attempts) == 2


@pytest.fixture
def heal_allowed(monkeypatch):
    """The self-heal is off in the suite unless a test turns it on. The
    list collects the ports it would have stopped Chrome on."""
    killed = []
    monkeypatch.setattr(ru, "_kill_chrome_on_port", killed.append)
    monkeypatch.setattr(ru.time, "sleep", lambda s: None)
    ru._LAST_HEAL[0] = 0.0
    yield killed
    ru._LAST_HEAL[0] = time.time()


def test_a_hung_chrome_is_restarted_once_and_tried_again(frozen_chrome, heal_allowed):
    uploader, video, attempts = frozen_chrome
    with pytest.raises(ru.RumbleSetupError, match="frozen tab"):
        uploader.upload(video, "t", "d", [])
    assert heal_allowed == [9222]
    assert attempts == [ru.CDP_ATTACH_TIMEOUT_MS] * 2


def test_chrome_is_never_restarted_under_another_upload(frozen_chrome, heal_allowed):
    uploader, video, attempts = frozen_chrome
    ru._ACTIVE[0] = 1  # another clip is inside that Chrome right now
    try:
        with pytest.raises(ru.RumbleSetupError):
            uploader.upload(video, "t", "d", [])
    finally:
        ru._ACTIVE[0] = 0
    assert heal_allowed == []
    assert len(attempts) == 1


def test_chrome_is_restarted_at_most_once_per_half_hour(frozen_chrome, heal_allowed):
    uploader, video, attempts = frozen_chrome
    for title in ("t", "t2"):
        ru._CHROME_DOWN.update(until=0.0, why="")
        with pytest.raises(ru.RumbleSetupError):
            uploader.upload(video, title, "d", [])
    assert heal_allowed == [9222]
    assert len(attempts) == 3


def test_the_suite_can_never_close_the_real_chrome(monkeypatch):
    calls = []
    monkeypatch.setattr(ru.subprocess, "run", lambda *a, **k: calls.append(a))
    REAL_KILL(9222)
    assert calls == []
