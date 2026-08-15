"""New settings must reach a config that git no longer manages.

Untracking config.json stopped `git pull` colliding with a switch the
operator flipped. It also stopped new settings ever arriving: a config
restored from a backup had no clips.auto_clip_folder, so the auto-clip
pass read "" and did nothing - silently, on a build that supported it.
The folder had two VODs in it and nothing happened, with no error.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from main import _fill_missing, merge_new_settings  # noqa: E402


def _write(path, data):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    return str(path)


@pytest.fixture
def pair(tmp_path):
    return str(tmp_path / "config.json"), str(tmp_path / "config.example.json")


# ── the exact failure ────────────────────────────────────────────────

def test_a_setting_added_after_the_backup_arrives(pair):
    live, example = pair
    _write(live, {"clips": {"profile": "auto"}})
    _write(example, {"clips": {"profile": "auto",
                               "auto_clip_folder": "./downloaded_vods"}})
    merge_new_settings(live, example)
    assert json.load(open(live))["clips"]["auto_clip_folder"] == "./downloaded_vods"


def test_their_own_choices_are_never_overwritten(pair):
    """The whole point of an untracked config is that these are theirs."""
    live, example = pair
    _write(live, {"posting": {"platforms": {"youtube_shorts": {"enabled": True}}}})
    _write(example, {"posting": {"platforms": {"youtube_shorts": {"enabled": False,
                                                                  "daily_cap": 3}}}})
    merge_new_settings(live, example)
    shorts = json.load(open(live))["posting"]["platforms"]["youtube_shorts"]
    assert shorts["enabled"] is True
    assert shorts["daily_cap"] == 3


def test_it_reaches_settings_nested_several_levels_down(pair):
    live, example = pair
    _write(live, {"a": {"b": {"c": {"kept": 1}}}})
    _write(example, {"a": {"b": {"c": {"kept": 9, "added": 2}}}})
    merge_new_settings(live, example)
    deep = json.load(open(live))["a"]["b"]["c"]
    assert deep == {"kept": 1, "added": 2}


def test_a_second_run_changes_nothing(pair):
    live, example = pair
    _write(live, {"clips": {}})
    _write(example, {"clips": {"auto_clip_count": 3}})
    assert merge_new_settings(live, example)
    assert merge_new_settings(live, example) == []


def test_it_says_what_it_added(pair):
    live, example = pair
    _write(live, {"clips": {}})
    _write(example, {"clips": {"auto_clip_folder": "./x"}})
    said = " ".join(merge_new_settings(live, example))
    assert "clips.auto_clip_folder" in said
    assert "not changed" in said


def test_comment_only_additions_are_not_announced(pair):
    """A new _comment is not news; saying so on every run is noise."""
    live, example = pair
    _write(live, {"clips": {"profile": "auto"}})
    _write(example, {"clips": {"profile": "auto", "_note": "explaining"}})
    assert merge_new_settings(live, example) == []
    assert "_note" in json.load(open(live))["clips"]


# ── never a crash, never a loss ──────────────────────────────────────

def test_a_missing_example_is_a_no_op(pair, tmp_path):
    live, _ = pair
    _write(live, {"a": 1})
    assert merge_new_settings(live, str(tmp_path / "nope.json")) == []
    assert json.load(open(live)) == {"a": 1}


def test_a_missing_config_is_a_no_op(pair):
    _, example = pair
    _write(example, {"a": 1})
    assert merge_new_settings(str(example) + ".absent", example) == []


def test_a_corrupt_config_is_left_completely_alone(pair):
    live, example = pair
    with open(live, "w") as handle:
        handle.write("{not json")
    _write(example, {"clips": {"auto_clip_count": 3}})
    assert merge_new_settings(live, example) == []
    assert open(live).read() == "{not json"


def test_a_list_setting_is_replaced_only_when_absent(pair):
    """Merging INTO a list would duplicate tags on every single run."""
    live, example = pair
    _write(live, {"youtube": {"tags": ["mine"]}})
    _write(example, {"youtube": {"tags": ["a", "b"]}})
    merge_new_settings(live, example)
    assert json.load(open(live))["youtube"]["tags"] == ["mine"]


def test_a_dict_replacing_a_plain_value_is_not_merged_into(pair):
    live, example = pair
    _write(live, {"clips": "off"})
    _write(example, {"clips": {"profile": "auto"}})
    merge_new_settings(live, example)
    assert json.load(open(live))["clips"] == "off"


def test_nothing_is_written_when_nothing_is_missing(pair):
    live, example = pair
    _write(live, {"a": 1})
    _write(example, {"a": 2})
    before = os.path.getmtime(live)
    assert merge_new_settings(live, example) == []
    assert os.path.getmtime(live) == before


def test_no_temp_file_is_left_behind(pair):
    live, example = pair
    _write(live, {})
    _write(example, {"clips": {"auto_clip_count": 3}})
    merge_new_settings(live, example)
    assert not os.path.exists(live + ".tmp")


def test_fill_missing_reports_the_full_path(pair):
    live = {"a": {}}
    assert _fill_missing(live, {"a": {"b": 1}}) == ["a.b"]
