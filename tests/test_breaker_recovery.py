"""A circuit breaker that only a person can clear is an outage.

From publishers.log, 2026-08-21:

    09:11:27 ERROR Instagram: upload rejected (HTTP 400) ProcessingFailedError
    09:12:31 ERROR Instagram: upload rejected (HTTP 400) ProcessingFailedError
    09:13:21 ERROR Instagram: upload rejected (HTTP 400) ProcessingFailedError

Three in a row, the breaker opened, and --posting-status still read

    BLOCK  instagram       circuit breaker open for instagram: 3 consecutive
    BLOCK  youtube_shorts  circuit breaker open for youtube_shorts: 3 conse...

two days later. Instagram had published dozens of Reels the day before and
the credentials were fine - Meta's ProcessingFailedError is transient and
comes back on the next attempt, which the log shows over and over. Nothing
was broken except that nobody had run --reset-failures.

The original rule said the breaker must never clear itself, because three
failures running means something is wrong at the account level and an
auto-reset would walk back into it every hour - which is how an account
gets flagged. That reasoning is right and its conclusion was not, for an
owner who is not watching the console.

So: not a reset, and not a retry loop. ONE post per window, and the window
doubles with every further failure - 1h, 2h, 4h, capped at a day. A real
credential problem is attempted about five times a day and then less; a
passing glitch clears itself in an hour with nobody involved.
"""

from __future__ import annotations

import os
import sys
import time

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (_REPO, os.path.join(_REPO, "auto_uploader")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from publish_guard import PublishGuard  # noqa: E402

HOUR = 3600.0


def _guard(tmp_path, **breaker):
    settings = {"consecutive_failures": 3, "trial_after_minutes": 60}
    settings.update(breaker)
    config = {
        "enabled": True,
        "platforms": {"instagram": {"enabled": True, "daily_cap": 50,
                                    "min_minutes_between": 0}},
        "circuit_breaker": settings,
    }
    return PublishGuard(config, state_path=str(tmp_path / "state.json"))


def _open_it(guard, times=3):
    for _ in range(times):
        guard.record_failure("instagram")


def _age(guard, seconds):
    guard._state["failed_at"]["instagram"] = time.time() - seconds


# ── it still opens ───────────────────────────────────────────────────────

def test_three_failures_still_stop_the_posting(tmp_path):
    guard = _guard(tmp_path)
    _open_it(guard)

    assert not guard.check("instagram").allowed


def test_it_says_when_it_will_try_again(tmp_path):
    """"fix it and clear it manually" is not an answer for somebody who
    is not reading this."""
    guard = _guard(tmp_path)
    _open_it(guard)

    reason = guard.check("instagram").reason

    assert "Trying one post again in" in reason
    assert "--reset-failures instagram" in reason


# ── and now it recovers by itself ────────────────────────────────────────

def test_one_post_gets_through_after_the_window(tmp_path):
    guard = _guard(tmp_path)
    _open_it(guard)
    _age(guard, HOUR + 60)

    assert guard.check("instagram").allowed


def test_a_success_clears_it_completely(tmp_path):
    guard = _guard(tmp_path)
    _open_it(guard)
    _age(guard, HOUR + 60)

    guard.record_post("instagram")

    assert guard.check("instagram").allowed
    assert guard.consecutive_failures("instagram") == 0
    assert "instagram" not in guard._state.get("failed_at", {})


def test_a_failed_trial_doubles_the_wait(tmp_path):
    """This is what stops it hammering a genuinely broken account."""
    guard = _guard(tmp_path)
    _open_it(guard)
    _age(guard, HOUR + 60)
    guard.record_failure("instagram")      # the trial failed

    _age(guard, HOUR + 60)
    assert not guard.check("instagram").allowed, "an hour is no longer enough"

    _age(guard, 2 * HOUR + 60)
    assert guard.check("instagram").allowed


def test_the_wait_is_capped(tmp_path):
    """Doubling forever becomes never. A day is the floor of how often a
    broken platform is re-checked."""
    guard = _guard(tmp_path, max_trial_wait_minutes=24 * 60)
    _open_it(guard, times=30)
    _age(guard, 24 * HOUR + 60)

    assert guard.check("instagram").allowed


def test_a_broken_account_is_attempted_only_a_handful_of_times_a_day(tmp_path):
    """The original objection: an auto-reset walks back into a broken
    account every hour, and that is what gets an account flagged."""
    guard = _guard(tmp_path)
    _open_it(guard)
    attempts, clock = 0, 0.0

    for _ in range(400):                      # a fortnight, in five-minute steps
        clock += 300
        guard._state.setdefault("failed_at", {})["instagram"] = time.time() - clock
        if guard.check("instagram").allowed:
            attempts += 1
            guard.record_failure("instagram")
            clock = 0.0

    assert attempts <= 6, f"{attempts} attempts is a retry loop, not a breaker"


# ── the state files that already exist ───────────────────────────────────

def test_a_breaker_stuck_open_from_before_this_existed_lets_one_through(tmp_path):
    """Instagram and youtube_shorts are open right now, on a state file
    written before failed_at was recorded. They must recover on the next
    run rather than needing the command nobody ran."""
    guard = _guard(tmp_path)
    guard._state["failures"]["instagram"] = 3   # no failed_at alongside it

    assert guard.check("instagram").allowed


def test_a_corrupt_timestamp_does_not_wedge_it_shut(tmp_path):
    guard = _guard(tmp_path)
    _open_it(guard)
    guard._state["failed_at"]["instagram"] = "not a number"

    assert guard.check("instagram").allowed


def test_manual_clearing_still_works(tmp_path):
    guard = _guard(tmp_path)
    _open_it(guard)

    guard.reset_failures("instagram")

    assert guard.check("instagram").allowed
    assert "instagram" not in guard._state.get("failed_at", {})


def test_clearing_everything_clears_the_timestamps_too(tmp_path):
    guard = _guard(tmp_path)
    _open_it(guard)

    guard.reset_failures()

    assert guard._state.get("failed_at") == {}


def test_it_can_be_turned_back_into_a_manual_only_breaker(tmp_path):
    """For a platform where any automated retry is the wrong move."""
    guard = _guard(tmp_path, trial_after_minutes=0)
    _open_it(guard)
    _age(guard, 365 * 24 * HOUR)

    assert not guard.check("instagram").allowed


def test_the_state_survives_a_restart(tmp_path):
    guard = _guard(tmp_path)
    _open_it(guard)

    again = _guard(tmp_path)

    assert again.consecutive_failures("instagram") == 3
    assert again.last_failure_at("instagram") > 0
