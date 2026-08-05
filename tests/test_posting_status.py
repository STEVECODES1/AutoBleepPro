"""
The pre-flight check for social posting.

Its whole job is to be trustworthy when nothing is configured yet, so
these tests are mostly about the empty and half-filled cases: a check
that reports "fine" on a missing token would send someone to enable
posting against an account that cannot post.

Nothing here touches the network. `verify()` is only exercised on
platforms whose credentials are absent, which short-circuits before any
request is made - a test that made a live Graph API call would fail in CI
for reasons that have nothing to do with this code.
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

from utils.posting_status import (  # noqa: E402
    MISSING,
    OK,
    REQUIRED_ENV,
    missing_env,
    report,
    verify,
)
from publish_guard import PublishGuard  # noqa: E402

_ALL_POSTING_VARS = (
    "IG_PAGE_TOKEN", "IG_BUSINESS_ACCOUNT_ID",
    "FB_PAGE_TOKEN", "FB_PAGE_ID",
    "TWITTER_API_KEY", "TWITTER_API_SECRET",
    "TWITTER_ACCESS_TOKEN", "TWITTER_ACCESS_SECRET",
)


@pytest.fixture
def clean_env(monkeypatch):
    for name in _ALL_POSTING_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith("REDDIT_"):
            monkeypatch.delenv(name, raising=False)
    return monkeypatch


def make_posting(tmp_path, enabled=False):
    return {
        "enabled": enabled,
        "kill_switch_file": str(tmp_path / "STOP_POSTING"),
        "state_path": str(tmp_path / "posting_state.json"),
        "platforms": {
            "instagram": {"enabled": False, "daily_cap": 5},
            "facebook": {"enabled": False, "daily_cap": 5},
            "x": {"enabled": False, "daily_cap": 3},
            "reddit": {"enabled": False, "daily_cap": 1,
                       "manual_approval_only": True},
            "facebook_group": {"enabled": False, "manual_approval_only": True},
        },
        "circuit_breaker": {"consecutive_failures": 3},
    }


# ═════════════════════════════════════════════════════════════════════════════
# Which variables each platform needs
# ═════════════════════════════════════════════════════════════════════════════

def test_nothing_configured_reports_everything_missing(clean_env):
    for platform in ("instagram", "facebook", "x"):
        assert missing_env(platform) == list(REQUIRED_ENV[platform])


def test_a_fully_configured_platform_reports_nothing_missing(clean_env):
    for name in REQUIRED_ENV["facebook"]:
        clean_env.setenv(name, "value")
    assert missing_env("facebook") == []


def test_a_half_configured_platform_names_only_the_gap(clean_env):
    """"Nearly configured" is not configured - and the message has to say
    which half is missing or it sends you looking in the wrong place."""
    clean_env.setenv("IG_PAGE_TOKEN", "token")
    assert missing_env("instagram") == ["IG_BUSINESS_ACCOUNT_ID"]


def test_a_blank_variable_counts_as_missing(clean_env):
    """.env files are full of `KEY=` lines; present-but-empty is not set."""
    clean_env.setenv("FB_PAGE_TOKEN", "   ")
    clean_env.setenv("FB_PAGE_ID", "123")
    assert missing_env("facebook") == ["FB_PAGE_TOKEN"]


def test_reddit_checks_the_named_account_and_the_subreddit(clean_env):
    for field in ("CLIENT_ID", "CLIENT_SECRET", "USERNAME", "PASSWORD"):
        clean_env.setenv(f"REDDIT_{field}_2", "x")
    assert missing_env("reddit", "2") == ["REDDIT_SUBREDDIT"]
    clean_env.setenv("REDDIT_SUBREDDIT", "stackswopo")
    assert missing_env("reddit", "2") == []


def test_reddit_primary_credentials_do_not_satisfy_account_two(clean_env):
    for field in ("CLIENT_ID", "CLIENT_SECRET", "USERNAME", "PASSWORD"):
        clean_env.setenv(f"REDDIT_{field}", "primary")
    clean_env.setenv("REDDIT_SUBREDDIT", "stackswopo")
    assert missing_env("reddit", "2") != []


# ═════════════════════════════════════════════════════════════════════════════
# verify() stops before the network when credentials are absent
# ═════════════════════════════════════════════════════════════════════════════

def test_verify_reports_missing_without_calling_out(clean_env):
    checks = verify(["instagram", "facebook", "x", "reddit"], "2")
    assert {c.platform for c in checks} == {"instagram", "facebook", "x", "reddit"}
    assert all(c.state == MISSING for c in checks), \
        "a check with no credentials must not attempt a request"
    assert all(c.state != OK for c in checks)


def test_verify_detail_names_the_variables_to_fill_in(clean_env):
    check = verify(["instagram"])[0]
    assert "IG_PAGE_TOKEN" in check.detail


def test_an_unknown_platform_is_skipped_not_passed(clean_env):
    check = verify(["tiktok"])[0]
    assert check.state != OK


# ═════════════════════════════════════════════════════════════════════════════
# The printed report
# ═════════════════════════════════════════════════════════════════════════════

def test_report_runs_with_nothing_configured(clean_env, tmp_path, capsys):
    posting = make_posting(tmp_path)
    guard = PublishGuard(posting, posting["state_path"])
    report({"posting": posting}, guard, "2", live=False)

    out = capsys.readouterr().out
    assert "POSTING STATUS" in out
    assert "posting.enabled is false" in out
    for platform in ("instagram", "facebook", "x", "reddit"):
        assert platform in out


def test_report_never_prints_a_credential(clean_env, tmp_path, capsys):
    """The whole point is to be safe to paste into a chat."""
    secret = "SUPERSECRETTOKEN123"
    clean_env.setenv("FB_PAGE_TOKEN", secret)
    clean_env.setenv("FB_PAGE_ID", "999")
    posting = make_posting(tmp_path)
    guard = PublishGuard(posting, posting["state_path"])
    report({"posting": posting}, guard, "2", live=False)
    assert secret not in capsys.readouterr().out


def test_report_marks_facebook_group_as_permanently_manual(clean_env, tmp_path, capsys):
    posting = make_posting(tmp_path)
    guard = PublishGuard(posting, posting["state_path"])
    report({"posting": posting}, guard, "2", live=False)
    out = capsys.readouterr().out
    assert "no approved API route" in out


def test_report_shows_the_guard_would_block_everything_while_off(
        clean_env, tmp_path, capsys):
    posting = make_posting(tmp_path, enabled=False)
    guard = PublishGuard(posting, posting["state_path"])
    report({"posting": posting}, guard, "2", live=False)
    out = capsys.readouterr().out
    assert "ALLOW" not in out, "nothing may be allowed while posting is off"


def test_report_reflects_the_master_switch_being_on(clean_env, tmp_path, capsys):
    posting = make_posting(tmp_path, enabled=True)
    posting["platforms"]["instagram"]["enabled"] = True
    guard = PublishGuard(posting, posting["state_path"])
    report({"posting": posting}, guard, "2", live=False)
    out = capsys.readouterr().out
    assert "ALLOW  instagram" in out
    assert "BLOCK  reddit" in out, "reddit still ships manual-approval only"


# ═════════════════════════════════════════════════════════════════════════════
# Config plumbing - the posting block used to be dropped entirely
# ═════════════════════════════════════════════════════════════════════════════

def test_load_config_keeps_the_posting_block():
    from utils.config import load_config

    cfg = load_config(os.path.join(_UPLOADER, "config.json"),
                      os.path.join(_UPLOADER, ".env"))
    assert cfg.posting, "posting block was dropped on load"
    assert "platforms" in cfg.posting
    assert cfg.posting["enabled"] is False


def test_posting_paths_are_absolute_so_state_follows_the_config():
    """A relative state path resolves against the working directory, so
    running main.py from elsewhere would read an empty cap history - and
    an empty history permits a full day's burst."""
    from utils.config import load_config

    cfg = load_config(os.path.join(_UPLOADER, "config.json"),
                      os.path.join(_UPLOADER, ".env"))
    for key in ("state_path", "queue_path", "kill_switch_file"):
        assert os.path.isabs(cfg.posting[key]), f"{key} is not absolute"


def test_load_config_keeps_the_clips_block():
    from autoreel.crop_strategy import CROP_CENTER, resolve_crop_strategy
    from utils.config import load_config

    cfg = load_config(os.path.join(_UPLOADER, "config.json"),
                      os.path.join(_UPLOADER, ".env"))
    assert resolve_crop_strategy({"clips": cfg.clips}) == CROP_CENTER
