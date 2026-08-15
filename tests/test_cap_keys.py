"""A cap that nothing reads is worse than no cap at all.

The youtube_shorts block was written with "max_per_day". The guard reads
"daily_cap". Nothing joined the two, so a config that plainly said 3
produced "youtube_shorts OK (unlimited)" - and the only place that
showed was one word in a status report nobody reads twice.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from publish_guard import (  # noqa: E402
    daily_cap_of, unknown_keys, FALLBACK_DAILY_CAP, KNOWN_PLATFORM_KEYS)


def test_the_documented_key_is_read():
    assert daily_cap_of({"enabled": True, "daily_cap": 3}) == 3


def test_the_key_that_was_actually_written_is_read_too():
    assert daily_cap_of({"enabled": True, "max_per_day": 3}) == 3


def test_the_documented_key_wins_when_both_are_present():
    assert daily_cap_of({"enabled": True, "daily_cap": 2,
                         "max_per_day": 99}) == 2


def test_an_enabled_platform_with_no_cap_is_never_unlimited():
    """An oversight is far likelier than a decision to post without bound."""
    assert daily_cap_of({"enabled": True}) == FALLBACK_DAILY_CAP


def test_a_disabled_platform_needs_no_cap():
    assert daily_cap_of({"enabled": False}) == 0


def test_an_explicit_zero_is_honoured_as_no_limit():
    """Writing 0 is a decision. Only a MISSING cap is an oversight."""
    assert daily_cap_of({"enabled": True, "daily_cap": 0}) == 0


def test_a_junk_cap_does_not_crash():
    with pytest.raises((ValueError, TypeError)):
        daily_cap_of({"enabled": True, "daily_cap": "three"})


def test_a_misspelled_key_is_reported():
    assert unknown_keys({"enabled": True, "dailycap": 3}) == ["dailycap"]


def test_a_correct_block_reports_nothing():
    assert unknown_keys({"enabled": True, "daily_cap": 3,
                         "min_minutes_between": 180, "_comment": "x"}) == []


def test_every_key_the_shipped_config_uses_is_known():
    """The check must not cry wolf about the project's own config."""
    import json

    raw = json.load(open(os.path.join(ROOT, "auto_uploader", "config.json"),
                         encoding="utf-8"))
    for name, block in (raw["posting"]["platforms"] or {}).items():
        assert unknown_keys(block) == [], f"{name} has keys nothing reads"


def test_the_shipped_shorts_cap_is_actually_in_effect():
    """The exact failure: config said 3, guard said unlimited."""
    import json

    raw = json.load(open(os.path.join(ROOT, "auto_uploader", "config.json"),
                         encoding="utf-8"))
    shorts = raw["posting"]["platforms"]["youtube_shorts"]
    assert daily_cap_of({**shorts, "enabled": True}) == 3


def test_every_shipped_platform_has_a_real_cap():
    import json

    raw = json.load(open(os.path.join(ROOT, "auto_uploader", "config.json"),
                         encoding="utf-8"))
    for name, block in (raw["posting"]["platforms"] or {}).items():
        assert daily_cap_of({**block, "enabled": True}) > 0, name
