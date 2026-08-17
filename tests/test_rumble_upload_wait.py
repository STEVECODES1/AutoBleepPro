"""
Waiting for Rumble's transfer to actually finish before submitting.

A full VOD went up like this and never published:

    2026-08-17 07:08:26 [WARNING] Stackswopo - ... (FULL STREAM).ts:
    FAILED: Rumble finished but the video is not on the channel -
    it did not publish. Run again to retry.

Every clip from the same stream uploaded fine minutes later, so nothing
was wrong with the login or the browser. What was wrong was the wait:
the ceiling was a flat 90 minutes and the readout on the stuck form said
"(708.2KB/s - 120m22s)". The wait returns rather than raises, so the run
went on to submit a form whose file was still uploading, and the page sat
filled in and unpublished.

Two separate ways that wait could end early, both fixed here:
  * the flat ceiling, now scaled to the file's size
  * a single failed page read being treated as "the readout is gone,
    so the upload must be done"
"""

from __future__ import annotations

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
for _path in (_REPO, _UPLOADER):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from utils import rumble_uploader  # noqa: E402
from utils.rumble_uploader import (  # noqa: E402
    MIN_UPLOAD_WAIT_SECONDS,
    RumbleUploader,
    upload_timeout_for,
)


class FakeLocator:
    def __init__(self, page):
        self._page = page

    def inner_text(self, timeout=None):
        return self._page._next_text()


class FakePage:
    """Serves one scripted body text per poll.

    A text of None means the read raises, the way a real inner_text does
    when the page is mid-navigation or simply slow.
    """

    def __init__(self, texts):
        self._texts = list(texts)
        self.polls = 0
        self.waited_ms = 0

    def _next_text(self):
        self.polls += 1
        # The last state repeats forever once the script runs out - a real
        # page keeps showing whatever it last showed.
        text = self._texts.pop(0) if len(self._texts) > 1 else self._texts[0]
        if text is None:
            raise RuntimeError("page read failed")
        return text

    def locator(self, _selector):
        return FakeLocator(self)

    def wait_for_timeout(self, ms):
        self.waited_ms += ms


def _wait(page, **kwargs):
    seen = []
    done = RumbleUploader.__new__(RumbleUploader)._wait_for_upload_complete(
        page, seen.append, **kwargs)
    return done, seen


def test_a_failed_page_read_is_not_proof_the_upload_finished():
    """The bug: one flaky read at 3% ended the wait and submit followed."""
    page = FakePage(["3%", None, "4%", "5%", "100%"])

    done, seen = _wait(page)

    assert done is True
    assert seen == [3, 4, 5, 100]


def test_the_readout_disappearing_for_good_still_counts_as_finished():
    """Rumble swaps the progress text out for the finished state."""
    page = FakePage(["40%", "80%", "Upload complete", "Upload complete",
                     "Upload complete"])

    done, _ = _wait(page)

    assert done is True


def test_one_frame_without_a_percentage_does_not_end_the_wait():
    """A single re-render caught between frames is not the finished state."""
    page = FakePage(["40%", "", "60%", "100%"])

    done, seen = _wait(page)

    assert done is True
    assert seen == [40, 60, 100]


def test_giving_up_reports_that_it_gave_up():
    """The caller submits anyway, but this must not read as success."""
    page = FakePage(["7%"] * 100)

    done, _ = _wait(page, timeout_seconds=0.001)

    assert done is False


def test_a_long_vod_gets_hours_not_ninety_minutes(tmp_path):
    """The VOD that failed was still transferring at 120m22s."""
    vod = tmp_path / "stream.ts"
    vod.write_bytes(b"x" * (6 * 1024 * 1024 * 1024 // 1024))  # stand-in
    os.truncate(vod, 6 * 1024 * 1024 * 1024)

    ceiling = upload_timeout_for(str(vod))

    assert ceiling > 120 * 60, "the failing upload took 120m22s"


def test_a_short_clip_still_gets_the_floor(tmp_path):
    """Small files must not get a proportionally tiny ceiling."""
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x" * 2048)

    assert upload_timeout_for(str(clip)) == MIN_UPLOAD_WAIT_SECONDS


def test_a_missing_file_falls_back_to_the_floor():
    assert upload_timeout_for("/no/such/file.mp4") == MIN_UPLOAD_WAIT_SECONDS


def test_a_stalled_upload_does_not_hold_the_run_for_the_whole_ceiling(
        monkeypatch):
    """The ceiling is hours now, so a dead transfer needs its own exit."""
    monkeypatch.setattr(rumble_uploader, "STALL_SECONDS", 0)
    page = FakePage(["12%"] * 50)

    done, _ = _wait(page, timeout_seconds=60 * 60 * 5)

    assert done is False
    assert page.polls < 10, "should bail on the stall, not poll for hours"
