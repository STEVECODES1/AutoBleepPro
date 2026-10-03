"""
The Postproxy clip publisher, against a fake HTTP layer. Nothing here
reaches postproxy.dev.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)


class _Response:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.ok = 200 <= status < 300

    def json(self):
        return self._body


PROFILES = {"data": [
    {"id": "ig1", "name": "stackswopoman", "platform": "instagram",
     "status": "active"},
    {"id": "tt1", "name": "stevebottin", "platform": "tiktok",
     "status": "active"},
    {"id": "yt1", "name": "STACKSWOPO CLIPS", "platform": "youtube",
     "status": "active"},
]}


class FakeHTTP:
    def __init__(self, profiles=PROFILES, created=None, statuses=(),
                 post_status=201):
        self.profiles = profiles
        self.created = created
        self.statuses = list(statuses)
        self.post_status = post_status
        self.posts = []

    def get(self, url, headers=None, timeout=None):
        assert headers["Authorization"] == "Bearer test-key"
        if url.endswith("/profiles"):
            return _Response(200, self.profiles)
        return _Response(200, self.statuses.pop(0) if self.statuses
                         else self.created)

    def post(self, url, files=None, headers=None, timeout=None):
        assert url.endswith("/api/posts")
        fields = {}
        for name, value in files:
            if name == "media[]":
                fields.setdefault(name, []).append(value[0])
            elif name == "profiles[]":
                fields.setdefault(name, []).append(value[1])
            else:
                fields[name] = value[1]
        self.posts.append({"fields": fields, "headers": headers})
        return _Response(self.post_status, self.created)


@pytest.fixture
def pp(monkeypatch, tmp_path):
    from publishers import postproxy

    monkeypatch.setenv("POSTPROXY_API_KEY", "test-key")
    monkeypatch.setattr(postproxy, "POLL_INTERVAL_S", 0)
    monkeypatch.setattr(postproxy.time, "sleep", lambda s: None)
    clip = tmp_path / "Funny moment - Clip 01.mp4"
    clip.write_bytes(b"x" * 100)
    return postproxy, str(clip)


def _created(*platforms):
    return {"id": "post1", "status": "processing", "platforms": [
        {"platform": name, "status": status, **extra}
        for name, status, extra in platforms]}


def test_instagram_and_tiktok_go_in_one_post_as_a_reel(pp, monkeypatch):
    postproxy, clip = pp
    http = FakeHTTP(created=_created(("instagram", "published", {}),
                                     ("tiktok", "published", {})))
    monkeypatch.setattr(postproxy, "requests", http)

    result = postproxy.PostproxyPublisher({}).post_clip(clip, "Funny moment")

    fields = http.posts[0]["fields"]
    assert fields["profiles[]"] == ["ig1", "tt1"]          # not YouTube
    assert fields["platforms[instagram][format]"] == "reel"
    assert fields["platforms[tiktok][privacy_status]"] == "PUBLIC_TO_EVERYONE"
    assert fields["media[]"] == ["Funny moment - Clip 01.mp4"]
    assert postproxy.platforms_reached(result) == {"instagram", "tiktok"}


def test_the_youtube_route_posts_only_to_youtube_as_a_titled_short(
        pp, monkeypatch):
    postproxy, clip = pp
    http = FakeHTTP(created=_created(("youtube", "published",
                                      {"url": "https://youtu.be/abc"})))
    monkeypatch.setattr(postproxy, "requests", http)

    result = postproxy.PostproxyPublisher({}, "postproxy_youtube").post_clip(
        clip, "He really said that\n\n#stackswopo #Shorts")

    fields = http.posts[0]["fields"]
    assert fields["profiles[]"] == ["yt1"]
    assert fields["platforms[youtube][title]"] == "He really said that"
    assert fields["platforms[youtube][privacy_status]"] == "public"
    assert postproxy.platforms_reached(result) == {"youtube_shorts"}
    assert result["results"][0]["post_url"] == "https://youtu.be/abc"


def test_a_retry_of_the_same_clip_sends_the_same_idempotency_key(
        pp, monkeypatch):
    """A dropped connection after Postproxy took the clip must not turn
    into a second post when the queue retries."""
    postproxy, clip = pp
    http = FakeHTTP(created=_created(("instagram", "published", {}),
                                     ("tiktok", "published", {})))
    monkeypatch.setattr(postproxy, "requests", http)
    publisher = postproxy.PostproxyPublisher({})

    publisher.post_clip(clip, "a")
    publisher.post_clip(clip, "a")

    keys = [p["headers"]["Idempotency-Key"] for p in http.posts]
    assert keys[0] == keys[1]
    assert keys[0] != postproxy.idempotency_key("postproxy_youtube", clip)


def test_a_failed_platform_is_not_counted_as_reached(pp, monkeypatch):
    postproxy, clip = pp
    http = FakeHTTP(
        created=_created(("instagram", "pending", {}),
                         ("tiktok", "pending", {})),
        statuses=[_created(("instagram", "published", {}),
                           ("tiktok", "failed", {"error": "too short"}))])
    monkeypatch.setattr(postproxy, "requests", http)

    result = postproxy.PostproxyPublisher({}).post_clip(clip, "a")

    assert postproxy.platforms_reached(result) == {"instagram"}
    failed = [r for r in result["results"] if r["platform"] == "tiktok"][0]
    assert failed["error"] == "too short"


def test_an_expired_account_is_left_out(pp, monkeypatch):
    postproxy, clip = pp
    profiles = {"data": [dict(PROFILES["data"][0], status="expired"),
                         PROFILES["data"][1]]}
    http = FakeHTTP(profiles=profiles,
                    created=_created(("tiktok", "published", {})))
    monkeypatch.setattr(postproxy, "requests", http)

    postproxy.PostproxyPublisher({}).post_clip(clip, "a")

    assert http.posts[0]["fields"]["profiles[]"] == ["tt1"]


def test_a_platform_switched_off_in_config_is_left_out(pp, monkeypatch):
    postproxy, clip = pp
    http = FakeHTTP(created=_created(("tiktok", "published", {})))
    monkeypatch.setattr(postproxy, "requests", http)
    cfg = {"posting": {"platforms": {"instagram": {"enabled": False}}}}

    postproxy.PostproxyPublisher(cfg).post_clip(clip, "a")

    assert http.posts[0]["fields"]["profiles[]"] == ["tt1"]


def test_nothing_connected_is_a_configuration_problem(pp, monkeypatch):
    from publishers.errors import NotConfigured

    postproxy, clip = pp
    monkeypatch.setattr(postproxy, "requests",
                        FakeHTTP(profiles={"data": [PROFILES["data"][0]]}))

    with pytest.raises(NotConfigured):
        postproxy.PostproxyPublisher({}, "postproxy_youtube").post_clip(
            clip, "a")


def test_a_bad_key_or_lapsed_plan_is_a_configuration_problem(
        pp, monkeypatch):
    """Not a failure to count against the circuit breaker."""
    from publishers.errors import NotConfigured

    postproxy, clip = pp
    http = FakeHTTP(created={"error": "Payment required"}, post_status=402)
    monkeypatch.setattr(postproxy, "requests", http)

    with pytest.raises(NotConfigured):
        postproxy.PostproxyPublisher({}).post_clip(clip, "a")


def test_no_key_means_not_ready(monkeypatch):
    from publishers import postproxy

    monkeypatch.delenv("POSTPROXY_API_KEY", raising=False)
    assert postproxy.PostproxyPublisher({}).ready() is False


def test_a_long_caption_sheds_hashtags_before_the_title():
    from publishers.postproxy import fit_caption

    caption = "The line\n\n" + " ".join(f"#tag{n}" for n in range(400))
    fitted = fit_caption(caption, 2200)
    assert len(fitted) <= 2200
    assert fitted.startswith("The line")


def test_the_publisher_is_what_the_queue_builds():
    from publishers.postproxy import PostproxyPublisher
    from utils.social_promoter import _publisher_for

    assert isinstance(_publisher_for("postproxy", {}), PostproxyPublisher)
    shorts = _publisher_for("postproxy_youtube", {})
    assert shorts.route == "postproxy_youtube"
    assert _publisher_for("upload_post", {}) is None
    assert _publisher_for("postplanify", {}) is None
