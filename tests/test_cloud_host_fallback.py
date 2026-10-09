"""A clip still gets a public link when Cloudinary's month runs out."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "auto_uploader"))

from utils import cloud_host as C  # noqa: E402


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"x" * 1000)
    return str(path)


def test_no_cloudinary_goes_straight_to_the_free_host(monkeypatch, clip):
    monkeypatch.setattr(C, "credentials", lambda: None)
    monkeypatch.setattr(C, "_litterbox", lambda p: "https://litter.catbox.moe/a.mp4")
    assert C.ready()
    assert C.host_video(clip) == "https://litter.catbox.moe/a.mp4"


def test_cloudinary_over_its_limit_is_rested_and_the_fallback_used(monkeypatch, clip):
    monkeypatch.setattr(C, "credentials", lambda: ("c", "k", "s"))
    calls = []

    def over(path):
        calls.append(path)
        raise RuntimeError("Cloudinary upload HTTP 420: Usage limit exceeded")

    monkeypatch.setattr(C, "_cloudinary", over)
    monkeypatch.setattr(C, "_litterbox", lambda p: "https://litter.catbox.moe/b.mp4")
    assert C.host_video(clip).endswith("b.mp4")
    assert C.host_video(clip).endswith("b.mp4")
    assert len(calls) == 1          # not asked again while over its limit


def test_cloudinary_working_is_still_first(monkeypatch, clip):
    monkeypatch.setattr(C, "credentials", lambda: ("c", "k", "s"))
    monkeypatch.setattr(C, "_cloudinary", lambda p: "https://res.cloudinary.com/x.mp4")
    monkeypatch.setattr(C, "_litterbox", lambda p: pytest.fail("not needed"))
    assert C.host_video(clip) == "https://res.cloudinary.com/x.mp4"


def test_the_fallback_can_be_turned_off(monkeypatch, clip):
    monkeypatch.setattr(C, "credentials", lambda: None)
    monkeypatch.setenv("CLOUD_HOST_FALLBACK", "0")
    assert not C.ready()
    with pytest.raises(RuntimeError, match="CLOUDINARY_URL"):
        C.host_video(clip)
