"""
tests/test_publish_guard.py
"""
import json
import time
import pytest
from pathlib import Path
from auto_uploader.publish_guard import PublishGuard


def _base_cfg(tmp_path, **platform_overrides):
    platforms = {
        "instagram": {"enabled": True,  "daily_cap": 5, "min_minutes_between": 0},
        "reddit":    {"enabled": False, "daily_cap": 1, "min_minutes_between": 0,
                      "manual_approval_only": True},
        "facebook_group": {"enabled": False, "daily_cap": 1,
                           "manual_approval_only": True},
        "x":         {"enabled": True,  "daily_cap": 3, "min_minutes_between": 0},
    }
    platforms.update(platform_overrides)
    return {
        "posting": {
            "enabled": True,
            "kill_switch_file": str(tmp_path / "STOP_POSTING"),
            "state_path": str(tmp_path / "state.json"),
            "queue_path":  str(tmp_path / "queue.json"),
            "platforms": platforms,
            "circuit_breaker": {"consecutive_failures": 3},
        }
    }


def _guard(tmp_path, **overrides):
    cfg = _base_cfg(tmp_path, **overrides)
    return PublishGuard(cfg, cfg["posting"]["state_path"])


# ── global kill switch ────────────────────────────────────────────────────────

def test_kill_switch_file_blocks(tmp_path):
    g = _guard(tmp_path)
    (tmp_path / "STOP_POSTING").write_text("stop")
    ok, reason = g.can_post("instagram")
    assert not ok
    assert "kill switch" in reason


def test_global_enabled_false_blocks(tmp_path):
    cfg = _base_cfg(tmp_path)
    cfg["posting"]["enabled"] = False
    g = PublishGuard(cfg, cfg["posting"]["state_path"])
    ok, reason = g.can_post("instagram")
    assert not ok
    assert "posting.enabled" in reason


# ── platform flags ────────────────────────────────────────────────────────────

def test_platform_disabled_blocks(tmp_path):
    g = _guard(tmp_path, instagram={"enabled": False, "daily_cap": 5,
                                     "min_minutes_between": 0})
    ok, reason = g.can_post("instagram")
    assert not ok
    assert "disabled" in reason


def test_manual_approval_only_blocks_reddit(tmp_path):
    g = _guard(tmp_path, reddit={"enabled": True, "daily_cap": 5,
                                  "min_minutes_between": 0,
                                  "manual_approval_only": True})
    ok, reason = g.can_post("reddit")
    assert not ok
    assert "manual-approval" in reason


def test_facebook_group_cannot_be_enabled(tmp_path):
    g = _guard(tmp_path,
               facebook_group={"enabled": True, "daily_cap": 1,
                               "manual_approval_only": True})
    ok, reason = g.can_post("facebook_group")
    assert not ok
    assert "manual-approval" in reason


# ── rolling 24h cap ───────────────────────────────────────────────────────────

def test_cap_blocks_when_full(tmp_path):
    g = _guard(tmp_path, instagram={"enabled": True, "daily_cap": 2,
                                     "min_minutes_between": 0})
    g.record_result("instagram", success=True)
    g.record_result("instagram", success=True)
    ok, reason = g.can_post("instagram")
    assert not ok
    assert "cap reached" in reason


def test_cap_allows_when_under(tmp_path):
    g = _guard(tmp_path)
    ok, _ = g.can_post("instagram")
    assert ok


def test_cap_rolls_over_after_24h(tmp_path):
    """Old posts outside the 24h window should not count against the cap."""
    g = _guard(tmp_path, instagram={"enabled": True, "daily_cap": 1,
                                     "min_minutes_between": 0})
    state_path = Path(tmp_path / "state.json")
    # Inject an old post (25 hours ago)
    old_ts = time.time() - 90000
    state_path.write_text(json.dumps(
        {"instagram": {"posts": [old_ts], "last_post_ts": old_ts,
                       "consecutive_failures": 0, "circuit_open": False,
                       "circuit_open_since": 0}}
    ))
    ok, _ = g.can_post("instagram")
    assert ok


# ── minimum spacing ───────────────────────────────────────────────────────────

def test_spacing_blocks_too_soon(tmp_path):
    g = _guard(tmp_path, instagram={"enabled": True, "daily_cap": 5,
                                     "min_minutes_between": 60})
    g.record_result("instagram", success=True)
    ok, reason = g.can_post("instagram")
    assert not ok
    assert "spacing" in reason


# ── circuit breaker ───────────────────────────────────────────────────────────

def test_circuit_opens_after_n_failures(tmp_path):
    g = _guard(tmp_path)
    for _ in range(3):
        g.record_result("instagram", success=False)
    ok, reason = g.can_post("instagram")
    assert not ok
    assert "circuit breaker" in reason


def test_success_resets_circuit(tmp_path):
    g = _guard(tmp_path)
    for _ in range(3):
        g.record_result("instagram", success=False)
    g.record_result("instagram", success=True)
    ok, _ = g.can_post("instagram")
    assert ok
