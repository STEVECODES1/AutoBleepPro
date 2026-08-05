"""
Diagnosing an X 401 without guessing.

X answers a bad credential with a bare "401 Unauthorized" that names none
of the four values involved, so the same message covers stale tokens,
quoted .env values, and the API key pasted into the access token slot.
These tests cover the checks that narrow it down locally, before any
network call.
"""

from __future__ import annotations

import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
for _path in (_REPO, _UPLOADER):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from utils.posting_status import (  # noqa: E402
    APP_KEYS_BAD,
    APP_KEYS_OK,
    APP_KEYS_UNKNOWN,
    REQUIRED_ENV,
    x_credential_shape_problems,
)

# Shaped like the real thing: the access token carries the user id and a
# hyphen, the others don't.
GOOD = {
    "TWITTER_API_KEY": "aBcDeFgHiJkLmNoPqRsTuVwXy",
    "TWITTER_API_SECRET": "s" * 50,
    "TWITTER_ACCESS_TOKEN": "1234567890123456789-AbCdEfGhIjKlMnOpQrSt",
    "TWITTER_ACCESS_SECRET": "t" * 45,
}


@pytest.fixture
def x_env(monkeypatch):
    def setup(**overrides):
        values = dict(GOOD)
        values.update(overrides)
        for name in REQUIRED_ENV["x"]:
            monkeypatch.delenv(name, raising=False)
        for name, value in values.items():
            if value is not None:
                monkeypatch.setenv(name, value)
        return values
    return setup


def test_well_formed_credentials_report_nothing(x_env):
    x_env()
    assert x_credential_shape_problems() == []


def test_quoted_values_are_caught(x_env):
    """.env is literal - the quotes become part of the credential and the
    result is a 401 that looks exactly like an expired token."""
    x_env(TWITTER_API_KEY='"aBcDeFgHiJkLmNoPqRsTuVwXy"')
    problems = x_credential_shape_problems()
    assert any("quotes" in p for p in problems)
    assert any("TWITTER_API_KEY" in p for p in problems)


def test_single_quoted_values_are_caught(x_env):
    x_env(TWITTER_ACCESS_SECRET="'" + "t" * 45 + "'")
    assert any("quotes" in p for p in x_credential_shape_problems())


def test_trailing_whitespace_is_caught(x_env):
    x_env(TWITTER_ACCESS_TOKEN=GOOD["TWITTER_ACCESS_TOKEN"] + "  ")
    assert any("whitespace" in p for p in x_credential_shape_problems())


def test_placeholders_are_caught(x_env):
    x_env(TWITTER_API_SECRET="your_api_secret_here")
    assert any("placeholder" in p for p in x_credential_shape_problems())


def test_an_access_token_without_a_hyphen_is_flagged(x_env):
    """X issues access tokens as '<user id>-<secret>'. One without a
    hyphen is nearly always the API key in the wrong variable."""
    x_env(TWITTER_ACCESS_TOKEN="aBcDeFgHiJkLmNoPqRsTuVwXy")
    problems = x_credential_shape_problems()
    assert any("'-'" in p for p in problems)


def test_the_api_key_pasted_as_the_access_token_is_flagged(x_env):
    x_env(TWITTER_ACCESS_TOKEN=GOOD["TWITTER_API_KEY"])
    assert any("identical" in p for p in x_credential_shape_problems())


def test_duplicated_secrets_are_flagged(x_env):
    x_env(TWITTER_ACCESS_SECRET=GOOD["TWITTER_API_SECRET"])
    assert any("identical" in p for p in x_credential_shape_problems())


def test_several_problems_are_all_reported(x_env):
    """Fixing one and rediscovering the next is a slow way to work."""
    x_env(TWITTER_API_KEY=' "abc" ', TWITTER_API_SECRET="your_secret")
    assert len(x_credential_shape_problems()) >= 2


def test_a_real_looking_token_is_not_false_flagged(x_env):
    x_env(TWITTER_ACCESS_TOKEN="99887766554433221-xYzAbC123dEfGhIjKlMnOp")
    assert x_credential_shape_problems() == []


# ═════════════════════════════════════════════════════════════════════════════
# The 401 guidance itself
# ═════════════════════════════════════════════════════════════════════════════

def test_guidance_names_the_access_tokens_when_the_app_keys_are_valid(
        x_env, monkeypatch):
    """The useful split: valid app keys mean regenerate the tokens, not
    rebuild the app."""
    from utils import posting_status

    x_env()
    monkeypatch.setattr(posting_status, "x_app_credentials_work",
                        lambda: (APP_KEYS_OK, ""))
    guidance = posting_status._x_401_guidance()
    assert "ACCESS TOKEN" in guidance
    assert "Regenerate" in guidance


def test_guidance_points_at_the_app_when_even_the_keys_fail(x_env, monkeypatch):
    from utils import posting_status

    x_env()
    monkeypatch.setattr(
        posting_status, "x_app_credentials_work",
        lambda: (APP_KEYS_BAD, "the API key/secret were rejected (401)"))
    guidance = posting_status._x_401_guidance()
    assert "not only the access tokens" in guidance
    assert "SAME app" in guidance


def test_a_shape_problem_short_circuits_the_network_call(x_env, monkeypatch):
    """No point asking X about credentials that are visibly malformed."""
    from utils import posting_status

    called = []
    x_env(TWITTER_API_KEY='"quoted"')
    monkeypatch.setattr(posting_status, "x_app_credentials_work",
                        lambda: called.append(1) or (APP_KEYS_OK, ""))
    guidance = posting_status._x_401_guidance()
    assert called == [], "made a network call despite a local diagnosis"
    assert "quotes" in guidance


def test_guidance_always_mentions_the_permission_ordering(x_env, monkeypatch):
    """Setting Read+Write does NOT update tokens that already exist - the
    order matters and is the most common cause of a repeat 401."""
    from utils import posting_status

    x_env()
    monkeypatch.setattr(posting_status, "x_app_credentials_work",
                        lambda: (APP_KEYS_OK, ""))
    assert "order matters" in posting_status._x_401_guidance()


def test_a_403_from_app_only_auth_is_not_read_as_bad_keys(x_env, monkeypatch):
    """App-only auth is unavailable on the Free tier, so a 403 there says
    nothing about the key and secret. Reading it as "the keys are bad"
    sends people to recreate an app that was never the problem."""
    from utils import posting_status

    x_env()
    monkeypatch.setattr(
        posting_status, "x_app_credentials_work",
        lambda: (APP_KEYS_UNKNOWN, "app-only auth is not available (403)"))
    guidance = posting_status._x_401_guidance()
    assert "rejected" not in guidance
    assert "likeliest cause is still the ACCESS TOKEN" in guidance
    assert "Regenerate" in guidance
