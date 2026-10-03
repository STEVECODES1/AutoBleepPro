"""
A dropped connection resumes the upload; it does not start it again.

A real run: [Errno 10053] at 98% of a 5.3 GB stream, and every retry
sent the whole file again from 0% - leaving the broken upload behind on
the channel each time.
"""

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (os.path.join(_REPO, "auto_uploader"), _REPO):
    if path not in sys.path:
        sys.path.insert(0, path)

import pytest  # noqa: E402

yt = pytest.importorskip("utils.youtube_uploader")


class _Status:
    def __init__(self, p):
        self.p = p

    def progress(self):
        return self.p


class _Request:
    """next_chunk drops the line twice, then finishes - the SAME object
    throughout, which is what lets the library resume."""

    def __init__(self, drops):
        self.drops = drops
        self.calls = 0

    def next_chunk(self, num_retries=0):
        self.calls += 1
        if self.calls == 2 and self.drops:
            self.drops -= 1
            self.calls -= 1
            raise ConnectionAbortedError(10053, "connection aborted")
        if self.calls < 3:
            return _Status(self.calls / 3), None
        return None, {"id": "abc123"}


class _Service:
    def __init__(self, request):
        self.request = request
        self.inserts = 0

    def videos(self):
        return self

    def insert(self, **kwargs):
        self.inserts += 1
        return self.request


def _uploader(monkeypatch, tmp_path, request):
    service = _Service(request)
    up = yt.YouTubeUploader.__new__(yt.YouTubeUploader)
    monkeypatch.setattr(up, "_client", lambda: service, raising=False)
    monkeypatch.setattr(yt, "MediaFileUpload", lambda *a, **k: object())
    monkeypatch.setattr(yt.time, "sleep", lambda s: None)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    return up, service, str(video)


def test_a_dropped_connection_resumes_the_same_upload(monkeypatch, tmp_path):
    request = _Request(drops=2)
    up, service, video = _uploader(monkeypatch, tmp_path, request)

    url = up.upload(video, "t", "d", [])

    assert url == "https://www.youtube.com/watch?v=abc123"
    assert service.inserts == 1, "started a second upload instead of resuming"


def test_a_line_that_stays_down_still_fails(monkeypatch, tmp_path):
    request = _Request(drops=yt.MAX_RESUMES + 1)
    up, service, video = _uploader(monkeypatch, tmp_path, request)

    with pytest.raises(ConnectionAbortedError):
        up.upload(video, "t", "d", [])
    assert service.inserts == 1
