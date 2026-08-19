"""--verify had nothing to say about the one platform that was broken.

    OK   instagram        as @stackswopomanz  (28232 followers)
    OK   x                as @BinScripts
    --   zernio_twitter   (no credential check written)

...while every clip was being skipped with "zernio_twitter: not
configured yet". --posting-status said every variable was present and the
guard said ALLOW, both truthfully: the API key was fine and the ACCOUNT
ID was missing from config.json. Nothing anywhere said which half was
wrong.

A check that reports "no check written" for a platform is not a passing
check - it is a gap wearing the same colour as one.
"""

from __future__ import annotations

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
for _path in (_REPO, _UPLOADER):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import pytest  # noqa: E402

from utils.posting_status import (FAILED, MISSING, OK,  # noqa: E402
                                  SKIPPED, verify)


class FakeZernio:
    """Stands in for ZernioPublisher."""

    def __init__(self, token="k", account="acc-1", found=None, blows_up=False):
        self._token = token
        self._account = account
        self._found = found
        self._blows_up = blows_up

    def token(self):
        return self._token

    def account_id(self):
        return self._account

    def accounts(self):
        if self._blows_up:
            raise RuntimeError("Zernio is down")
        if self._found is None:
            return [{"id": "acc-1", "username": "BinScripts"}]
        return self._found


@pytest.fixture
def zernio(monkeypatch):
    holder = {}

    def make(config, destination="zernio_twitter"):
        return holder["publisher"]

    import publishers.zernio as module
    monkeypatch.setattr(module, "ZernioPublisher", make)
    return holder


def _check(zernio, publisher):
    zernio["publisher"] = publisher
    return verify(["zernio_twitter"])[0]


def test_it_is_checked_at_all(zernio):
    """The whole point: not SKIPPED with 'no check written'."""
    assert _check(zernio, FakeZernio()).state != SKIPPED


def test_a_working_setup_names_the_account(zernio):
    check = _check(zernio, FakeZernio())

    assert check.state == OK
    assert check.identity == "@BinScripts"


def test_a_missing_key_is_named_as_that(zernio):
    check = _check(zernio, FakeZernio(token=""))

    assert check.state == MISSING
    assert "ZERNIO_API_KEY" in check.detail


def test_a_missing_account_id_is_named_as_that(zernio):
    """The actual failure. The key was never the problem."""
    check = _check(zernio, FakeZernio(account=""))

    assert check.state == FAILED
    assert "--setup-zernio" in check.detail
    assert "key is set" in check.detail


def test_an_id_the_key_cannot_reach_is_caught(zernio):
    """A stale id survives a key rotation and posts nowhere, which reads
    exactly like a broken key."""
    check = _check(zernio, FakeZernio(
        account="old-id", found=[{"id": "new-id", "username": "BinScripts"}]))

    assert check.state == FAILED
    assert "--setup-zernio" in check.detail


def test_a_key_with_no_accounts_connected(zernio):
    check = _check(zernio, FakeZernio(found=[]))

    assert check.state == FAILED
    assert "connected" in check.detail


def test_zernio_being_down_is_not_a_crash(zernio):
    check = _check(zernio, FakeZernio(blows_up=True))

    assert check.state == FAILED
    assert "down" in check.detail


def test_the_other_destination_is_checked_too(zernio):
    zernio["publisher"] = FakeZernio()

    assert verify(["zernio_tiktok"])[0].state != SKIPPED


def test_an_account_with_no_username_still_passes(zernio):
    check = _check(zernio, FakeZernio(found=[{"id": "acc-1"}]))

    assert check.state == OK


def test_the_id_field_matches_what_setup_zernio_saves(zernio):
    """--setup-zernio stores account["_id"]. Comparing against "id" here
    meant a correct save never matched, and this check called a freshly
    written id unreachable - a false alarm pointing at the command that
    had just been run successfully."""
    check = _check(zernio, FakeZernio(
        account="6a80c2547755",
        found=[{"_id": "6a80c2547755", "platform": "twitter",
                "username": "BinScripts"}]))

    assert check.state == OK
    assert check.identity == "@BinScripts"


def test_setup_and_verify_agree_on_the_field_order():
    """One list, read the same way by both, or they drift apart again."""
    main_body = open(os.path.join(_UPLOADER, "main.py"),
                     encoding="utf-8").read()
    status_body = open(os.path.join(_UPLOADER, "utils", "posting_status.py"),
                       encoding="utf-8").read()

    assert 'account.get("_id")' in main_body
    assert 'entry.get("_id"' in status_body


# ── a FAIL you are meant to ignore ───────────────────────────────────

def _cfg(enabled):
    return {"posting": {"platforms": {
        "zernio_tiktok": {"enabled": enabled, "daily_cap": 6}}}}


def test_a_disabled_platform_does_not_shout(zernio):
    """zernio_tiktok failed on every run for an account deliberately
    switched off and never coming back. A FAIL nobody is meant to act on
    is what teaches somebody to scroll past a real one."""
    zernio["publisher"] = FakeZernio(account="")

    check = verify(["zernio_tiktok"], cfg_dict=_cfg(False))[0]

    assert check.state == SKIPPED
    assert "off in config" in check.detail


def test_the_reason_survives_the_downgrade(zernio):
    """Downgraded, not hidden - the row stays and still says what is
    wrong, for whenever it does get switched on."""
    zernio["publisher"] = FakeZernio(account="")

    check = verify(["zernio_tiktok"], cfg_dict=_cfg(False))[0]

    assert "--setup-zernio" in check.detail


def test_an_enabled_platform_still_fails_loudly(zernio):
    zernio["publisher"] = FakeZernio(account="")

    check = verify(["zernio_tiktok"], cfg_dict=_cfg(True))[0]

    assert check.state == FAILED


def test_a_disabled_platform_that_works_still_reads_ok(zernio):
    """Knowing a switched-off platform's credentials are good is worth
    having on the day it gets switched on."""
    zernio["publisher"] = FakeZernio()

    check = verify(["zernio_tiktok"], cfg_dict=_cfg(False))[0]

    assert check.state == OK


def test_no_config_at_all_changes_nothing(zernio):
    zernio["publisher"] = FakeZernio(account="")

    assert verify(["zernio_tiktok"])[0].state == FAILED
