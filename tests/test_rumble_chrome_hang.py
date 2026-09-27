"""A real watcher run: Chrome was listening on 9222, the websocket
connected, and connect_over_cdp still hung for its full 180s - on every
clip, ten in a row, half an hour of nothing. A frozen tab in that window
does exactly this."""

import contextlib
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

from utils import rumble_uploader as ru  # noqa: E402

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
