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
    assert sent["channelId"] == "c-x" and sent["mode"] == "customScheduled"
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
    assert sent["channelId"] == "c-x" and sent["mode"] == "customScheduled"


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


def test_cloudinary_credentials_from_the_url_or_the_three_parts(monkeypatch):
    from utils import cloud_host

    for name in ("URL", "CLOUD_NAME", "API_KEY", "API_SECRET"):
        monkeypatch.delenv(f"CLOUDINARY_{name}", raising=False)
    assert cloud_host.credentials() is None
    # The console's copy has placeholders: not credentials.
    monkeypatch.setenv("CLOUDINARY_URL",
                       "cloudinary://<your_api_key>:<your_api_secret>@demo")
    assert cloud_host.credentials() is None
    monkeypatch.setenv("CLOUDINARY_CLOUD_NAME", "demo")
    monkeypatch.setenv("CLOUDINARY_API_KEY", "123")
    monkeypatch.setenv("CLOUDINARY_API_SECRET", "abc")
    assert cloud_host.credentials() == ("demo", "123", "abc")
    monkeypatch.setenv("CLOUDINARY_URL", "cloudinary://9:z@other")
    assert cloud_host.credentials() == ("other", "9", "z")


def test_a_clip_over_the_free_upload_cap_is_refused_before_uploading(
        monkeypatch, tmp_path):
    from utils import cloud_host

    monkeypatch.setenv("CLOUDINARY_URL", "cloudinary://1:s@demo")
    monkeypatch.setattr(cloud_host, "MAX_BYTES", 10)
    clip = tmp_path / "big.mp4"
    clip.write_bytes(b"x" * 11)
    with pytest.raises(RuntimeError, match="free plan"):
        cloud_host.host_video(str(clip))



# ── a lost reply is not a failed post (2026-10-06) ──────────────────

def _clip(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    return str(clip)


def _recent_post(text, minutes_ago=1, status="sent"):
    import datetime as dt

    made = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes_ago)
    return {"node": {"id": "p-old", "text": text, "status": status,
                     "createdAt": made.strftime("%Y-%m-%dT%H:%M:%S.000Z")}}


def _buffer_with(monkeypatch, calls, recent, create):
    def fake_call(self, query, variables=None):
        calls.append(query)
        if "organizations" in query:
            return {"account": {"organizations": [{"id": "o1"}]}}
        if "channels(" in query:
            return {"channels": [{"id": "c-x", "service": "twitter"}]}
        if "posts(" in query:
            return {"posts": {"edges": recent()}}
        return create()

    monkeypatch.setattr(B.BufferPublisher, "_call", fake_call)
    monkeypatch.setattr("time.sleep", lambda s: None)


def _hosted(monkeypatch, uploads):
    from utils import cloud_host

    monkeypatch.setenv("BUFFER_API_KEY", "test")
    monkeypatch.setattr(cloud_host, "ready", lambda: True)
    monkeypatch.setattr(cloud_host, "host_video",
                        lambda path: uploads.append(path) or "https://h/c.mp4")


def test_a_dropped_reply_for_a_post_that_went_out_counts_as_posted(
        monkeypatch, tmp_path):
    uploads, calls, made = [], [], []

    def create():
        made.append(1)
        raise B.BufferDropped("Remote end closed connection without response")

    _hosted(monkeypatch, uploads)
    _buffer_with(monkeypatch, calls,
                 lambda: [_recent_post("the caption")] if made else [], create)
    assert B.BufferPublisher({}).post_clip(_clip(tmp_path), "the caption") \
        == "buffer post p-old"


def test_a_dropped_reply_with_nothing_on_the_channel_is_a_retry(
        monkeypatch, tmp_path):
    uploads, calls = [], []

    def create():
        raise B.BufferDropped("Remote end closed connection without response")

    _hosted(monkeypatch, uploads)
    _buffer_with(monkeypatch, calls, lambda: [], create)
    assert B.BufferPublisher({}).post_clip(_clip(tmp_path), "caption") is None


def test_a_clip_already_on_the_channel_is_not_uploaded_or_posted_again(
        monkeypatch, tmp_path):
    uploads, calls = [], []
    _hosted(monkeypatch, uploads)
    _buffer_with(monkeypatch, calls,
                 lambda: [_recent_post("same caption", minutes_ago=600)],
                 lambda: pytest.fail("posted a second time"))
    assert B.BufferPublisher({}).post_clip(_clip(tmp_path), "same caption") \
        == "buffer post p-old"
    assert uploads == []


def test_a_post_that_errored_at_buffer_does_not_block_the_retry(
        monkeypatch, tmp_path):
    uploads, calls = [], []
    _hosted(monkeypatch, uploads)
    _buffer_with(monkeypatch, calls,
                 lambda: [_recent_post("cap", minutes_ago=30, status="error")],
                 lambda: {"createPost": {"post": {"id": "p-new"}}})
    assert B.BufferPublisher({}).post_clip(_clip(tmp_path), "cap") \
        == "buffer post p-new"
    assert len(uploads) == 1


def test_posts_are_scheduled_a_little_ahead_not_shared_now(monkeypatch):
    sent = []

    def fake_call(self, query, variables=None):
        if "organizations" in query:
            return {"account": {"organizations": [{"id": "o1"}]}}
        if "channels(" in query:
            return {"channels": [{"id": "c-x", "service": "twitter"}]}
        sent.append(variables["input"])
        return {"createPost": {"post": {"id": "p1"}}}

    monkeypatch.setenv("BUFFER_API_KEY", "test")
    monkeypatch.setattr(B.BufferPublisher, "_call", fake_call)
    assert B.BufferPublisher({}).post_link("hi https://x.y", "https://x.y")
    assert sent[0]["mode"] == "customScheduled"
    assert sent[0]["dueAt"].endswith("Z")
