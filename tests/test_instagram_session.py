"""Instagram asked for a 2FA code on every run: the saved session was
loaded and then a full password login done anyway."""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

ig = pytest.importorskip("publishers.instagram_free")


class FakeClient:
    logins = []
    session_ok = True

    def load_settings(self, path):
        pass

    def dump_settings(self, path):
        pass

    def get_timeline_feed(self):
        if not FakeClient.session_ok:
            raise RuntimeError("login_required")

    def login(self, user, password, verification_code=""):
        FakeClient.logins.append(verification_code)
        if not verification_code:
            raise ig.TwoFactorRequired("code please")
        return True

    def totp_generate_code(self, secret):
        return "123456"


@pytest.fixture
def publisher(tmp_path, monkeypatch):
    FakeClient.logins = []
    FakeClient.session_ok = True
    session = tmp_path / "session.json"
    session.write_text("{}")
    monkeypatch.setenv("INSTA_USERNAME", "u")
    monkeypatch.setenv("INSTA_PASSWORD", "p")
    monkeypatch.setenv("INSTA_SESSION_FILE", str(session))
    monkeypatch.delenv("INSTA_TOTP_SECRET", raising=False)
    monkeypatch.setattr(ig, "InstagrapiClient", FakeClient, raising=False)
    monkeypatch.setattr(ig, "_INSTA_OK", True)
    monkeypatch.setattr("builtins.input",
                        lambda *a: pytest.fail("asked for a 2FA code"))
    return ig.InstagramFreePublisher({})


def test_a_good_saved_session_is_used_without_logging_in(publisher):
    assert publisher._ensure_client() is not None
    assert FakeClient.logins == []


def test_an_expired_session_answers_2fa_from_the_totp_secret(
        publisher, monkeypatch):
    FakeClient.session_ok = False
    monkeypatch.setenv("INSTA_TOTP_SECRET", "ABCD EFGH")
    assert publisher._ensure_client() is not None
    assert FakeClient.logins == ["", "123456"]
