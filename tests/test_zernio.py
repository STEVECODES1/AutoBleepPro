"""Posting to X through an authorised third party.

X's own API charges $0.20 for a post containing a URL - every post this
pipeline would make. twikit and Selenium avoid that by driving X's
private endpoints with the account password in a file, which the
research proposing them says gets flagged as bot spam, and a brand-new
clipping account is the easiest kind to catch.

Zernio holds the X relationship itself. The stored credential is a
Zernio key, and revoking it costs nothing.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from publishers.errors import NotConfigured  # noqa: E402
from publishers.zernio import (  # noqa: E402
    MAX_TWEET_CHARS, MAX_UPLOAD_BYTES, ZernioError, ZernioPublisher, _post_url)


def _pub(destination="zernio_twitter", **settings):
    base = {"api_key": "sk_test",
            "accounts": {"twitter": {"account_id": "acc_1"},
                         "tiktok": {"account_id": "acc_tt"}}}
    base.update(settings)
    return ZernioPublisher({"zernio": base}, destination)


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"video bytes")
    return str(path)


# ── configuration ────────────────────────────────────────────────────

def test_it_is_not_ready_without_a_key():
    assert not _pub(api_key="").ready()


def test_it_is_not_ready_without_an_account():
    """A key with no account id reaches Zernio and is told which account
    it forgot, one clip at a time."""
    assert not _pub(accounts={}).ready()


def test_it_is_ready_with_both():
    assert _pub().ready()


def test_the_key_is_read_from_the_environment_first(monkeypatch):
    """config.json gets copied around, pasted into chat and
    screenshotted. .env does not."""
    monkeypatch.setenv("ZERNIO_API_KEY", "sk_from_env")
    assert _pub(api_key="sk_in_config").token() == "sk_from_env"


def test_a_missing_key_names_the_command_to_fix_it():
    with pytest.raises(NotConfigured) as caught:
        _pub(api_key="").post_clip("/c/a.mp4", "hi")
    assert "--set-env" in str(caught.value)


def test_a_missing_account_names_the_command_to_fix_it():
    with pytest.raises(NotConfigured) as caught:
        _pub(accounts={}).post_clip("/c/a.mp4", "hi")
    assert "--setup-zernio" in str(caught.value)


def test_a_missing_file_is_not_an_upload(clip):
    with pytest.raises(NotConfigured):
        _pub().post_clip("/no/such/clip.mp4", "hi")


# ── the three calls ──────────────────────────────────────────────────

def test_the_clip_is_uploaded_then_posted(clip, monkeypatch):
    calls = []

    def fake(method, url, token="", payload=None, raw=b"", content_type=""):
        calls.append((method, url, payload, len(raw)))
        if url.endswith("/v1/media/presign"):
            return {"uploadUrl": "https://store/put", "publicUrl": "https://cdn/x.mp4"}
        if url == "https://store/put":
            return {}
        return {"platformPostUrl": "https://x.com/i/status/1"}

    monkeypatch.setattr("publishers.zernio._request", fake)
    assert _pub().post_clip(clip, "hello") == "https://x.com/i/status/1"

    methods = [c[0] for c in calls]
    assert methods == ["POST", "PUT", "POST"]
    assert calls[1][3] == len(b"video bytes"), "the file bytes were not sent"


def test_the_presigned_put_carries_no_bearer_token(clip, monkeypatch):
    """The signature IS the auth. Sending a bearer token to cloud storage
    is how a presigned PUT gets rejected."""
    seen = {}

    def fake(method, url, token="", payload=None, raw=b"", content_type=""):
        seen[url] = token
        if url.endswith("/presign"):
            return {"uploadUrl": "https://store/put", "publicUrl": "https://cdn/x.mp4"}
        return {"platformPostUrl": "https://x.com/1"}

    monkeypatch.setattr("publishers.zernio._request", fake)
    _pub().post_clip(clip, "hi")
    assert seen["https://store/put"] == ""


def test_a_tiktok_post_names_the_tiktok_account(clip, monkeypatch):
    body = {}

    def fake(method, url, token="", payload=None, raw=b"", content_type=""):
        if url.endswith("/presign"):
            return {"uploadUrl": "https://s/p", "publicUrl": "https://cdn/x.mp4"}
        if url.endswith("/v1/posts"):
            body.update(payload)
        return {}

    monkeypatch.setattr("publishers.zernio._request", fake)
    _pub("zernio_tiktok").post_clip(clip, "hi")
    assert body["platforms"] == [{"platform": "tiktok", "accountId": "acc_tt"}]


def test_the_post_names_the_configured_account(clip, monkeypatch):
    body = {}

    def fake(method, url, token="", payload=None, raw=b"", content_type=""):
        if url.endswith("/presign"):
            return {"uploadUrl": "https://s/p", "publicUrl": "https://cdn/x.mp4"}
        if url.endswith("/v1/posts"):
            body.update(payload)
        return {"platformPostUrl": "https://x.com/1"}

    monkeypatch.setattr("publishers.zernio._request", fake)
    _pub().post_clip(clip, "hi")
    assert body["platforms"] == [{"platform": "twitter", "accountId": "acc_1"}]
    assert body["mediaItems"][0]["url"] == "https://cdn/x.mp4"


def test_it_publishes_now_rather_than_scheduling(clip, monkeypatch):
    """The spacing that decides WHEN already lives in PublishGuard with
    every other platform's. A second scheduler would fight it."""
    body = {}

    def fake(method, url, token="", payload=None, raw=b"", content_type=""):
        if url.endswith("/presign"):
            return {"uploadUrl": "https://s/p", "publicUrl": "https://cdn/x.mp4"}
        if url.endswith("/v1/posts"):
            body.update(payload)
        return {}

    monkeypatch.setattr("publishers.zernio._request", fake)
    _pub().post_clip(clip, "hi")
    assert body["publishNow"] is True
    assert "scheduledFor" not in body


def test_the_caption_is_cut_to_what_x_accepts(clip, monkeypatch):
    body = {}

    def fake(method, url, token="", payload=None, raw=b"", content_type=""):
        if url.endswith("/presign"):
            return {"uploadUrl": "https://s/p", "publicUrl": "https://cdn/x.mp4"}
        if url.endswith("/v1/posts"):
            body.update(payload)
        return {}

    monkeypatch.setattr("publishers.zernio._request", fake)
    _pub().post_clip(clip, "x" * 500)
    assert len(body["content"]) == MAX_TWEET_CHARS


def test_a_dry_run_uploads_nothing(clip, monkeypatch):
    monkeypatch.setattr("publishers.zernio._request",
                        lambda *a, **k: pytest.fail("a dry run posted"))
    assert _pub().post_clip(clip, "hi", dry_run=True) == "dry-run"


# ── failure ──────────────────────────────────────────────────────────

def test_a_failure_returns_none_rather_than_raising(clip, monkeypatch):
    """The queue's ceiling, backoff and breaker decide what happens next
    - a publisher that retries inside itself makes its own caps
    meaningless."""
    def boom(*a, **k):
        raise ZernioError("HTTP 500: upstream")

    monkeypatch.setattr("publishers.zernio._request", boom)
    assert _pub().post_clip(clip, "hi") is None


def test_a_presign_with_no_url_is_an_error(clip, monkeypatch):
    monkeypatch.setattr("publishers.zernio._request",
                        lambda *a, **k: {"expiresIn": 3600})
    assert _pub().post_clip(clip, "hi") is None


def test_an_oversized_file_is_refused_before_uploading(tmp_path, monkeypatch):
    big = tmp_path / "huge.mp4"
    big.write_bytes(b"x")
    monkeypatch.setattr(os.path, "getsize", lambda p: MAX_UPLOAD_BYTES + 1)
    monkeypatch.setattr("publishers.zernio._request",
                        lambda *a, **k: pytest.fail("uploaded an oversized file"))
    assert _pub().post_clip(str(big), "hi") is None


def test_a_post_with_no_link_back_still_counts_as_posted(clip, monkeypatch):
    """The post WAS made. Reading that as a failure would have the queue
    retry it and post it twice."""
    def fake(method, url, token="", payload=None, raw=b"", content_type=""):
        if url.endswith("/presign"):
            return {"uploadUrl": "https://s/p", "publicUrl": "https://cdn/x.mp4"}
        return {"post": {"_id": "abc"}, "message": "ok"}

    monkeypatch.setattr("publishers.zernio._request", fake)
    assert _pub().post_clip(clip, "hi")


# ── reading the reply ────────────────────────────────────────────────

def test_the_url_is_found_at_the_top_level():
    assert _post_url({"platformPostUrl": "https://x.com/1"}) == "https://x.com/1"


def test_the_url_is_found_inside_the_platform_list():
    answer = {"post": {"platforms": [{"platform": "twitter",
                                      "platformPostUrl": "https://x.com/2"}]}}
    assert _post_url(answer) == "https://x.com/2"


def test_no_url_anywhere_is_an_empty_string():
    assert _post_url({"post": {"_id": "x"}}) == ""
    assert _post_url(None) == ""


# ── wiring ───────────────────────────────────────────────────────────

def test_both_destinations_are_clip_platforms():
    from utils.clip_queue import CLEAN_TEXT_PLATFORMS, CLIP_PLATFORMS

    for destination in ("zernio_twitter", "zernio_tiktok"):
        assert destination in CLIP_PLATFORMS
        assert destination in CLEAN_TEXT_PLATFORMS, "both read the text"


def test_the_publisher_lookup_finds_each_destination():
    from utils.social_promoter import _publisher_for

    for destination in ("zernio_twitter", "zernio_tiktok"):
        found = _publisher_for(destination, {"zernio": {}})
        assert found is not None
        assert found.destination == destination


def test_each_destination_has_its_own_cap():
    """One shared cap would force X and TikTok to share a budget, and
    their rules are nothing alike."""
    raw = json.load(open(os.path.join(ROOT, "auto_uploader",
                                      "config.example.json"), encoding="utf-8"))
    platforms = raw["posting"]["platforms"]
    x = platforms["zernio_twitter"]
    tiktok = platforms["zernio_tiktok"]
    assert x["enabled"] is False and tiktok["enabled"] is False
    assert x["min_minutes_between"] >= 60
    assert tiktok["min_minutes_between"] > x["min_minutes_between"], (
        "TikTok's spam checks punish rapid near-identical posting hardest")
    assert tiktok["daily_cap"] < x["daily_cap"]


def test_reddit_is_not_posted_to_automatically():
    """Self-promotion is governed per subreddit, and an automated feed of
    one channel's clips is what most subreddits ban on sight."""
    from publishers.zernio import DESTINATIONS, NOT_AUTOMATED

    assert "reddit" in NOT_AUTOMATED
    assert "reddit" not in DESTINATIONS.values()


def test_each_destination_uses_its_own_account():
    """The same key reaches four accounts. Posting to whichever was
    written down first is not a thing to leave to chance."""
    assert _pub("zernio_twitter").account_id() == "acc_1"
    assert _pub("zernio_tiktok").account_id() == "acc_tt"


def test_the_caption_is_cut_to_each_platform_s_limit():
    assert _pub("zernio_twitter").char_limit() == MAX_TWEET_CHARS
    assert _pub("zernio_tiktok").char_limit() > MAX_TWEET_CHARS


def test_every_destination_shares_one_caption_block():
    from utils.clip_queue import caption_for

    config = {"zernio": {"caption_template": "{title} SHARED"}}
    for destination in ("zernio_twitter", "zernio_tiktok"):
        assert "SHARED" in caption_for(destination, "/c/Clip 01.mp4", "x", config)


def test_the_api_key_is_not_in_the_shipped_config():
    raw = json.load(open(os.path.join(ROOT, "auto_uploader",
                                      "config.example.json"), encoding="utf-8"))
    assert not raw["zernio"].get("api_key"), "the key belongs in .env"
