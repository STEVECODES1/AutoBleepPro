"""X through Buffer's free plan."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "auto_uploader"))

import pytest  # noqa: E402

from publishers import buffer as B  # noqa: E402


def test_links_count_as_23_characters():
    text = "watch " + "https://rumble.com/" + "a" * 200
    assert B.x_length(text) == len("watch ") + 23


def test_a_long_message_is_cut_but_keeps_its_link():
    link = "https://rumble.com/v7ggb28-my-name-johnny-cox.html"
    message = "New upload: " + "word " * 100 + "\n" + link
    out = B.fit_for_x(message, link)
    assert link in out and B.x_length(out) <= 280


def test_a_short_message_is_untouched():
    link = "https://youtu.be/x"
    assert B.fit_for_x(f"hi {link}", link) == f"hi {link}"


def test_x_goes_through_buffer_when_its_key_is_set(monkeypatch):
    from utils.social_promoter import _publisher_for

    monkeypatch.setenv("BUFFER_API_KEY", "test")
    assert type(_publisher_for("x", {})).__name__ == "BufferPublisher"
    assert type(_publisher_for("buffer_x", {})).__name__ == "BufferPublisher"


def _fake_buffer(monkeypatch, calls):
    def fake_call(self, query, variables=None):
        calls.append((query, variables))
        if "organizations" in query:
            return {"account": {"organizations": [{"id": "o1", "name": "me"}]}}
        if "channels" in query:
            return {"channels": [{"id": "c-ig", "service": "instagram"},
                                 {"id": "c-x", "service": "twitter"},
                                 {"id": "c-tt", "service": "tiktok"}]}
        return {"createPost": {"post": {"id": "p1"}}}

    monkeypatch.setattr(B.BufferPublisher, "_call", fake_call)


def test_buffer_posts_a_link_now_to_the_x_channel(monkeypatch):
    monkeypatch.setenv("BUFFER_API_KEY", "test")
    calls = []
    _fake_buffer(monkeypatch, calls)
    assert B.BufferPublisher({}).post_link("new clip", "https://r.com/v") is True
    sent = calls[-1][1]["input"]
    assert sent["channelId"] == "c-x" and sent["mode"] == "shareNow"
    assert "https://r.com/v" in sent["text"]


def test_a_clip_goes_up_as_a_video_from_its_public_link(monkeypatch, tmp_path):
    from utils import cloud_host

    monkeypatch.setenv("BUFFER_API_KEY", "test")
    monkeypatch.setattr(cloud_host, "ready", lambda: True)
    monkeypatch.setattr(cloud_host, "host_video",
                        lambda path: "https://res.cloudinary.com/x/clip.mp4")
    calls = []
    _fake_buffer(monkeypatch, calls)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    assert B.BufferPublisher({}).post_clip(str(clip), "caption #gtarp")
    sent = calls[-1][1]["input"]
    assert sent["assets"] == [{"video": {
        "url": "https://res.cloudinary.com/x/clip.mp4"}}]
    assert sent["channelId"] == "c-x" and sent["mode"] == "shareNow"


def test_no_host_is_a_setup_step_not_a_failure(monkeypatch, tmp_path):
    from publishers.errors import NotConfigured
    from utils import cloud_host

    monkeypatch.setenv("BUFFER_API_KEY", "test")
    monkeypatch.setattr(cloud_host, "ready", lambda: False)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    with pytest.raises(NotConfigured):
        B.BufferPublisher({}).post_clip(str(clip), "caption")


def test_buffer_x_follows_the_x_rules_in_the_queue():
    from utils.clip_queue import CLIP_PLATFORMS, DIRECT_BASE, base_platform

    assert "buffer_x" in CLIP_PLATFORMS
    assert base_platform("buffer_x") == "x"
    assert DIRECT_BASE["buffer_x"] == "x"


def test_cloudinary_signature_matches_their_documented_example():
    from utils.cloud_host import _sign

    # From Cloudinary's "Generating authentication signatures" guide.
    params = {"eager": "w_400,h_300,c_pad|w_260,h_200,c_crop",
              "public_id": "sample_image", "timestamp": 1315060510}
    assert _sign(params, "abcd") == "bfd09f95f331f558cbd1320e67aa8d488770583e"


# ── TikTok through the same Buffer account ──────────────────────────

def test_buffer_tiktok_is_its_own_route_with_the_tiktok_rules(monkeypatch):
    from utils.clip_queue import CLIP_PLATFORMS, DIRECT_BASE, base_platform
    from utils.social_promoter import _publisher_for

    assert "buffer_tiktok" in CLIP_PLATFORMS
    assert base_platform("buffer_tiktok") == "tiktok"
    assert DIRECT_BASE["buffer_tiktok"] == "tiktok"
    pub = _publisher_for("buffer_tiktok", {})
    assert type(pub).__name__ == "BufferPublisher"
    assert pub.service == "tiktok" and not pub.supports_link_posts


def test_a_tiktok_clip_goes_to_the_tiktok_channel_with_its_full_caption(
        monkeypatch, tmp_path):
    from utils import cloud_host

    monkeypatch.setenv("BUFFER_API_KEY", "test")
    monkeypatch.setattr(cloud_host, "ready", lambda: True)
    monkeypatch.setattr(cloud_host, "host_video",
                        lambda path: "https://res.cloudinary.com/x/clip.mp4")
    calls = []
    _fake_buffer(monkeypatch, calls)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    caption = "word " * 100  # 500 chars: too long for X, fine for TikTok
    assert B.BufferPublisher({}, "tiktok").post_clip(str(clip), caption)
    sent = calls[-1][1]["input"]
    assert sent["channelId"] == "c-tt"
    assert sent["text"] == caption.strip()
    assert sent["assets"] == [{"video": {
        "url": "https://res.cloudinary.com/x/clip.mp4"}}]
