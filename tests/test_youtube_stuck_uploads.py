"""The uploads playlist lists every upload attempt - failed, rejected, and
ones stuck before processing ("Processing will begin shortly", Pending
for days). It was read as "this stream is on YouTube": "halfa mill"
9/21/26 stalled there, every later run skipped YouTube for it, Rumble got
it, and the source was retired as fully uploaded."""

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

from utils.youtube_checker import fetch_existing_videos, is_really_up  # noqa: E402

DAY = 24 * 3600


def _iso(seconds_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                         time.gmtime(time.time() - seconds_ago))


class _Call:
    def __init__(self, result):
        self._result = result

    def execute(self):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _Service:
    def __init__(self, videos, states=None):
        self._videos, self._states = videos, states

    def channels(self):
        service = self

        class C:
            def list(self, **k):
                return _Call({"items": [{"contentDetails": {
                    "relatedPlaylists": {"uploads": "UU1"}}}]})
        return C()

    def playlistItems(self):
        service = self

        class P:
            def list(self, **k):
                return _Call({"items": [
                    {"snippet": {"title": t, "publishedAt": when,
                                 "resourceId": {"videoId": vid}}}
                    for vid, t, when in service._videos]})
        return P()

    def videos(self):
        service = self

        class V:
            def list(self, **k):
                if isinstance(service._states, Exception):
                    return _Call(service._states)
                return _Call({"items": [
                    {"id": vid, "status": {"uploadStatus": up},
                     "processingDetails": {"processingStatus": proc}}
                    for vid, (up, proc) in service._states.items()]})
        return V()


def test_a_stalled_upload_does_not_count_as_on_youtube():
    service = _Service(
        [("ok", '"i feel sorry" 9/24/36 Stackswopo Stream', _iso(DAY)),
         ("stuck", '"halfa mill" 9/21/26 Stackswopo Stream', _iso(5 * DAY)),
         ("bad", '"old" 9/1/26 Stackswopo Stream', _iso(20 * DAY))],
        {"ok": ("processed", "succeeded"),
         "stuck": ("uploaded", ""),
         "bad": ("failed", "")})

    kept = [v.video_id for v in fetch_existing_videos(service)]

    assert kept == ["ok"]


def test_one_still_processing_right_now_still_counts():
    """A stream this tool uploaded an hour ago looks exactly like a stuck
    one, minus the age. Re-uploading it would be a duplicate."""
    assert is_really_up("uploaded", "processing", _iso(3600))
    assert not is_really_up("uploaded", "processing", _iso(3 * DAY))


def test_rejected_and_terminated_do_not_count():
    assert not is_really_up("rejected", "")
    assert not is_really_up("uploaded", "terminated", _iso(60))
    assert is_really_up("processed", "succeeded", _iso(90 * DAY))


def test_if_states_cannot_be_read_everything_counts_as_before():
    service = _Service([("a", "t 9/1/26", _iso(DAY))], RuntimeError("quota"))
    assert [v.video_id for v in fetch_existing_videos(service)] == ["a"]
