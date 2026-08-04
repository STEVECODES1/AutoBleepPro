"""
tests/test_publishers.py — Unit tests for publisher modules.

All network calls are mocked. Tests verify:
  - credentials gate works (returns False when env vars missing)
  - Reddit uses _2 credentials, not the original REDDIT_* vars
  - Facebook group posting cannot be enabled via config
  - Instagram/Facebook stay disabled until credentials provided
  - face_tracking default is center (not face) for gameplay clips
"""
import os
import pytest
from unittest.mock import MagicMock, patch


# ── Instagram ─────────────────────────────────────────────────────────────────

def test_instagram_blocked_without_credentials(monkeypatch):
    monkeypatch.delenv("IG_PAGE_TOKEN", raising=False)
    monkeypatch.delenv("IG_BUSINESS_ACCOUNT_ID", raising=False)
    from auto_uploader.publishers.instagram import InstagramPublisher
    pub = InstagramPublisher({})
    result = pub.post_reel("https://example.com/clip.mp4")
    assert result is False


def test_instagram_blocked_with_partial_credentials(monkeypatch):
    monkeypatch.setenv("IG_PAGE_TOKEN", "token123")
    monkeypatch.delenv("IG_BUSINESS_ACCOUNT_ID", raising=False)
    from auto_uploader.publishers.instagram import InstagramPublisher
    pub = InstagramPublisher({})
    assert pub.post_reel("https://example.com/clip.mp4") is False


# ── Facebook ──────────────────────────────────────────────────────────────────

def test_facebook_blocked_without_credentials(monkeypatch):
    monkeypatch.delenv("FB_PAGE_TOKEN", raising=False)
    monkeypatch.delenv("FB_PAGE_ID", raising=False)
    from auto_uploader.publishers.facebook import FacebookPublisher
    pub = FacebookPublisher({})
    assert pub.post_reel("https://example.com/clip.mp4") is False


def test_facebook_group_manual_only_regardless_of_config(tmp_path):
    """Even if config sets facebook_group enabled=True, guard must block it."""
    from auto_uploader.publish_guard import PublishGuard
    cfg = {
        "posting": {
            "enabled": True,
            "kill_switch_file": str(tmp_path / "STOP_POSTING"),
            "platforms": {
                "facebook_group": {
                    "enabled": True,
                    "daily_cap": 5,
                    "manual_approval_only": True,
                }
            },
            "circuit_breaker": {"consecutive_failures": 3},
        }
    }
    g = PublishGuard(cfg, str(tmp_path / "state.json"))
    ok, reason = g.can_post("facebook_group")
    assert not ok
    assert "manual-approval" in reason


# ── Reddit ────────────────────────────────────────────────────────────────────

def test_reddit_uses_separate_account_env_vars(monkeypatch):
    """Reddit publisher must read _2 vars, never the original REDDIT_* vars."""
    # Set original vars to prove they are NOT used
    monkeypatch.setenv("REDDIT_CLIENT_ID", "LOCKED_ACCOUNT")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "LOCKED_SECRET")
    monkeypatch.setenv("REDDIT_USERNAME", "locked_user")
    monkeypatch.setenv("REDDIT_PASSWORD", "locked_pass")
    # Leave _2 vars unset → should fail with missing _2 vars, not use locked ones
    monkeypatch.delenv("REDDIT_CLIENT_ID_2", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET_2", raising=False)
    monkeypatch.delenv("REDDIT_USERNAME_2", raising=False)
    monkeypatch.delenv("REDDIT_PASSWORD_2", raising=False)
    monkeypatch.setenv("REDDIT_SUBREDDIT", "stackswopo")

    from auto_uploader.publishers.reddit import RedditPublisher
    pub = RedditPublisher({})
    result = pub.post_link("Test", "https://example.com")
    assert result is False  # blocked — _2 creds missing


def test_reddit_ready_with_correct_2_creds(monkeypatch):
    monkeypatch.setenv("REDDIT_CLIENT_ID_2", "id2")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET_2", "secret2")
    monkeypatch.setenv("REDDIT_USERNAME_2", "user2")
    monkeypatch.setenv("REDDIT_PASSWORD_2", "pass2")
    monkeypatch.setenv("REDDIT_SUBREDDIT", "stackswopo")
    from auto_uploader.publishers.reddit import RedditPublisher
    pub = RedditPublisher({})
    assert pub._ready() is True


# ── face tracking / gameplay crop default ─────────────────────────────────────

def test_face_tracking_off_by_default():
    """FaceTracker must not be enabled by default for gameplay clips."""
    try:
        from autoreel.face_tracking import FaceTracker
        # If the class has a default mode attr, it should be 'center'
        ft = FaceTracker()
        mode = getattr(ft, 'default_mode', None) or getattr(ft, 'mode', None)
        if mode is not None:
            assert mode == 'center', (
                f"FaceTracker default mode should be 'center' for gameplay, got '{mode}'"
            )
    except ImportError:
        pytest.skip("autoreel.face_tracking not available in this env")
