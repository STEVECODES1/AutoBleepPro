"""A timed-out YouTube upload usually landed anyway.

The bytes are all there; only the final acknowledgement never came
back. Retrying blind uploads the file a second time, and the operator
deletes the duplicate by hand - which is what happened to TALK TO ME
REAL QUICK after three 90% timeouts. So every retry first asks the
channel whether this exact title is already up (still-processing
counts: that is what a minutes-old landing looks like), the same way
Rumble retries check before re-sending.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "auto_uploader"))

import main  # noqa: E402
from utils.youtube_checker import ExistingVideo  # noqa: E402


def _videos(*titles):
    return [ExistingVideo(title=t, video_id=f"id{i}",
                          url=f"https://www.youtube.com/watch?v=id{i}")
            for i, t in enumerate(titles)]


def test_a_landed_timeout_is_not_uploaded_twice(monkeypatch):
    import utils.youtube_checker as yc
    monkeypatch.setattr(yc, "fetch_existing_videos",
                        lambda service: _videos("Stackswopo - TALK TO ME REAL QUICK - 10-14-26 (FULL STREAM)"))

    found = main._already_on_youtube(
        "Stackswopo - TALK TO ME REAL QUICK - 10-14-26 (FULL STREAM)",
        object(), "Gaming Stream")
    assert found.startswith("https://www.youtube.com/watch?v=id")


def test_a_genuinely_new_title_still_uploads(monkeypatch):
    import utils.youtube_checker as yc
    monkeypatch.setattr(yc, "fetch_existing_videos",
                        lambda service: _videos("Some older stream"))

    assert main._already_on_youtube("A brand new stream title here",
                                    object(), "Gaming Stream") == ""


def test_a_fallback_title_never_matches(monkeypatch):
    """Every untitled stream shares one name - matching on it would
    cancel uploads that are genuinely new."""
    import utils.youtube_checker as yc
    monkeypatch.setattr(yc, "fetch_existing_videos",
                        lambda service: _videos("Gaming Stream"))

    assert main._already_on_youtube("Gaming Stream", object(),
                                    "Gaming Stream") == ""


def test_anything_unreadable_means_upload(monkeypatch):
    import utils.youtube_checker as yc

    def explode(service):
        raise RuntimeError("quota spent")

    monkeypatch.setattr(yc, "fetch_existing_videos", explode)
    assert main._already_on_youtube("Some title here", object(),
                                    "Gaming Stream") == ""
    assert main._already_on_youtube("Some title here", None,
                                    "Gaming Stream") == ""
