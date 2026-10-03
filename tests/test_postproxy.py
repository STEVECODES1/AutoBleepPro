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
    postproxy._PROFILES_CACHE.clear()
    clip = tmp_path / "Funny moment - Clip 01.mp4"
    clip.write_bytes(b"x" * 100)
    return postproxy, str(clip)


def _created(*platforms):
    return {"id": "post1", "status": "processing", "platforms": [
        {"platform": name, "status": status, **extra}
        for name, status, extra in platforms]}


def test_instagram_gets_its_own_post_as_a_reel(pp, monkeypatch):
    postproxy, clip = pp
    http = FakeHTTP(created=_created(("instagram", "published", {})))
    monkeypatch.setattr(postproxy, "requests", http)

    result = postproxy.PostproxyPublisher(
        {}, "postproxy_instagram").post_clip(clip, "Funny moment")

    fields = http.posts[0]["fields"]
    assert fields["profiles[]"] == ["ig1"]          # Instagram only
    assert fields["post[body]"] == "Funny moment"
    assert fields["platforms[instagram][format]"] == "reel"
    assert fields["media[]"] == ["Funny moment - Clip 01.mp4"]
    assert postproxy.platforms_reached(result) == {"instagram"}


def test_tiktok_gets_its_own_post_and_caption(pp, monkeypatch):
    postproxy, clip = pp
    http = FakeHTTP(created=_created(("tiktok", "published", {})))
    monkeypatch.setattr(postproxy, "requests", http)

    result = postproxy.PostproxyPublisher(
        {}, "postproxy_tiktok").post_clip(clip, "He got arrested in court")

    fields = http.posts[0]["fields"]
    assert fields["profiles[]"] == ["tt1"]
    assert fields["post[body]"] == "He got arrested in court"
    assert fields["platforms[tiktok][privacy_status]"] == "PUBLIC_TO_EVERYONE"
    assert postproxy.platforms_reached(result) == {"tiktok"}


def test_the_youtube_route_posts_only_to_youtube_as_a_titled_short(
        pp, monkeypatch):
    postproxy, clip = pp
    http = FakeHTTP(created=_created(("youtube", "published",
                                      {"url": "https://youtu.be/abc"})))
    monkeypatch.setattr(postproxy, "requests", http)
    cfg = {"youtube_shorts": {
        "description_template": "[CAPTION]\n\nFull streams: rumble",
        "privacy": "public"}}

    result = postproxy.PostproxyPublisher(cfg, "postproxy_youtube").post_clip(
        clip, "He really said that 🤣🤣\n\n#stackswopo")

    fields = http.posts[0]["fields"]
    assert fields["profiles[]"] == ["yt1"]
    # Titled like the direct Shorts publisher: the line, without the
    # trailing emoji.
    assert fields["platforms[youtube][title]"] == "He really said that"
    assert fields["platforms[youtube][privacy_status]"] == "public"
    assert "Full streams: rumble" in fields["post[body]"]
    assert "#Shorts" in fields["post[body]"]
    assert postproxy.platforms_reached(result) == {"youtube_shorts"}
    assert result["results"][0]["post_url"] == "https://youtu.be/abc"


def test_x_is_trimmed_to_its_limit(pp, monkeypatch):
    postproxy, clip = pp
    profiles = {"data": [{"id": "x1", "name": "WOPOCLIPS",
                          "platform": "twitter", "status": "active"}]}
    http = FakeHTTP(profiles=profiles,
                    created=_created(("twitter", "published", {})))
    monkeypatch.setattr(postproxy, "requests", http)

    result = postproxy.PostproxyPublisher({}, "postproxy_x").post_clip(
        clip, "line\n\n" + "#tag " * 100)

    assert len(http.posts[0]["fields"]["post[body]"]) <= 280
    assert postproxy.platforms_reached(result) == {"x"}


def test_a_route_with_nothing_connected_is_not_ready(pp, monkeypatch):
    """Facebook and X are not connected yet: their routes must stay
    quiet, not queue a job per clip that can only fail."""
    postproxy, clip = pp
    monkeypatch.setattr(postproxy, "requests", FakeHTTP())

    assert postproxy.PostproxyPublisher({}, "postproxy_tiktok").ready()
    assert not postproxy.PostproxyPublisher({}, "postproxy_x").ready()
    assert not postproxy.PostproxyPublisher({}, "postproxy_facebook").ready()


def test_the_profile_list_is_asked_for_once_not_per_route(pp, monkeypatch):
    postproxy, clip = pp
    http = FakeHTTP()
    calls = []
    real_get = http.get
    http.get = lambda url, **k: calls.append(url) or real_get(url, **k)
    monkeypatch.setattr(postproxy, "requests", http)

    for route in postproxy.ROUTES:
        postproxy.PostproxyPublisher({}, route).ready()

    assert len(calls) == 1


def test_a_retry_of_the_same_clip_sends_the_same_idempotency_key(
        pp, monkeypatch):
    """A dropped connection after Postproxy took the clip must not turn
    into a second post when the queue retries."""
    postproxy, clip = pp
    http = FakeHTTP(created=_created(("instagram", "published", {})))
    monkeypatch.setattr(postproxy, "requests", http)
    publisher = postproxy.PostproxyPublisher({}, "postproxy_instagram")

    publisher.post_clip(clip, "a")
    publisher.post_clip(clip, "a")

    keys = [p["headers"]["Idempotency-Key"] for p in http.posts]
    assert keys[0] == keys[1]
    # A different platform is a different post of the same clip.
    assert keys[0] != postproxy.idempotency_key("postproxy_tiktok", clip)


def test_a_failed_platform_is_not_counted_as_reached(pp, monkeypatch):
    postproxy, clip = pp
    http = FakeHTTP(
        created=_created(("tiktok", "pending", {})),
        statuses=[_created(("tiktok", "failed", {"error": "too short"}))])
    monkeypatch.setattr(postproxy, "requests", http)

    result = postproxy.PostproxyPublisher({}, "postproxy_tiktok").post_clip(
        clip, "a")

    assert postproxy.platforms_reached(result) == set()
    assert result["results"][0]["error"] == "too short"


def test_an_expired_account_is_left_out(pp, monkeypatch):
    postproxy, clip = pp
    profiles = {"data": [dict(PROFILES["data"][0], status="expired")]}
    monkeypatch.setattr(postproxy, "requests", FakeHTTP(profiles=profiles))

    assert not postproxy.PostproxyPublisher(
        {}, "postproxy_instagram").ready()


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
        postproxy.PostproxyPublisher({}, "postproxy_tiktok").post_clip(
            clip, "a")


def test_no_key_means_not_ready(monkeypatch):
    from publishers import postproxy

    monkeypatch.delenv("POSTPROXY_API_KEY", raising=False)
    assert postproxy.PostproxyPublisher({}, "postproxy_tiktok").ready() \
        is False


def test_a_long_caption_sheds_hashtags_before_the_title():
    from publishers.postproxy import fit_caption

    caption = "The line\n\n" + " ".join(f"#tag{n}" for n in range(400))
    fitted = fit_caption(caption, 2200)
    assert len(fitted) <= 2200
    assert fitted.startswith("The line")


def test_the_publisher_is_what_the_queue_builds():
    from publishers.postproxy import ROUTES, PostproxyPublisher
    from utils.clip_queue import CLIP_PLATFORMS, ROUTE_BASE
    from utils.social_promoter import _publisher_for

    for route in ROUTES:
        built = _publisher_for(route, {})
        assert isinstance(built, PostproxyPublisher) and built.route == route
        assert route in CLIP_PLATFORMS
        assert ROUTE_BASE[route] == ROUTES[route][1]
    for gone in ("upload_post", "postplanify", "postproxy"):
        assert _publisher_for(gone, {}) is None
