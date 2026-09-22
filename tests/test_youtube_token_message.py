"""
Which sign-in expired, and what to run about it.

One YouTubeUploader class serves two channels with two token files -
youtube_token.json for the VOD channel and youtube_shorts_token.json
for the Shorts one. The expiry message named `--setup-youtube` and said
"pick the VOD channel rather than the Shorts one" whichever of them had
failed. So when the SHORTS token expired it sent you to re-authorise
the VOD channel, which was working fine, and left the broken one
broken.

The second half of the message was wrong in a different way: it
asserted the app was in Testing mode. On the real project it says "In
production", where refresh tokens do NOT expire on a seven-day clock -
so the advice sent someone to a setting that was already correct while
the real causes went unmentioned.
"""

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (_REPO, os.path.join(_REPO, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

import pytest  # noqa: E402

from utils.youtube_uploader import YouTubeUploader  # noqa: E402


class _Expired(Exception):
    def __str__(self):
        return "('invalid_grant: Token has been expired or revoked.', {})"


def _expire(monkeypatch, tmp_path, token_name):
    """Drive _get_credentials to the invalid_grant branch."""
    token = tmp_path / token_name
    token.write_text("{}")

    class _Creds:
        valid = False
        expired = True
        refresh_token = "r"

        def refresh(self, _request):
            raise _Expired()

    import utils.youtube_uploader as yu
    monkeypatch.setattr(yu.Credentials, "from_authorized_user_file",
                        staticmethod(lambda *a, **k: _Creds()))

    uploader = YouTubeUploader(str(tmp_path / "client_secrets.json"),
                               str(token))
    with pytest.raises(RuntimeError) as raised:
        uploader._get_credentials()
    return str(raised.value)


def test_a_dead_shorts_token_sends_you_to_setup_shorts(monkeypatch, tmp_path):
    message = _expire(monkeypatch, tmp_path, "youtube_shorts_token.json")

    assert "--setup-shorts" in message
    assert "--setup-youtube" not in message
    assert "SHORTS channel" in message
    # And says which file, so two failures are not one message twice.
    assert "youtube_shorts_token.json" in message


def test_a_dead_vod_token_sends_you_to_setup_youtube(monkeypatch, tmp_path):
    message = _expire(monkeypatch, tmp_path, "youtube_token.json")

    assert "--setup-youtube" in message
    assert "--setup-shorts" not in message
    assert "not the Shorts one" in message


def test_testing_mode_is_offered_as_a_check_not_asserted(monkeypatch, tmp_path):
    """The real project reads "In production", where this is not the
    cause at all. Stating it as fact sends someone to a setting that is
    already correct."""
    message = _expire(monkeypatch, tmp_path, "youtube_token.json")

    assert "check whether" in message.lower()
    assert "If it already says 'In production', that is NOT the cause" \
        in message.replace("\n", " ").replace("         ", "")


def test_the_other_real_causes_are_named(monkeypatch, tmp_path):
    """A refresh token is also revoked by a password change, by removing
    the app from the account's third-party access, and after six months
    unused - and signing in again fixes all three."""
    message = _expire(monkeypatch, tmp_path, "youtube_token.json").lower()

    assert "password change" in message
    assert "third-party access" in message
    assert "six months" in message
