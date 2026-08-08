"""
Stop conditions for outbound posting.

This is the component whose failure mode is an account ban rather than a
crash, so the tests are written around "what must never be allowed"
rather than "what should work".

Both calling conventions are exercised - check()/record_post() with an
injectable clock, and can_post()/record_result() as the publishers use
them - because there is one implementation behind both and a divergence
between them would be a hole in the guard.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
for _path in (_REPO, _UPLOADER):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from auto_uploader.publish_guard import (  # noqa: E402
    ALWAYS_MANUAL,
    WINDOW_SECONDS,
    PublishGuard,
    engage_kill_switch,
    release_kill_switch,
)

NOW = 1_770_000_000.0


def make_config(tmp_path, **overrides):
    """The `posting` block on its own - the guard accepts either shape."""
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
# Config shape - the whole app config or just its posting block
# ═════════════════════════════════════════════════════════════════════════════

def test_accepts_a_nested_app_config(tmp_path):
    """publishers/ pass the whole config; tests pass the posting block."""
    nested = {"posting": make_config(tmp_path), "youtube": {"channel": "x"}}
    guard = PublishGuard(nested, str(tmp_path / "s.json"))
    assert guard.check("instagram", now=NOW).allowed


def test_positional_construction_matches_keyword(tmp_path):
    positional = PublishGuard(make_config(tmp_path), str(tmp_path / "a.json"))
    keyword = PublishGuard(config=make_config(tmp_path),
                           state_path=str(tmp_path / "b.json"))
    assert positional.check("instagram", now=NOW).allowed
    assert keyword.check("instagram", now=NOW).allowed


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
        assert "posting.enabled" in decision.reason


def test_kill_switch_file_stops_every_platform(guard, tmp_path):
    assert guard.check("instagram", now=NOW).allowed is True
    (tmp_path / "STOP_POSTING").write_text("halt")
    decision = guard.check("instagram", now=NOW)
    assert not decision and "kill switch file" in decision.reason


def test_kill_switch_beats_an_otherwise_fine_platform(guard):
    """No per-platform setting can re-enable posting."""
    engage_kill_switch(guard.config, note="testing")
    for platform in guard.config["platforms"]:
        assert not guard.check(platform, now=NOW)


def test_engage_and_release_round_trip(guard):
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


def test_can_post_agrees_with_check(guard, tmp_path):
    allowed, reason = guard.can_post("instagram")
    assert allowed is True and reason == ""

    (tmp_path / "STOP_POSTING").write_text("x")
    allowed, reason = guard.can_post("instagram")
    assert allowed is False and "kill switch" in reason


# ═════════════════════════════════════════════════════════════════════════════
# Manual-approval platforms are never automated
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("platform", sorted(ALWAYS_MANUAL))
def test_hardcoded_manual_platforms_are_never_allowed(guard, platform):
    """Facebook group publishing was withdrawn from the Graph API, so
    there is no compliant automated route and config must not offer one."""
    decision = guard.check(platform, now=NOW)
    assert not decision
    assert "manual-approval only" in decision.reason


def test_facebook_group_stays_manual_even_when_config_says_otherwise(tmp_path):
    config = make_config(tmp_path)
    config["platforms"]["facebook_group"] = {
        "enabled": True, "daily_cap": 50, "manual_approval_only": False}
    guard = PublishGuard(config=config, state_path=str(tmp_path / "s.json"))
    allowed, reason = guard.can_post("facebook_group")
    assert not allowed and "manual-approval" in reason


def test_reddit_is_manual_by_default(guard):
    """Ships parked: the caps are per-account reputation, not an API quota."""
    decision = guard.check("reddit", now=NOW)
    assert not decision and "manual-approval only" in decision.reason


def test_reddit_can_be_enabled_independently_of_any_locked_account(tmp_path):
    """Reddit has a supported API, so which account posts is a config
    decision - a problem with one account must not block the integration."""
    config = make_config(tmp_path)
    config["platforms"]["reddit"] = {
        "enabled": True, "daily_cap": 2, "min_minutes_between": 0,
        "manual_approval_only": False}
    guard = PublishGuard(config=config, state_path=str(tmp_path / "s.json"))
    assert guard.check("reddit", now=NOW).allowed


def test_enabled_reddit_is_still_capped(tmp_path):
    """Turning manual approval off must not turn the limits off with it."""
    config = make_config(tmp_path)
    config["platforms"]["reddit"] = {
        "enabled": True, "daily_cap": 1, "min_minutes_between": 720,
        "manual_approval_only": False}
    guard = PublishGuard(config=config, state_path=str(tmp_path / "s.json"))

    assert guard.check("reddit", now=NOW).allowed
    guard.record_post("reddit", now=NOW)
    decision = guard.check("reddit", now=NOW + 60)
    assert not decision and "cap reached" in decision.reason


def test_enabled_reddit_still_has_a_circuit_breaker(tmp_path):
    config = make_config(tmp_path)
    config["platforms"]["reddit"] = {
        "enabled": True, "daily_cap": 5, "manual_approval_only": False}
    guard = PublishGuard(config=config, state_path=str(tmp_path / "s.json"))
    for _ in range(3):
        guard.record_failure("reddit")
    decision = guard.check("reddit", now=NOW)
    assert not decision and "circuit breaker" in decision.reason


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


def test_unknown_platform_is_refused_distinctly(guard):
    """A typo'd platform name must not look like a deliberate off switch."""
    decision = guard.check("tiktok", now=NOW)
    assert not decision and "not configured" in decision.reason


def _shipped_posting():
    with open(os.path.join(_UPLOADER, "config.json")) as f:
        shipped = json.load(f)
    posting = shipped.get("posting")
    assert posting is not None, "posting block missing from config"
    return posting


# What each platform itself allows in 24h. A cap above this is not a
# policy choice, it is posts that will fail.
PLATFORM_CEILING = {
    "instagram": 50,     # Content Publishing API rejects the 51st
    "x": 16,             # free tier is ~500/month
    "reddit": 25,
    "facebook": 25,
    "facebook_group": 25,
}


def test_every_enabled_platform_has_a_real_cap():
    """Turning a platform on is a decision; turning the limits off with it
    is not. daily_cap 0 means unlimited in the guard, so an enabled
    platform that ships with 0 would post without bound - and would keep
    attempting past the platform's own limit, where the rejections are
    real failures that trip the circuit breaker."""
    for name, settings in _shipped_posting()["platforms"].items():
        if not settings.get("enabled"):
            continue
        cap = int(settings.get("daily_cap", 0) or 0)
        ceiling = PLATFORM_CEILING.get(name, 25)
        assert cap > 0, f"{name} is enabled with no daily cap"
        assert cap <= ceiling, \
            f"{name} cap of {cap} is above what {name} itself allows ({ceiling})"


# Instagram posts every clip the moment it is ready, deliberately - the
# owner asked for no waiting there. Listed by name so turning the spacing
# off somewhere else stays a decision rather than a drift.
UNSPACED_BY_CHOICE = {"instagram"}


def test_every_enabled_platform_spaces_its_posts():
    """Back-to-back posts are what a spam classifier is looking for."""
    for name, settings in _shipped_posting()["platforms"].items():
        if settings.get("enabled") and name not in UNSPACED_BY_CHOICE:
            assert float(settings.get("min_minutes_between", 0) or 0) > 0, \
                f"{name} is enabled with no spacing between posts"


def test_an_unspaced_platform_is_still_capped():
    """Spacing off and cap off together is unbounded posting."""
    for name in UNSPACED_BY_CHOICE:
        settings = _shipped_posting()["platforms"].get(name, {})
        if settings.get("enabled"):
            assert int(settings.get("daily_cap", 0) or 0) > 0, \
                f"{name} has neither spacing nor a cap"


def test_the_kill_switch_stays_configured():
    """The panic stop has to exist even after posting is switched on -
    especially then."""
    assert _shipped_posting().get("kill_switch_file"), \
        "no kill_switch_file: there would be no way to halt a running --watch"


def test_facebook_group_never_ships_enabled():
    settings = _shipped_posting()["platforms"]["facebook_group"]
    assert settings.get("enabled") is False
    assert settings.get("manual_approval_only") is True


def test_shipped_config_spaces_reddit_posts_out():
    """Reddit is automated now, so the cap and the gap between posts are
    the only things standing between this and a burst of self-promotion
    from one account - which is what gets a domain shadowbanned."""
    with open(os.path.join(_UPLOADER, "config.json")) as f:
        shipped = json.load(f)
    reddit = shipped["posting"]["platforms"]["reddit"]
    assert reddit["daily_cap"] <= 10
    assert reddit["min_minutes_between"] >= 60
    assert reddit["daily_cap"] * reddit["min_minutes_between"] >= 600, \
        "the day's posts must be spread over hours, not minutes"


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
    assert not guard.check("instagram", now=NOW + WINDOW_SECONDS - 10)
    # ...and one slot frees exactly when it does.
    assert guard.check("instagram", now=NOW + WINDOW_SECONDS + 1).allowed


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


def test_record_result_counts_against_the_cap(tmp_path):
    """The publisher-facing API must not be a way around the limits."""
    config = make_config(tmp_path)
    config["platforms"]["instagram"] = {"enabled": True, "daily_cap": 2,
                                        "min_minutes_between": 0}
    guard = PublishGuard(config=config, state_path=str(tmp_path / "s.json"))
    guard.record_result("instagram", success=True)
    guard.record_result("instagram", success=True)
    allowed, reason = guard.can_post("instagram")
    assert not allowed and "cap reached" in reason


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
    """Not a timer. Three failures running means something is wrong at the
    account level, and walking back into it on a schedule is itself the
    behaviour that gets an account flagged."""
    for _ in range(3):
        guard.record_failure("instagram")
    assert not guard.check("instagram", now=NOW)
    assert not guard.check("instagram", now=NOW + 86_400 * 7)
    guard.reset_failures("instagram")
    assert guard.check("instagram", now=NOW).allowed


def test_a_success_clears_the_failure_run(guard):
    guard.record_failure("instagram")
    guard.record_failure("instagram")
    assert guard.consecutive_failures("instagram") == 2
    guard.record_post("instagram", now=NOW)
    assert guard.consecutive_failures("instagram") == 0


def test_record_result_failure_opens_the_breaker(guard):
    for _ in range(3):
        guard.record_result("instagram", success=False)
    allowed, reason = guard.can_post("instagram")
    assert not allowed and "circuit breaker" in reason


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


def test_legacy_state_layout_is_absorbed_not_discarded(tmp_path):
    """Dropping the old records on upgrade would reset the caps to zero,
    which is exactly the burst this module exists to prevent."""
    state = tmp_path / "s.json"
    state.write_text(json.dumps({
        "instagram": {"posts": [NOW, NOW + 1, NOW + 2, NOW + 3, NOW + 4],
                      "last_post_ts": NOW + 4,
                      "consecutive_failures": 0,
                      "circuit_open": False, "circuit_open_since": 0},
    }))
    guard = PublishGuard(config=make_config(tmp_path), state_path=str(state))
    assert guard.posts_in_window("instagram", now=NOW + 60) == 5
    assert not guard.check("instagram", now=NOW + 60)


def test_legacy_failure_counts_are_absorbed(tmp_path):
    state = tmp_path / "s.json"
    state.write_text(json.dumps({
        "x": {"posts": [], "consecutive_failures": 3, "circuit_open": True},
    }))
    guard = PublishGuard(config=make_config(tmp_path), state_path=str(state))
    assert guard.consecutive_failures("x") == 3
    assert not guard.check("x", now=NOW)


def test_old_posts_outside_the_window_do_not_count(tmp_path):
    config = make_config(tmp_path)
    config["platforms"]["instagram"]["daily_cap"] = 1
    config["platforms"]["instagram"]["min_minutes_between"] = 0
    guard = PublishGuard(config=config, state_path=str(tmp_path / "s.json"))
    guard.record_post("instagram", now=NOW - WINDOW_SECONDS - 100)
    assert guard.check("instagram", now=NOW).allowed


def test_corrupt_state_file_does_not_crash(tmp_path):
    state = tmp_path / "s.json"
    state.write_text("{not json at all")
    guard = PublishGuard(config=make_config(tmp_path), state_path=str(state))
    assert guard.posts_in_window("instagram") == 0
    assert guard.check("instagram", now=NOW).allowed


def test_state_is_written_even_when_the_folder_does_not_exist(tmp_path):
    """A state file the guard cannot write is a cap it cannot enforce."""
    state = str(tmp_path / "nested" / "deeper" / "s.json")
    guard = PublishGuard(config=make_config(tmp_path), state_path=state)
    guard.record_post("instagram", now=NOW)
    assert os.path.exists(state)


def test_no_temp_files_are_left_behind(tmp_path):
    guard = PublishGuard(config=make_config(tmp_path),
                         state_path=str(tmp_path / "s.json"))
    for i in range(5):
        guard.record_post("instagram", now=NOW + i)
    assert [p for p in os.listdir(tmp_path) if p.endswith(".tmp")] == []


# ═════════════════════════════════════════════════════════════════════════════
# Reporting
# ═════════════════════════════════════════════════════════════════════════════

def test_status_covers_every_configured_platform(guard):
    rows = guard.status()
    assert {name for name, _, _ in rows} == set(guard.config["platforms"])
    by_name = {name: (ok, why) for name, ok, why in rows}
    assert by_name["facebook_group"][0] is False
    assert "manual" in by_name["facebook_group"][1]


# ═════════════════════════════════════════════════════════════════════════════
# Testing one clip by hand
#
# Spacing exists so an automated run does not fire a burst. A person
# running one command is not a burst - and being unable to check a single
# post for 100 minutes is how config gets edited in a hurry and left
# wrong.
# ═════════════════════════════════════════════════════════════════════════════

def test_spacing_can_be_waived_for_a_single_hand_run_post(guard):
    guard.record_post("instagram")
    assert not guard.check("instagram").allowed
    assert guard.check("instagram", ignore_spacing=True).allowed


def test_waiving_spacing_does_not_waive_the_daily_cap(tmp_path):
    """The cap is about how much goes out in a day, which a human being
    present does not change."""
    from publish_guard import PublishGuard

    config = {"enabled": True,
              "platforms": {"instagram": {"enabled": True, "daily_cap": 2,
                                          "min_minutes_between": 100}}}
    g = PublishGuard(config, str(tmp_path / "state.json"))
    g.record_post("instagram")
    g.record_post("instagram")
    decision = g.check("instagram", ignore_spacing=True)
    assert not decision.allowed
    assert "cap" in decision.reason


def test_waiving_spacing_does_not_waive_the_kill_switch(tmp_path):
    from publish_guard import PublishGuard

    config = {"enabled": True, "kill_switch_file": __file__,
              "platforms": {"instagram": {"enabled": True, "daily_cap": 5}}}
    g = PublishGuard(config, str(tmp_path / "state.json"))
    assert not g.check("instagram", ignore_spacing=True).allowed


def test_waiving_spacing_does_not_waive_the_circuit_breaker(tmp_path):
    from publish_guard import PublishGuard

    config = {"enabled": True,
              "circuit_breaker": {"consecutive_failures": 3},
              "platforms": {"instagram": {"enabled": True, "daily_cap": 5}}}
    g = PublishGuard(config, str(tmp_path / "state.json"))
    for _ in range(3):
        g.record_failure("instagram")
    assert not g.check("instagram", ignore_spacing=True).allowed


def test_spacing_still_applies_by_default(guard):
    """The waiver must be something you ask for, never the default - the
    automated path must never reach it."""
    guard.record_post("instagram")
    assert not guard.can_post("instagram")[0]
