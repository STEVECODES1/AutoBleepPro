"""An explicit "do not retry" has to be honoured - where it means it.

The original reason for reading the field at all:

    [Publisher] Instagram: upload rejected (HTTP 400):
      {'debug_info': {'retriable': False, 'type': 'ProcessingFailedError'}}
    [Clips] instagram: uploading _vertical_Yoo Howl - Clip 03.mp4 ...
    [Publisher] Instagram: upload rejected (HTTP 400): ...same...
    [Clips] instagram: uploading _vertical_Yoo Howl - Clip 03.mp4 ...
    [Publisher] Instagram: upload rejected (HTTP 400): ...same...

Three identical uploads of one clip inside a single drain, each answered
identically, against a field that said not to.

But a longer run of publishers.log shows the other half of it - the same
error, the same clip, and then it posts:

    11:16:46 upload rejected (HTTP 400): ProcessingFailedError,
             'Request processing failed', retriable: False
    11:17:34 ...the same
    11:18:10 uploaded 18.6 MB for container 18423474292146676
    11:18:18 published Reel, media_id=17959044240199078

and a `retriable: False` whose own message reads "Generic Internal Error:
An internal server error occurred. Please try again later."

So ProcessingFailedError is Meta being briefly unable, not Meta refusing
this video, and abandoning a clip on it threw away clips that would have
posted seconds later. The flag is still final everywhere else; the
attempt ceiling and the backoff are what bound this class instead.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from publishers.errors import (  # noqa: E402
    NotConfigured, PermanentlyRejected, is_permanent_rejection)


# ── reading Meta's answer ────────────────────────────────────────────

def test_a_processing_failure_is_not_final_however_it_is_flagged():
    """The clip that got this twice published on the third attempt."""
    assert not is_permanent_rejection(
        {"debug_info": {"retriable": False, "type": "ProcessingFailedError",
                        "message": "Request processing failed"}})


def test_the_type_is_read_however_it_is_cased():
    assert not is_permanent_rejection(
        {"error": {"debug_info": {"retriable": False,
                                  "type": "processingFailedError"}}})


def test_metas_own_server_error_is_never_about_this_video():
    """'Please try again later' arrived with retriable: False on it."""
    body = {"debug_info": {
        "retriable": False, "type": "ProcessingFailedError",
        "message": '{"success":false,"error":{"message":"Generic Internal '
                   'Error: An internal server error occurred. Please try '
                   'again later."}}'}}
    assert not is_permanent_rejection(body, status=500)
    # ...and a 5xx alone is enough, whatever the body says.
    assert not is_permanent_rejection({"retriable": False}, status=500)


def test_a_rejection_that_names_the_video_is_still_final():
    """Narrowing the rule must not disarm it - this is the case the
    exception exists for, and retrying it is hammering an API that
    already answered."""
    assert is_permanent_rejection(
        {"debug_info": {"retriable": False, "type": "UnsupportedFormatError",
                        "message": "Unsupported video format"}})


def test_it_is_found_when_nested_under_error():
    assert is_permanent_rejection(
        {"error": {"debug_info": {"retriable": False}}})


def test_it_is_found_at_the_top_level():
    assert is_permanent_rejection({"retriable": False})


def test_retriable_true_is_not_permanent():
    assert not is_permanent_rejection({"debug_info": {"retriable": True}})


def test_silence_is_not_permanent():
    """Absent means unknown. Treating that as final would abandon clips
    over a dropped connection - and the attempt ceiling already bounds
    the ordinary case."""
    assert not is_permanent_rejection({"error": {"message": "boom"}})
    assert not is_permanent_rejection({})


def test_a_non_dict_body_is_not_permanent():
    assert not is_permanent_rejection(None)
    assert not is_permanent_rejection("500 Server Error")
    assert not is_permanent_rejection(["retriable"])


# ── the exception survives the catch-alls ────────────────────────────

class _Rejecting:
    supports_reels = True

    def ready(self):
        return True

    def post_reel_from_file(self, *a, **k):
        raise PermanentlyRejected("Instagram will not process this video")


def test_publish_does_not_swallow_it(tmp_path, monkeypatch):
    """publish() wraps the upload in `except Exception`. A permanent
    rejection caught there would read as an ordinary failure and be
    retried - which is the whole bug."""
    from utils import clip_queue

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    monkeypatch.setattr(clip_queue, "_publisher", lambda p, c: _Rejecting())
    monkeypatch.setattr("utils.social_promoter._vertical_copy",
                        lambda path, s, c: (str(clip), None))
    # A Reel is censored before it is re-framed now, and there is no
    # whisper here. Nothing to censor = the path back unchanged.
    monkeypatch.setattr("utils.censor.censor_video",
                        lambda path, *a, **k: type(
                            "R", (), {"output_path": path,
                                      "violation_count": 0})())

    with pytest.raises(PermanentlyRejected):
        clip_queue.publish("instagram", str(clip), "cap", {}, dry_run=False)


class _RejectingClipPublisher:
    def ready(self):
        return True

    def post_clip(self, *a, **k):
        raise PermanentlyRejected("nope")


def test_the_post_clip_path_does_not_swallow_it_either(tmp_path, monkeypatch):
    from utils import clip_queue

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    monkeypatch.setattr(clip_queue, "_publisher",
                        lambda p, c: _RejectingClipPublisher())
    # Censoring off for this one. Shorts now bleep the clip's audio
    # before posting, and a one-byte stand-in video cannot be bleeped -
    # so the publisher would never be reached and the refusal under test
    # would never be raised. Whether a Short gets censored is
    # test_clip_queue's business; this is about the exception surviving.
    with pytest.raises(PermanentlyRejected):
        clip_queue.publish("youtube_shorts", str(clip), "cap",
                           {"youtube_shorts": {"censor_uploads": False}})


def test_an_ordinary_failure_is_still_just_false(tmp_path, monkeypatch):
    """Only an explicit refusal is final. Everything else keeps its
    retries."""
    from utils import clip_queue

    class _Broken(_Rejecting):
        def post_reel_from_file(self, *a, **k):
            raise RuntimeError("connection reset")

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    monkeypatch.setattr(clip_queue, "_publisher", lambda p, c: _Broken())
    monkeypatch.setattr("utils.social_promoter._vertical_copy",
                        lambda path, s, c: (str(clip), None))
    assert clip_queue.publish("instagram", str(clip), "cap", {}) is False


def test_it_is_a_different_thing_from_not_configured():
    """One is about the account, the other about this one video."""
    assert not issubclass(PermanentlyRejected, NotConfigured)
    assert not issubclass(NotConfigured, PermanentlyRejected)
