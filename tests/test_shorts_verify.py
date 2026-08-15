"""Which channel the Shorts token actually belongs to.

A YouTube token binds to the CHANNEL picked on the consent screen, not
to the account. Picking the VOD channel by mistake sends every Short
there, the upload succeeds, the log says ok, and nothing anywhere says
the clips are on the wrong channel.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from utils.posting_status import (  # noqa: E402
    _check_youtube_shorts, _handle, verify, OK, MISSING, FAILED, SKIPPED)


@pytest.fixture
def signed_in(tmp_path):
    token = tmp_path / "shorts_token.json"
    token.write_text("{}")
    return {
        "youtube_shorts": {"channel": "@STACKSWOPO10K",
                           "token_path": str(token)},
        "youtube": {"channel": "STACKSWOPOVODS", "client_secrets_path": ""},
    }


def _youtube(title, custom, subscribers="41", monkeypatch=None):
    """Stand in for the YouTube API answering channels.list(mine=True)."""

    class _Request:
        def execute(self):
            return {"items": [{"snippet": {"title": title, "customUrl": custom},
                               "statistics": {"subscriberCount": subscribers}}]}

    class _Channels:
        def list(self, **kw):
            assert kw.get("mine") is True, "must ask about the token's OWN channel"
            return _Request()

    class _Service:
        def channels(self):
            return _Channels()

    class _Uploader:
        def __init__(self, *a, **k):
            pass

        def get_service(self):
            return _Service()

    return _Uploader


def _patch(monkeypatch, uploader):
    import utils.youtube_uploader as module

    monkeypatch.setattr(module, "YouTubeUploader", uploader)


# ── comparing names ──────────────────────────────────────────────────

@pytest.mark.parametrize("a,b", [
    ("@STACKSWOPO10K", "stackswopo10k"),
    ("@STACKSWOPO10K", "Stackswopo 10K"),
    ("  @stackswopo10k  ", "STACKSWOPO10K"),
])
def test_one_channel_written_three_ways_still_matches(a, b):
    assert _handle(a) == _handle(b)


def test_two_real_channels_do_not_match():
    assert _handle("@STACKSWOPO10K") != _handle("STACKSWOPOVODS")


# ── the check ────────────────────────────────────────────────────────

def test_the_right_channel_passes(signed_in, monkeypatch):
    _patch(monkeypatch, _youtube("Stackswopo 10K", "@stackswopo10k"))
    check = _check_youtube_shorts(signed_in)
    assert check.state == OK
    assert check.identity == "@stackswopo10k"
    assert "41 subscribers" in check.detail


def test_the_vod_channel_is_caught_and_named(signed_in, monkeypatch):
    """The whole reason this check exists."""
    _patch(monkeypatch, _youtube("STACKSWOPOVODS", "@stackswopovods"))
    check = _check_youtube_shorts(signed_in)
    assert check.state == FAILED
    assert "your VOD channel" in check.detail
    assert "@STACKSWOPO10K" in check.detail


def test_a_wrong_channel_says_how_to_fix_it(signed_in, monkeypatch):
    _patch(monkeypatch, _youtube("Some Other Channel", "@other"))
    check = _check_youtube_shorts(signed_in)
    assert check.state == FAILED
    assert "--setup-shorts" in check.detail


def test_an_unnamed_channel_does_not_crash(signed_in, monkeypatch):
    _patch(monkeypatch, _youtube("", ""))
    assert _check_youtube_shorts(signed_in).state == FAILED


def test_no_token_file_means_not_signed_in_yet(tmp_path):
    cfg = {"youtube_shorts": {"channel": "@X",
                              "token_path": str(tmp_path / "missing.json")}}
    check = _check_youtube_shorts(cfg)
    assert check.state == MISSING
    assert "--setup-shorts" in check.detail


def test_no_token_path_configured_is_reported(tmp_path):
    check = _check_youtube_shorts({"youtube_shorts": {"channel": "@X"}})
    assert check.state == MISSING
    assert "token_path" in check.detail


def test_an_api_failure_is_reported_not_raised(signed_in, monkeypatch):
    class _Boom:
        def __init__(self, *a, **k):
            pass

        def get_service(self):
            raise RuntimeError("quota exceeded")

    _patch(monkeypatch, _Boom)
    check = _check_youtube_shorts(signed_in)
    assert check.state == FAILED
    assert "quota exceeded" in check.detail


def test_no_configured_channel_accepts_whatever_is_signed_in(tmp_path, monkeypatch):
    """Nothing to compare against is not the same as a mismatch."""
    token = tmp_path / "t.json"
    token.write_text("{}")
    _patch(monkeypatch, _youtube("Anything", "@anything"))
    check = _check_youtube_shorts(
        {"youtube_shorts": {"channel": "", "token_path": str(token)}})
    assert check.state == OK


def test_an_empty_config_does_not_crash():
    assert _check_youtube_shorts({}).state == MISSING


def test_it_is_wired_into_verify(signed_in, monkeypatch):
    _patch(monkeypatch, _youtube("Stackswopo 10K", "@stackswopo10k"))
    results = verify(["youtube_shorts"], cfg_dict=signed_in)
    assert results[0].state == OK
    assert results[0].state != SKIPPED, "was 'no credential check written'"
