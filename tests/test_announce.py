"""
Announcing a finished upload to the public platforms.

The event already existed - `announce_upload` fires once per real upload,
never for a skip. What is tested here is that reaching Facebook/Instagram/X
through it did not become a way around the guard, and that a platform
which cannot carry a link post says so instead of reporting a phantom
success.

No network: every publisher is replaced with a recording double.
"""

from __future__ import annotations

import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
for _path in (_REPO, _UPLOADER):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from utils import social_promoter  # noqa: E402
from utils.social_promoter import (  # noqa: E402
    announce_to_platforms,
    announce_upload,
    build_message,
    primary_link,
)

UPLOADS = {"youtube": "https://youtu.be/abc123"}
BOTH = {"youtube": "https://youtu.be/abc123",
        "rumble": "https://rumble.com/v7x-damn.html"}


def make_posting(tmp_path, **platform_overrides):
    platforms = {
        "facebook": {"enabled": True, "daily_cap": 5, "min_minutes_between": 45},
        "instagram": {"enabled": True, "daily_cap": 5, "min_minutes_between": 45},
        "x": {"enabled": False, "daily_cap": 3, "min_minutes_between": 90},
    }
    platforms.update(platform_overrides)
    return {
        "enabled": True,
        "kill_switch_file": str(tmp_path / "STOP_POSTING"),
        "state_path": str(tmp_path / "posting_state.json"),
        "platforms": platforms,
        "circuit_breaker": {"consecutive_failures": 3},
    }


class FakePublisher:
    """Stands in for a real publisher; records what it was asked to post."""

    def __init__(self, supports_link_posts=True, succeeds=True):
        self.supports_link_posts = supports_link_posts
        self.succeeds = succeeds
        self.calls = []

    def post_link(self, message, link):
        self.calls.append((message, link))
        return self.succeeds


@pytest.fixture
def publishers(monkeypatch):
    """Swap in doubles; Instagram keeps its real no-link-posts constraint."""
    made = {"facebook": FakePublisher(),
            "instagram": FakePublisher(supports_link_posts=False)}
    monkeypatch.setattr(social_promoter, "_publisher_for",
                        lambda platform, config: made.get(platform))
    return made


@pytest.fixture
def no_x(monkeypatch):
    tweets = []
    monkeypatch.setattr(social_promoter, "_post_twitter", tweets.append)
    return tweets


# ═════════════════════════════════════════════════════════════════════════════
# The message and the link
# ═════════════════════════════════════════════════════════════════════════════

def test_primary_link_prefers_youtube():
    assert primary_link(BOTH) == BOTH["youtube"]


def test_primary_link_falls_back_to_rumble():
    assert primary_link({"rumble": "https://rumble.com/x"}) == "https://rumble.com/x"


def test_no_upload_means_no_link():
    assert primary_link({}) == ""


def test_message_carries_every_url_that_uploaded():
    message = build_message("DAMN", BOTH)
    assert BOTH["youtube"] in message and BOTH["rumble"] in message


# ═════════════════════════════════════════════════════════════════════════════
# Facebook: the one that can carry a link post
# ═════════════════════════════════════════════════════════════════════════════

def test_facebook_gets_the_announcement(tmp_path, publishers, no_x):
    posted = announce_to_platforms(make_posting(tmp_path), "DAMN", UPLOADS)
    assert "facebook" in posted
    message, link = publishers["facebook"].calls[0]
    assert link == UPLOADS["youtube"]
    assert "DAMN" in message


def test_a_successful_post_counts_against_the_cap(tmp_path, publishers, no_x):
    from publish_guard import PublishGuard

    posting = make_posting(tmp_path)
    announce_to_platforms(posting, "DAMN", UPLOADS)
    guard = PublishGuard(posting, posting["state_path"])
    assert guard.posts_in_window("facebook") == 1


def test_the_cap_actually_stops_a_second_announcement(tmp_path, publishers, no_x):
    posting = make_posting(tmp_path, facebook={
        "enabled": True, "daily_cap": 1, "min_minutes_between": 0})
    assert announce_to_platforms(posting, "one", UPLOADS) == ["facebook"]
    assert announce_to_platforms(posting, "two", UPLOADS) == []
    assert len(publishers["facebook"].calls) == 1


def test_spacing_stops_a_rapid_second_announcement(tmp_path, publishers, no_x):
    posting = make_posting(tmp_path)   # 45 min spacing
    announce_to_platforms(posting, "one", UPLOADS)
    assert announce_to_platforms(posting, "two", UPLOADS) == []


def test_a_failed_post_is_recorded_as_a_failure(tmp_path, monkeypatch, no_x):
    from publish_guard import PublishGuard

    failing = FakePublisher(succeeds=False)
    monkeypatch.setattr(social_promoter, "_publisher_for",
                        lambda platform, config:
                        failing if platform == "facebook" else None)
    posting = make_posting(tmp_path, instagram={"enabled": False})
    assert announce_to_platforms(posting, "DAMN", UPLOADS) == []
    guard = PublishGuard(posting, posting["state_path"])
    assert guard.consecutive_failures("facebook") == 1
    assert guard.posts_in_window("facebook") == 0, \
        "a failed post must not consume the daily cap"


def test_repeated_failures_open_the_breaker(tmp_path, monkeypatch, no_x):
    failing = FakePublisher(succeeds=False)
    monkeypatch.setattr(social_promoter, "_publisher_for",
                        lambda platform, config:
                        failing if platform == "facebook" else None)
    posting = make_posting(tmp_path, instagram={"enabled": False},
                           facebook={"enabled": True, "daily_cap": 9,
                                     "min_minutes_between": 0})
    for _ in range(3):
        announce_to_platforms(posting, "DAMN", UPLOADS)
    before = len(failing.calls)
    announce_to_platforms(posting, "DAMN", UPLOADS)
    assert len(failing.calls) == before, "breaker did not stop the next attempt"


# ═════════════════════════════════════════════════════════════════════════════
# Instagram: cannot carry a link post at all
# ═════════════════════════════════════════════════════════════════════════════

def test_instagram_is_skipped_for_link_announcements(tmp_path, publishers, no_x):
    """Every IG publish call needs a media container with a hosted URL;
    there is no text/link endpoint to fall back to."""
    posted = announce_to_platforms(make_posting(tmp_path), "DAMN", UPLOADS)
    assert "instagram" not in posted
    assert publishers["instagram"].calls == []


def test_skipping_instagram_does_not_touch_its_cap(tmp_path, publishers, no_x):
    """A post that cannot happen must not look like one that did."""
    from publish_guard import PublishGuard

    posting = make_posting(tmp_path)
    announce_to_platforms(posting, "DAMN", UPLOADS)
    guard = PublishGuard(posting, posting["state_path"])
    assert guard.posts_in_window("instagram") == 0
    assert guard.consecutive_failures("instagram") == 0


def test_the_real_instagram_publisher_declares_the_limitation():
    from auto_uploader.publishers.instagram import InstagramPublisher
    assert InstagramPublisher.supports_link_posts is False


def test_the_real_facebook_publisher_can_do_link_posts():
    from auto_uploader.publishers.facebook import FacebookPublisher
    assert FacebookPublisher.supports_link_posts is True


def test_facebook_refuses_a_link_post_with_no_link(monkeypatch):
    monkeypatch.setenv("FB_PAGE_TOKEN", "token")
    monkeypatch.setenv("FB_PAGE_ID", "123")
    from auto_uploader.publishers.facebook import FacebookPublisher
    assert FacebookPublisher({}).post_link("hi", "") is False


# ═════════════════════════════════════════════════════════════════════════════
# X: disabled, but the path is wired
# ═════════════════════════════════════════════════════════════════════════════

def test_x_is_skipped_while_disabled(tmp_path, publishers, no_x):
    announce_to_platforms(make_posting(tmp_path), "DAMN", UPLOADS)
    assert no_x == [], "X is disabled in config and must not be posted to"


def test_x_posts_once_enabled(tmp_path, publishers, no_x):
    """The 401 is a credential problem, not a missing code path."""
    posting = make_posting(tmp_path, x={"enabled": True, "daily_cap": 3,
                                        "min_minutes_between": 90})
    posted = announce_to_platforms(posting, "DAMN", UPLOADS)
    assert "x" in posted
    assert UPLOADS["youtube"] in no_x[0]


def test_x_is_still_capped_once_enabled(tmp_path, publishers, no_x):
    posting = make_posting(tmp_path, x={"enabled": True, "daily_cap": 1,
                                        "min_minutes_between": 0})
    announce_to_platforms(posting, "one", UPLOADS)
    announce_to_platforms(posting, "two", UPLOADS)
    assert len(no_x) == 1


def test_a_missing_tweepy_is_reported_not_crashed_on(tmp_path, publishers, monkeypatch):
    def boom(message):
        raise ImportError("No module named 'tweepy'")

    monkeypatch.setattr(social_promoter, "_post_twitter", boom)
    posting = make_posting(tmp_path, x={"enabled": True, "daily_cap": 3,
                                        "min_minutes_between": 0})
    assert "x" not in announce_to_platforms(posting, "DAMN", UPLOADS)


# ═════════════════════════════════════════════════════════════════════════════
# The kill switch still wins
# ═════════════════════════════════════════════════════════════════════════════

def test_kill_switch_file_stops_every_announcement(tmp_path, publishers, no_x):
    posting = make_posting(tmp_path)
    (tmp_path / "STOP_POSTING").write_text("halt")
    assert announce_to_platforms(posting, "DAMN", UPLOADS) == []
    assert publishers["facebook"].calls == []


def test_master_switch_off_stops_every_announcement(tmp_path, publishers, no_x):
    posting = make_posting(tmp_path)
    posting["enabled"] = False
    assert announce_to_platforms(posting, "DAMN", UPLOADS) == []


def test_dry_run_posts_nothing(tmp_path, publishers, no_x):
    posted = announce_to_platforms(make_posting(tmp_path), "DAMN", UPLOADS,
                                   dry_run=True)
    assert "facebook" in posted, "dry run should still report what it would do"
    assert publishers["facebook"].calls == [], "dry run must not post"


# ═════════════════════════════════════════════════════════════════════════════
# announce_upload: the existing event, extended
# ═════════════════════════════════════════════════════════════════════════════

def test_announce_upload_without_posting_config_is_unchanged(tmp_path, publishers, no_x):
    """Existing behaviour: Discord/Reddit only, nothing guarded."""
    posted = announce_upload({"enabled": True, "discord": False}, "DAMN", UPLOADS)
    assert posted == []
    assert publishers["facebook"].calls == []


def test_announce_upload_reaches_facebook_when_posting_is_configured(
        tmp_path, publishers, no_x):
    posted = announce_upload({"enabled": True, "discord": False}, "DAMN",
                             UPLOADS, posting=make_posting(tmp_path))
    assert "facebook" in posted


def test_a_skipped_upload_announces_nothing(tmp_path, publishers, no_x):
    """announce_upload is only ever handed uploads that happened this run,
    and an empty dict must stay silent."""
    assert announce_upload({"enabled": True}, "DAMN", {},
                           posting=make_posting(tmp_path)) == []
    assert publishers["facebook"].calls == []


def test_the_promoter_being_off_stops_everything(tmp_path, publishers, no_x):
    assert announce_upload({"enabled": False}, "DAMN", UPLOADS,
                           posting=make_posting(tmp_path)) == []


def test_legacy_twitter_flag_does_not_double_post(tmp_path, publishers, no_x):
    """features.twitter and posting.platforms.x are the same account; both
    firing would post twice, once outside the cap."""
    announce_upload({"enabled": True, "discord": False, "twitter": True},
                    "DAMN", UPLOADS,
                    posting=make_posting(tmp_path,
                                         x={"enabled": True, "daily_cap": 3,
                                            "min_minutes_between": 0}))
    assert len(no_x) == 1


def test_legacy_twitter_flag_still_works_without_posting_config(tmp_path, no_x):
    announce_upload({"enabled": True, "discord": False, "twitter": True},
                    "DAMN", UPLOADS)
    assert len(no_x) == 1


# ═════════════════════════════════════════════════════════════════════════════
# Manual-approval platforms: parked, not lost
# ═════════════════════════════════════════════════════════════════════════════

def test_a_manual_platform_gets_its_text_composed(tmp_path, publishers, no_x):
    """Not automating something must not mean losing the announcement."""
    queue = tmp_path / "manual_posts.txt"
    posting = make_posting(tmp_path, x={
        "enabled": False, "daily_cap": 3, "manual_approval_only": True})
    posting["manual_queue_path"] = str(queue)

    announce_to_platforms(posting, "DAMN", UPLOADS)
    written = queue.read_text()
    assert "x" in written and UPLOADS["youtube"] in written
    assert "DAMN" in written


def test_a_manual_platform_is_never_actually_posted_to(tmp_path, publishers, no_x):
    posting = make_posting(tmp_path, x={
        "enabled": True, "daily_cap": 3, "manual_approval_only": True})
    posted = announce_to_platforms(posting, "DAMN", UPLOADS)
    assert "x" not in posted
    assert no_x == [], "manual-approval means a human posts, not the bot"


def test_parking_does_not_consume_the_cap(tmp_path, publishers, no_x):
    from publish_guard import PublishGuard

    posting = make_posting(tmp_path, x={
        "enabled": True, "daily_cap": 3, "manual_approval_only": True})
    announce_to_platforms(posting, "DAMN", UPLOADS)
    guard = PublishGuard(posting, posting["state_path"])
    assert guard.posts_in_window("x") == 0
    assert guard.consecutive_failures("x") == 0


def test_a_capped_platform_is_skipped_not_parked(tmp_path, publishers, no_x):
    """Being out of quota is temporary; it is not a request for a human."""
    queue = tmp_path / "manual_posts.txt"
    posting = make_posting(tmp_path, facebook={
        "enabled": True, "daily_cap": 1, "min_minutes_between": 0})
    posting["manual_queue_path"] = str(queue)

    announce_to_platforms(posting, "one", UPLOADS)     # uses the cap
    announce_to_platforms(posting, "two", UPLOADS)     # blocked by it
    assert not queue.exists() or "facebook" not in queue.read_text()


def test_x_text_fits_in_a_tweet(tmp_path):
    from utils.social_promoter import manual_post_text

    long_title = "OH MY GOD " * 40
    text = manual_post_text("x", long_title, UPLOADS)
    # The link counts as a fixed 23 characters however long it really is.
    body = text.split("\n")[0]
    assert len(body) + 1 + 23 <= 280


def test_x_text_keeps_a_short_title_intact(tmp_path):
    from utils.social_promoter import manual_post_text

    text = manual_post_text("x", '"DAMN" 8/5/26 Stackswopo Stream', UPLOADS)
    assert '"DAMN" 8/5/26 Stackswopo Stream' in text
    assert UPLOADS["youtube"] in text


def test_facebook_group_is_not_parked(tmp_path, publishers, no_x):
    """There is no approved route AND nothing useful to hand a person -
    group posting was withdrawn, so a queued text would just be noise."""
    queue = tmp_path / "manual_posts.txt"
    posting = make_posting(tmp_path)
    posting["platforms"]["facebook_group"] = {
        "enabled": False, "manual_approval_only": True}
    posting["manual_queue_path"] = str(queue)

    announce_to_platforms(posting, "DAMN", UPLOADS)
    assert not queue.exists() or "facebook_group" not in queue.read_text()


def test_the_shipped_config_parks_x_rather_than_paying_for_it():
    import json

    with open(os.path.join(_UPLOADER, "config.json")) as f:
        shipped = json.load(f)
    x = shipped["posting"]["platforms"]["x"]
    assert x["manual_approval_only"] is True
    assert x["enabled"] is False
    assert shipped["posting"].get("manual_queue_path"), \
        "parked posts need somewhere to land"
