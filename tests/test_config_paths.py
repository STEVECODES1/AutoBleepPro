"""Config paths must not depend on the directory the user launched from.

--setup-shorts run from the repo root reported a missing
client_secrets.json that was sitting in auto_uploader/ the whole time.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from publishers.youtube_shorts import YouTubeShortsPublisher  # noqa: E402

CONFIG_DIR = os.path.join(ROOT, "auto_uploader")


@pytest.fixture
def elsewhere(tmp_path, monkeypatch):
    """Run from a directory that is not the project."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_the_shared_secrets_file_is_found_from_any_directory(elsewhere):
    pub = YouTubeShortsPublisher({
        "youtube": {"client_secrets_path": "client_secrets.json"},
        "youtube_shorts": {"token_path": "./youtube_shorts_token.json"},
    })
    assert pub.client_secrets_path() == os.path.join(
        CONFIG_DIR, "client_secrets.json")


def test_the_token_is_found_from_any_directory(elsewhere):
    pub = YouTubeShortsPublisher({
        "youtube_shorts": {"token_path": "./youtube_shorts_token.json"},
    })
    assert pub.token_path() == os.path.join(
        CONFIG_DIR, "youtube_shorts_token.json")


def test_an_absolute_path_is_left_alone(elsewhere):
    wanted = os.path.join(str(elsewhere), "mine.json")
    pub = YouTubeShortsPublisher({"youtube_shorts": {"token_path": wanted}})
    assert pub.token_path() == wanted


def test_no_token_configured_stays_empty(elsewhere):
    pub = YouTubeShortsPublisher({"youtube_shorts": {}})
    assert pub.token_path() == ""


def test_ready_does_not_depend_on_the_launch_directory(elsewhere, tmp_path):
    """ready() gates every Short. A cwd-relative miss reads as
    'not signed in' and silently posts nothing."""
    token = os.path.join(CONFIG_DIR, "youtube_shorts_token.json")
    pub = YouTubeShortsPublisher({
        "youtube_shorts": {"token_path": "./youtube_shorts_token.json"}})
    assert pub.ready() == os.path.isfile(token)


def test_manual_queue_is_anchored_to_the_config(tmp_path, monkeypatch):
    """Manual-only posts must not pile up in whatever folder was current."""
    import json
    from utils.config import load_config

    src = json.load(open(os.path.join(CONFIG_DIR, "config.json")))
    (tmp_path / "config.json").write_text(json.dumps(src))
    monkeypatch.chdir(os.path.dirname(str(tmp_path)))
    cfg = load_config(str(tmp_path / "config.json"), str(tmp_path / ".env"))
    for key in ("manual_queue_path", "state_path", "queue_path",
                "kill_switch_file"):
        assert os.path.isabs(cfg.posting[key]), key
        assert str(tmp_path) in cfg.posting[key], key
