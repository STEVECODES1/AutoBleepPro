"""An explicit "do not retry" has to be honoured.

    [Publisher] Instagram: upload rejected (HTTP 400):
      {'debug_info': {'retriable': False, 'type': 'ProcessingFailedError'}}
    [Clips] instagram: uploading _vertical_Yoo Howl - Clip 03.mp4 ...
    [Publisher] Instagram: upload rejected (HTTP 400): ...same...
    [Clips] instagram: uploading _vertical_Yoo Howl - Clip 03.mp4 ...
    [Publisher] Instagram: upload rejected (HTTP 400): ...same...

Three identical uploads of one clip inside a single drain, each answered
identically, against a field that said not to. Retrying there is not
persistence; it is hammering an API that already answered.
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

def test_the_real_rejection_body_is_recognised():
    assert is_permanent_rejection(
        {"debug_info": {"retriable": False, "type": "ProcessingFailedError",
                        "message": "Request processing failed"}})


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
