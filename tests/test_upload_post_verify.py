"""
Upload-Post was the one platform nothing could verify.

--posting-status --verify asks every platform who you are. For
upload_post it printed

    --  upload_post   (no credential check written)

which was fine while it was a side route and is not now: with Zernio's
trial over it is the PRIMARY clip path, so the single unverified link
was the one carrying the most.

Three things fail separately and silently without this check: a key
that does not work, an UPLOAD_POST_USER that names no real profile
(it is a profile name from the managed-users page, not an email), and
a profile with nothing connected to it.
"""

import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (_REPO, os.path.join(_REPO, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

import io  # noqa: E402
import contextlib  # noqa: E402

import pytest  # noqa: E402

import utils.posting_status as ps  # noqa: E402

# Shape confirmed against the live API on 2026-09-22.
LIVE_SHAPE = {
    "success": True,
    "profiles": [{
        "username": "default",
        "social_accounts": {
            "tiktok": "",
            "x": {"display_name": "BinScripts", "handle": "BinScripts"},
            "instagram": {"display_name": "stackswopomanz"},
            "facebook": {"display_name": "Bin Bin"},
            "youtube": {"display_name": "STACKSWOPOVODS"},
            "discord": {"display_name": "d"},
            "linkedin": "",
        },
    }],
}


@pytest.fixture
def answers(monkeypatch):
    def _set(payload):
        class _Response:
            def read(self):
                return json.dumps(payload).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(ps.urllib.request, "urlopen",
                            lambda *a, **k: _Response())
    return _set


@pytest.fixture(autouse=True)
def _creds(monkeypatch):
    monkeypatch.setenv("UPLOAD_POST_API_KEY", "key")
    monkeypatch.setenv("UPLOAD_POST_USER", "default")


def test_it_names_the_platforms_the_clip_will_actually_reach(answers):
    answers(LIVE_SHAPE)
    check = ps._check_upload_post()

    assert check.state == ps.OK
    assert check.identity == "profile 'default'"
    for platform in ("discord", "facebook", "instagram", "x", "youtube"):
        assert platform in check.detail


def test_tiktok_is_not_claimed_when_it_is_not_connected(answers):
    """It comes back as an empty string on the free plan - Upload-Post
    does not sell TikTok there. A clip reported as posted to TikTok
    through this route never was."""
    answers(LIVE_SHAPE)
    assert "tiktok" not in ps._check_upload_post().detail


def test_a_profile_name_that_does_not_exist_is_caught(answers, monkeypatch):
    """The failure this exists for. UPLOAD_POST_USER is a profile name,
    and an email or a guess there fails at POST time with nothing that
    says why."""
    monkeypatch.setenv("UPLOAD_POST_USER", "wenboyjit@gmail.com")
    answers(LIVE_SHAPE)
    check = ps._check_upload_post()

    assert check.state == ps.FAILED
    assert "not a profile on this key" in check.detail
    assert "not an email" in check.detail
    # And what the real options are, so it is fixable from the message.
    assert "default" in check.detail


def test_a_profile_with_nothing_connected_is_not_reported_as_fine(answers):
    answers({"success": True,
             "profiles": [{"username": "default", "social_accounts": {}}]})
    check = ps._check_upload_post()

    assert check.state == ps.FAILED
    assert "would reach nothing" in check.detail


def test_missing_credentials_are_reported_as_missing(monkeypatch):
    monkeypatch.delenv("UPLOAD_POST_API_KEY", raising=False)
    monkeypatch.delenv("UPLOAD_POST_USER", raising=False)

    check = ps._check_upload_post()
    assert check.state == ps.MISSING
    assert "UPLOAD_POST_API_KEY" in check.detail
    assert "UPLOAD_POST_USER" in check.detail


def test_a_dead_api_is_a_failure_not_a_crash(monkeypatch):
    def explode(*_a, **_k):
        raise OSError("connection refused")

    monkeypatch.setattr(ps.urllib.request, "urlopen", explode)
    check = ps._check_upload_post()

    assert check.state == ps.FAILED
    assert "connection refused" in check.detail


def test_it_is_registered_so_verify_actually_calls_it():
    """The check existing and the verifier knowing about it are two
    different things - the old behaviour was a check that did not
    exist, reported as "no credential check written"."""
    assert "upload_post" in ps._CHECKS
    assert ps._CHECKS["upload_post"] is ps._check_upload_post
