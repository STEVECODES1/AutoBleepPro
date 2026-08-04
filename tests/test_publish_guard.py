"""
Stop conditions for outbound posting.

This is the component whose failure mode is an account ban rather than a
crash, so the tests are written around "what must never be allowed"
rather than "what should work".
"""

from __future__ import annotations

import json
import os
import sys

import pytest

_UPLOADER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto_uploader")
sys.path.insert(0, _UPLOADER)

from utils.publish_guard import (  # noqa: E402
    ALWAYS_MANUAL,
    WINDOW_SECONDS,
    PublishGuard,
    engage_kill_switch,
    release_kill_switch,
)

NOW = 1_770_000_000.0


def make_config(tmp_path, **overrides):
    config = {
        "enabled": True,
        "kill_switch_file": str(tmp_path / "STOP_POSTING"),
        "platforms": {
            "instagram": {"enabled": True, "daily_cap": 5, "min_minutes_between": 45},
            "facebook": {"enabled": True, "daily_cap": 5, "min_minutes_between": 45},
            "x": {"enabled": True, "daily_cap": 3, "min_minutes_between": 90},
            "reddit": {"enabled": True, "daily_cap": 1, "manual_approval_only": True},
            "facebook_group": {"enabled": True, "daily_cap": 1},
        },
        "circuit_breaker": {"consecutive_failures": 3},
    }
    config.update(overrides)
    return config


@pytest.fixture
def guard(tmp_path):
    return PublishGuard(config=make_config(tmp_path),
                        state_path=str(tmp_path / "posting_state.json"))


# ═════════════════════════════════════════════════════════════════════════════
# Kill switch - beats everything
# ═════════════════════════════════════════════════════════════════════════════

def test_config_flag_stops_every_platform(tmp_path):
    guard = PublishGuard(config=make_config(tmp_path, enabled=False),
                         state_path=str(tmp_path / "s.json"))
    for platform in ("instagram", "facebook", "x"):
        decision = guard.check(platform, now=NOW)
        assert not decision
        assert "KILL SWITCH" in decision.reason


def test_kill_switch_file_stops_every_platform(guard, tmp_path):
    assert guard.check("instagram", now=NOW).allowed is True
    (tmp_path / "STOP_POSTING").write_text("halt")
    decision = guard.check("instagram", now=NOW)
    assert not decision and "kill switch file" in decision.reason


def test_kill_switch_beats_an_otherwise_fine_platform(guard, tmp_path):
    """No per-platform setting can re-enable posting."""
    engage_kill_switch(guard.config, note="testing")
    for platform in guard.config["platforms"]:
        assert not guard.check(platform, now=NOW)


def test_engage_and_release_round_trip(guard, tmp_path):
    path = engage_kill_switch(guard.config)
    assert os.path.exists(path)
    assert not guard.check("instagram", now=NOW)

    assert release_kill_switch(guard.config) is True
    assert guard.check("instagram", now=NOW).allowed is True
    assert release_kill_switch(guard.config) is False   # already gone


def test_decision_is_falsy_when_blocked(guard, tmp_path):
    """Publishers write `if not guard.check(p): return` - that must work."""
    (tmp_path / "STOP_POSTING").write_text("x")
    assert not guard.check("instagram", now=NOW)
    assert bool(guard.check("instagram", now=NOW)) is False


# ═════════════════════════════════════════════════════════════════════════════
# Manual-approval platforms are never automated
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("platform", sorted(ALWAYS_MANUAL))
def test_hardcoded_manual_platforms_are_never_allowed(guard, platform):
    """Reddit's anti-spam is enforced by ban, and Facebook group publishing
    has no approved API route - so config must not be able to enable them."""
    decision = guard.check(platform, now=NOW)
    assert not decision
    assert "manual-approval only" in decision.reason


def test_config_cannot_enable_a_hardcoded_manual_platform(tmp_path):
    config = make_config(tmp_path)
    config["platforms"]["reddit"]["manual_approval_only"] = False
    config["platforms"]["reddit"]["enabled"] = True
    guard = PublishGuard(config=config, state_path=str(tmp_path / "s.json"))
    assert not guard.check("reddit", now=NOW)


def test_a_normal_platform_can_be_marked_manual(tmp_path):
    config = make_config(tmp_path)
    config["platforms"]["instagram"]["manual_approval_only"] = True
    guard = PublishGuard(config=config, state_path=str(tmp_path / "s.json"))
    assert not guard.check("instagram", now=NOW)


# ═════════════════════════════════════════════════════════════════════════════
# Per-platform enable
# ═════════════════════════════════════════════════════════════════════════════

def test_disabled_platform_is_refused(tmp_path):
    config = make_config(tmp_path)
    config["platforms"]["instagram"]["enabled"] = False
    guard = PublishGuard(config=config, state_path=str(tmp_path / "s.json"))
    decision = guard.check("instagram", now=NOW)
    assert not decision and "disabled" in decision.reason


def test_unknown_platform_is_refused(guard):
    decision = guard.check("tiktok", now=NOW)
    assert not decision and "not configured" in decision.reason


def test_shipped_config_has_posting_off_by_default():
    """Nothing may post until it is deliberately switched on."""
    with open(os.path.join(_UPLOADER, "config.json")) as f:
        shipped = json.load(f)
    posting = shipped.get("posting")
    assert posting is not None, "posting block missing from shipped config"
    assert posting["enabled"] is False
    for name, settings in posting["platforms"].items():
        assert settings.get("enabled") is False, f"{name} ships enabled"


# ═════════════════════════════════════════════════════════════════════════════
# Daily caps, over a rolling window
# ═════════════════════════════════════════════════════════════════════════════

def test_cap_blocks_the_post_after_the_limit(guard):
    for i in range(5):
        assert guard.check("instagram", now=NOW + i * 4000).allowed
        guard.record_post("instagram", now=NOW + i * 4000)
    decision = guard.check("instagram", now=NOW + 5 * 4000)
    assert not decision
    assert "cap reached" in decision.reason and "5/5" in decision.reason


def test_cap_is_per_platform(guard):
    for i in range(5):
        guard.record_post("instagram", now=NOW + i * 4000)
    assert not guard.check("instagram", now=NOW + 20_000)
    assert guard.check("facebook", now=NOW + 20_000).allowed


def test_window_rolls_rather_than_resetting_at_midnight(guard):
    """A calendar reset would permit 5 at 23:59 and 5 more at 00:01."""
    for i in range(5):
        guard.record_post("instagram", now=NOW + i * 60)
    assert not guard.check("instagram", now=NOW + 3600)

    # Still capped just before the oldest post ages out...
    just_before = NOW + WINDOW_SECONDS - 10
    assert not guard.check("instagram", now=just_before)
    # ...and one slot frees exactly when it does.
    just_after = NOW + WINDOW_SECONDS + 1
    assert guard.check("instagram", now=just_after).allowed


def test_blocked_by_cap_reports_when_to_retry(guard):
    for i in range(5):
        guard.record_post("instagram", now=NOW + i * 60)
    decision = guard.check("instagram", now=NOW + 3600)
    assert decision.retry_after_s is not None
    assert 0 < decision.retry_after_s <= WINDOW_SECONDS


def test_zero_cap_means_unlimited(tmp_path):
    config = make_config(tmp_path)
    config["platforms"]["instagram"]["daily_cap"] = 0
    config["platforms"]["instagram"]["min_minutes_between"] = 0
    guard = PublishGuard(config=config, state_path=str(tmp_path / "s.json"))
    for i in range(50):
        guard.record_post("instagram", now=NOW + i)
    assert guard.check("instagram", now=NOW + 100).allowed


# ═════════════════════════════════════════════════════════════════════════════
# Spacing - bursts look automated
# ═════════════════════════════════════════════════════════════════════════════

def test_minimum_spacing_between_posts(guard):
    guard.record_post("instagram", now=NOW)
    decision = guard.check("instagram", now=NOW + 10 * 60)
    assert not decision and "spacing" in decision.reason
    assert guard.check("instagram", now=NOW + 46 * 60).allowed


def test_spacing_reports_the_wait(guard):
    guard.record_post("x", now=NOW)
    decision = guard.check("x", now=NOW + 60)
    assert decision.retry_after_s == pytest.approx(90 * 60 - 60, abs=1)


def test_first_post_is_not_spacing_limited(guard):
    assert guard.check("x", now=NOW).allowed


# ═════════════════════════════════════════════════════════════════════════════
# Circuit breaker
# ═════════════════════════════════════════════════════════════════════════════

def test_consecutive_failures_open_the_breaker(guard):
    for _ in range(3):
        guard.record_failure("instagram")
    decision = guard.check("instagram", now=NOW)
    assert not decision and "circuit breaker" in decision.reason


def test_breaker_needs_a_deliberate_reset(guard):
    for _ in range(3):
        guard.record_failure("instagram")
    assert not guard.check("instagram", now=NOW)
    guard.reset_failures("instagram")
    assert guard.check("instagram", now=NOW).allowed


def test_a_success_clears_the_failure_run(guard):
    guard.record_failure("instagram")
    guard.record_failure("instagram")
    assert guard.consecutive_failures("instagram") == 2
    guard.record_post("instagram", now=NOW)
    assert guard.consecutive_failures("instagram") == 0


def test_breaker_is_per_platform(guard):
    for _ in range(3):
        guard.record_failure("instagram")
    assert not guard.check("instagram", now=NOW)
    assert guard.check("facebook", now=NOW).allowed


# ═════════════════════════════════════════════════════════════════════════════
# State survives restarts
# ═════════════════════════════════════════════════════════════════════════════

def test_counts_persist_across_instances(tmp_path):
    config = make_config(tmp_path)
    state = str(tmp_path / "s.json")
    first = PublishGuard(config=config, state_path=state)
    for i in range(5):
        first.record_post("instagram", now=NOW + i * 60)

    second = PublishGuard(config=config, state_path=state)
    assert second.posts_in_window("instagram", now=NOW + 3600) == 5
    assert not second.check("instagram", now=NOW + 3600), \
        "restarting the process must not reset the cap"


def test_failures_persist_across_instances(tmp_path):
    config = make_config(tmp_path)
    state = str(tmp_path / "s.json")
    first = PublishGuard(config=config, state_path=state)
    for _ in range(3):
        first.record_failure("x")
    second = PublishGuard(config=config, state_path=state)
    assert not second.check("x", now=NOW)


def test_corrupt_state_file_does_not_crash(tmp_path):
    state = tmp_path / "s.json"
    state.write_text("{not json at all")
    guard = PublishGuard(config=make_config(tmp_path), state_path=str(state))
    assert guard.posts_in_window("instagram") == 0
    assert guard.check("instagram", now=NOW).allowed


def test_old_timestamps_are_pruned(tmp_path):
    config = make_config(tmp_path)
    state = str(tmp_path / "s.json")
    guard = PublishGuard(config=config, state_path=state)
    for i in range(5):
        guard.record_post("instagram", now=NOW - WINDOW_SECONDS - 100 + i)
    assert guard.posts_in_window("instagram", now=NOW) == 0
    assert guard.check("instagram", now=NOW).allowed


# ═════════════════════════════════════════════════════════════════════════════
# Reporting
# ═════════════════════════════════════════════════════════════════════════════

def test_status_covers_every_configured_platform(guard):
    rows = guard.status()
    assert {name for name, _, _ in rows} == set(guard.config["platforms"])
    by_name = {name: (ok, why) for name, ok, why in rows}
    assert by_name["reddit"][0] is False
    assert "manual" in by_name["reddit"][1]
