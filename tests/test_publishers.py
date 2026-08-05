"""
Publisher modules: credential gating and the crop default.

No network calls happen here - every test either stops before the first
request or asserts that it stopped. What is actually being checked is
that a publisher with nothing configured refuses to act, because the
alternative is a half-configured module posting to a real account.
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

_REDDIT_VARS = (
    "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET",
    "REDDIT_USERNAME", "REDDIT_PASSWORD", "REDDIT_SUBREDDIT",
)


@pytest.fixture
def clean_reddit_env(monkeypatch):
    """No Reddit variables of any account, so each test sets its own."""
    for name in list(os.environ):
        if name.startswith("REDDIT_"):
            monkeypatch.delenv(name, raising=False)
    return monkeypatch


# ═════════════════════════════════════════════════════════════════════════════
# Instagram / Facebook - disabled until credentials exist
# ═════════════════════════════════════════════════════════════════════════════

def test_instagram_blocked_without_credentials(monkeypatch):
    monkeypatch.delenv("IG_PAGE_TOKEN", raising=False)
    monkeypatch.delenv("IG_BUSINESS_ACCOUNT_ID", raising=False)
    from auto_uploader.publishers.instagram import InstagramPublisher
    assert InstagramPublisher({}).post_reel("https://example.com/clip.mp4") is False


def test_instagram_blocked_with_partial_credentials(monkeypatch):
    """A token with no account id is not "nearly configured" - it's off."""
    monkeypatch.setenv("IG_PAGE_TOKEN", "token123")
    monkeypatch.delenv("IG_BUSINESS_ACCOUNT_ID", raising=False)
    from auto_uploader.publishers.instagram import InstagramPublisher
    assert InstagramPublisher({}).post_reel("https://example.com/clip.mp4") is False


def test_facebook_blocked_without_credentials(monkeypatch):
    monkeypatch.delenv("FB_PAGE_TOKEN", raising=False)
    monkeypatch.delenv("FB_PAGE_ID", raising=False)
    from auto_uploader.publishers.facebook import FacebookPublisher
    assert FacebookPublisher({}).post_reel("https://example.com/clip.mp4") is False


def test_facebook_blocked_with_partial_credentials(monkeypatch):
    monkeypatch.setenv("FB_PAGE_TOKEN", "token123")
    monkeypatch.delenv("FB_PAGE_ID", raising=False)
    from auto_uploader.publishers.facebook import FacebookPublisher
    assert FacebookPublisher({}).post_reel("https://example.com/clip.mp4") is False


# ═════════════════════════════════════════════════════════════════════════════
# Facebook Groups stay manual, in code
# ═════════════════════════════════════════════════════════════════════════════

def _group_config(tmp_path, **group):
    settings = {"enabled": True, "daily_cap": 5, "manual_approval_only": True}
    settings.update(group)
    return {
        "posting": {
            "enabled": True,
            "kill_switch_file": str(tmp_path / "STOP_POSTING"),
            "platforms": {"facebook_group": settings},
            "circuit_breaker": {"consecutive_failures": 3},
        }
    }


def test_facebook_group_manual_only_regardless_of_config(tmp_path):
    from auto_uploader.publish_guard import PublishGuard
    guard = PublishGuard(_group_config(tmp_path), str(tmp_path / "state.json"))
    ok, reason = guard.can_post("facebook_group")
    assert not ok and "manual-approval" in reason


def test_facebook_group_cannot_be_unlocked_by_clearing_the_flag(tmp_path):
    """Group publishing was withdrawn from the Graph API - there is no
    compliant route, so the block is in code, not in config."""
    from auto_uploader.publish_guard import PublishGuard
    config = _group_config(tmp_path, manual_approval_only=False, daily_cap=100)
    guard = PublishGuard(config, str(tmp_path / "state.json"))
    ok, reason = guard.can_post("facebook_group")
    assert not ok and "manual-approval" in reason


# ═════════════════════════════════════════════════════════════════════════════
# Reddit - a separate, named account
# ═════════════════════════════════════════════════════════════════════════════

def test_reddit_uses_separate_account_env_vars(clean_reddit_env):
    """Must read the account-2 vars, never the primary REDDIT_* ones."""
    clean_reddit_env.setenv("REDDIT_CLIENT_ID", "PRIMARY_ACCOUNT")
    clean_reddit_env.setenv("REDDIT_CLIENT_SECRET", "PRIMARY_SECRET")
    clean_reddit_env.setenv("REDDIT_USERNAME", "primary_user")
    clean_reddit_env.setenv("REDDIT_PASSWORD", "primary_pass")
    clean_reddit_env.setenv("REDDIT_SUBREDDIT", "stackswopo")

    from auto_uploader.publishers.reddit import RedditPublisher
    pub = RedditPublisher({})
    assert pub._ready() is False, "primary credentials must not satisfy account 2"
    assert pub.post_link("Test", "https://example.com") is False


def test_reddit_ready_with_correct_2_creds(clean_reddit_env):
    clean_reddit_env.setenv("REDDIT_CLIENT_ID_2", "id2")
    clean_reddit_env.setenv("REDDIT_CLIENT_SECRET_2", "secret2")
    clean_reddit_env.setenv("REDDIT_USERNAME_2", "user2")
    clean_reddit_env.setenv("REDDIT_PASSWORD_2", "pass2")
    clean_reddit_env.setenv("REDDIT_SUBREDDIT", "stackswopo")

    from auto_uploader.publishers.reddit import RedditPublisher
    assert RedditPublisher({})._ready() is True


def test_reddit_readiness_does_not_depend_on_praw_being_installed(clean_reddit_env):
    """"pip install praw" and "fill in your .env" are different problems;
    reporting one as the other sent people to fix the wrong thing."""
    clean_reddit_env.setenv("REDDIT_CLIENT_ID_2", "id2")
    clean_reddit_env.setenv("REDDIT_CLIENT_SECRET_2", "secret2")
    clean_reddit_env.setenv("REDDIT_USERNAME_2", "user2")
    clean_reddit_env.setenv("REDDIT_PASSWORD_2", "pass2")
    clean_reddit_env.setenv("REDDIT_SUBREDDIT", "stackswopo")

    from auto_uploader.publishers.reddit import RedditPublisher
    pub = RedditPublisher({})
    assert pub._ready() is True
    assert pub._missing_credentials() == []


def test_reddit_needs_a_subreddit(clean_reddit_env):
    for field in ("CLIENT_ID", "CLIENT_SECRET", "USERNAME", "PASSWORD"):
        clean_reddit_env.setenv(f"REDDIT_{field}_2", "x")
    from auto_uploader.publishers.reddit import RedditPublisher
    assert RedditPublisher({})._ready() is False


def test_reddit_account_name_comes_from_config(clean_reddit_env):
    """A third account needs no code change - only config plus .env."""
    clean_reddit_env.setenv("REDDIT_CLIENT_ID_3", "id3")
    clean_reddit_env.setenv("REDDIT_CLIENT_SECRET_3", "secret3")
    clean_reddit_env.setenv("REDDIT_USERNAME_3", "user3")
    clean_reddit_env.setenv("REDDIT_PASSWORD_3", "pass3")
    clean_reddit_env.setenv("REDDIT_SUBREDDIT", "stackswopo")

    from auto_uploader.publishers.reddit import RedditPublisher
    cfg = {"features": {"social_promoter": {"reddit_account": "3"}}}
    assert RedditPublisher(cfg)._ready() is True
    assert RedditPublisher({})._ready() is False, "account 2 is still unconfigured"


def test_reddit_credentials_name_the_variable_they_wanted(clean_reddit_env):
    from auto_uploader.utils.social_promoter import reddit_credentials
    with pytest.raises(KeyError) as excinfo:
        reddit_credentials("2")
    assert "REDDIT_CLIENT_ID_2" in str(excinfo.value)


def test_reddit_credentials_read_a_named_account(clean_reddit_env):
    from auto_uploader.utils.social_promoter import reddit_credentials
    for field in ("CLIENT_ID", "CLIENT_SECRET", "USERNAME", "PASSWORD"):
        clean_reddit_env.setenv(f"REDDIT_{field}_ALT", f"alt-{field.lower()}")
    creds = reddit_credentials("ALT")
    assert creds["username"] == "alt-username"


def test_reddit_credentials_accept_the_prefix_layout_too(clean_reddit_env):
    """REDDIT_ALT_CLIENT_ID reads as naturally as REDDIT_CLIENT_ID_ALT."""
    from auto_uploader.utils.social_promoter import reddit_credentials
    for field in ("CLIENT_ID", "CLIENT_SECRET", "USERNAME", "PASSWORD"):
        clean_reddit_env.setenv(f"REDDIT_ALT_{field}", f"alt-{field.lower()}")
    assert reddit_credentials("ALT")["client_id"] == "alt-client_id"


def test_primary_reddit_credentials_are_not_used_for_a_named_account(clean_reddit_env):
    from auto_uploader.utils.social_promoter import reddit_credentials
    for field in ("CLIENT_ID", "CLIENT_SECRET", "USERNAME", "PASSWORD"):
        clean_reddit_env.setenv(f"REDDIT_{field}", "primary")
    with pytest.raises(KeyError):
        reddit_credentials("2")


# ═════════════════════════════════════════════════════════════════════════════
# Crop strategy - centre by default, for gameplay
# ═════════════════════════════════════════════════════════════════════════════

def test_gameplay_default_crop_is_center():
    """Face tracking on GTA locks onto NPC faces and the crop jitters
    around the scene, so centre is the default and face is opt-in."""
    from autoreel.crop_strategy import (
        CROP_CENTER, DEFAULT_CROP_STRATEGY, resolve_crop_strategy)
    assert DEFAULT_CROP_STRATEGY == CROP_CENTER
    assert resolve_crop_strategy({}) == CROP_CENTER
    assert resolve_crop_strategy(None) == CROP_CENTER
    assert resolve_crop_strategy({"clips": {}}, "gameplay") == CROP_CENTER


def test_face_tracking_is_off_by_default():
    from autoreel.crop_strategy import face_tracking_enabled
    assert face_tracking_enabled({}) is False
    assert face_tracking_enabled({"clips": {"crop_strategy": "auto"}}) is False


def test_face_tracking_is_available_when_asked_for():
    from autoreel.crop_strategy import CROP_FACE, face_tracking_enabled, resolve_crop_strategy
    config = {"clips": {"crop_strategy": "face"}}
    assert resolve_crop_strategy(config) == CROP_FACE
    assert face_tracking_enabled(config) is True


def test_facecam_content_may_default_to_face():
    from autoreel.crop_strategy import CROP_FACE, resolve_crop_strategy
    assert resolve_crop_strategy({"clips": {"crop_strategy": "auto"}},
                                 "facecam") == CROP_FACE


def test_unknown_content_kind_falls_back_to_center():
    """Centre is the option that cannot track the wrong thing."""
    from autoreel.crop_strategy import CROP_CENTER, resolve_crop_strategy
    assert resolve_crop_strategy({}, "some-new-format") == CROP_CENTER


def test_a_misspelled_strategy_is_an_error_not_a_silent_fallback(tmp_path):
    """"centre" quietly becoming something else is how a channel's clips
    end up cropped a way nobody chose."""
    from autoreel.crop_strategy import CropStrategyError, resolve_crop_strategy
    with pytest.raises(CropStrategyError):
        resolve_crop_strategy({"clips": {"crop_strategy": "centre"}})


def test_shipped_config_uses_center_for_gameplay():
    import json
    with open(os.path.join(_UPLOADER, "config.json")) as f:
        shipped = json.load(f)
    from autoreel.crop_strategy import CROP_CENTER, resolve_crop_strategy
    assert resolve_crop_strategy(shipped) == CROP_CENTER
    assert shipped["clips"]["content_kind"] == "gameplay"
