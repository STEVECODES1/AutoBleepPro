"""Tuning how hard a platform is posted to, without editing JSON.

config.json is untracked so a pull never updates a value already in it,
and it is 700 lines long on the machine least convenient for editing. The
cap and the spacing are the two numbers anyone actually wants to change
once a platform starts doing well, so they get a command like the on/off
switch already has.

What this must NOT become is a way around the guard. The kill switch, the
circuit breaker and the manual-only rule are not negotiable from here.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
if _UPLOADER not in sys.path:
    sys.path.insert(0, _UPLOADER)


def _main():
    spec = importlib.util.spec_from_file_location(
        "_main_pace", os.path.join(_UPLOADER, "main.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config(tmp_path, platforms=None):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"posting": {"platforms": platforms or {
        "zernio_twitter": {"enabled": True, "daily_cap": 12,
                           "min_minutes_between": 60}}}}))
    return path


def _platforms(path):
    return json.loads(path.read_text())["posting"]["platforms"]


def test_both_numbers_can_be_set_at_once(tmp_path):
    path = _config(tmp_path)

    said = _main().set_platform_pace(str(path), "zernio_twitter",
                                     per_day=20, minutes=35)

    assert "20" in said and "35" in said
    settings = _platforms(path)["zernio_twitter"]
    assert settings["daily_cap"] == 20
    assert settings["min_minutes_between"] == 35


def test_one_can_be_changed_without_the_other(tmp_path):
    path = _config(tmp_path)

    _main().set_platform_pace(str(path), "zernio_twitter", minutes=30)

    settings = _platforms(path)["zernio_twitter"]
    assert settings["min_minutes_between"] == 30
    assert settings["daily_cap"] == 12, "the cap was not being changed"


def test_the_switch_is_not_touched(tmp_path):
    """--enable owns that. A pace change must not turn anything on."""
    path = _config(tmp_path, {"zernio_tiktok": {"enabled": False,
                                                "daily_cap": 6}})

    _main().set_platform_pace(str(path), "zernio_tiktok", per_day=10)

    assert _platforms(path)["zernio_tiktok"]["enabled"] is False


def test_a_cap_of_zero_is_refused(tmp_path):
    """It would read as "unlimited" to no-one and as "off" to everyone,
    and there is already a command that means off."""
    path = _config(tmp_path)

    said = _main().set_platform_pace(str(path), "zernio_twitter", per_day=0)

    assert "--disable" in said
    assert _platforms(path)["zernio_twitter"]["daily_cap"] == 12


def test_a_negative_gap_is_refused(tmp_path):
    path = _config(tmp_path)

    said = _main().set_platform_pace(str(path), "zernio_twitter", minutes=-5)

    assert "no sense" in said
    assert _platforms(path)["zernio_twitter"]["min_minutes_between"] == 60


def test_no_gap_at_all_is_allowed(tmp_path):
    """Zero is a real answer - it means the daily cap is the only limit."""
    path = _config(tmp_path)

    _main().set_platform_pace(str(path), "zernio_twitter", minutes=0)

    assert _platforms(path)["zernio_twitter"]["min_minutes_between"] == 0


def test_the_other_spelling_of_the_cap_is_cleared(tmp_path):
    """max_per_day is also read. Leaving both means the one that wins is
    whichever the reader checked first - a limit nobody can predict."""
    path = _config(tmp_path, {"zernio_twitter": {
        "enabled": True, "max_per_day": 3, "min_minutes_between": 60}})

    _main().set_platform_pace(str(path), "zernio_twitter", per_day=20)

    settings = _platforms(path)["zernio_twitter"]
    assert settings["daily_cap"] == 20
    assert "max_per_day" not in settings


def test_a_renamed_platform_can_still_be_paced(tmp_path):
    """Same migration as --enable: a live config predates the split."""
    path = _config(tmp_path, {"zernio": {"enabled": True, "daily_cap": 12,
                                         "min_minutes_between": 60}})

    said = _main().set_platform_pace(str(path), "zernio_twitter", per_day=20)

    assert "no such platform" not in said
    assert _platforms(path)["zernio_twitter"]["daily_cap"] == 20


def test_an_unknown_platform_is_refused(tmp_path):
    path = _config(tmp_path)

    said = _main().set_platform_pace(str(path), "myspace", per_day=5)

    assert "no such platform" in said


def test_the_guard_still_decides(tmp_path):
    """The point: this moves two numbers, and nothing else about whether
    a post may happen."""
    from publish_guard import PublishGuard

    path = _config(tmp_path)
    _main().set_platform_pace(str(path), "zernio_twitter", per_day=99,
                              minutes=0)

    posting = json.loads(path.read_text())["posting"]
    posting["enabled"] = False          # master switch off
    guard = PublishGuard(posting, str(tmp_path / "state.json"))

    assert not guard.check("zernio_twitter")
