"""The pinned link comment under your own upload.

A Short cannot carry a clickable link anywhere a viewer will find it, so
the comment is the only route from a Short to the full stream on Rumble.
Every creator leaves one; it should not be a manual step on every upload.

Two things this must never do: fail an upload, or claim to have posted
when it did not.
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


def _uploader(service=None, raises=None):
    from utils.youtube_uploader import YouTubeUploader

    who = YouTubeUploader.__new__(YouTubeUploader)

    def client():
        if raises:
            raise raises
        return service

    who._client = client
    return who


class FakeThreads:
    def __init__(self, blows_up=None):
        self.posted = []
        self._blows_up = blows_up

    def insert(self, part=None, body=None):
        self.posted.append((part, body))
        return self

    def execute(self):
        if self._blows_up:
            raise self._blows_up
        return {"id": "comment-1"}


class FakeService:
    def __init__(self, threads=None):
        self._threads = threads or FakeThreads()

    def commentThreads(self):
        return self._threads


# ── finding the video ────────────────────────────────────────────────

@pytest.mark.parametrize("value", [
    "https://www.youtube.com/watch?v=UXgZ9nMFFow",
    "https://youtu.be/UXgZ9nMFFow",
    "https://www.youtube.com/shorts/UXgZ9nMFFow",
    "https://www.youtube.com/live/UXgZ9nMFFow",
    "UXgZ9nMFFow",
])
def test_the_id_is_found_however_the_link_is_written(value):
    from utils.youtube_uploader import _video_id

    assert _video_id(value) == "UXgZ9nMFFow"


def test_something_that_is_not_a_video_is_not_guessed_at():
    from utils.youtube_uploader import _video_id

    assert _video_id("") == ""
    assert _video_id("https://rumble.com/c/BinScripts") == ""
    assert _video_id("uploaded (no link)") == ""


# ── posting ──────────────────────────────────────────────────────────

def test_the_comment_reaches_the_right_video():
    threads = FakeThreads()
    who = _uploader(FakeService(threads))

    assert who.comment("https://www.youtube.com/watch?v=UXgZ9nMFFow",
                       "Full stream: https://rumble.com/c/BinScripts")

    _part, body = threads.posted[0]
    snippet = body["snippet"]
    assert snippet["videoId"] == "UXgZ9nMFFow"
    assert "rumble.com/c/BinScripts" in (
        snippet["topLevelComment"]["snippet"]["textOriginal"])


def test_nothing_to_say_posts_nothing():
    threads = FakeThreads()
    who = _uploader(FakeService(threads))

    assert who.comment("UXgZ9nMFFow", "   ") is False
    assert not threads.posted


def test_no_video_posts_nothing():
    threads = FakeThreads()
    who = _uploader(FakeService(threads))

    assert who.comment("uploaded (no link)", "hi") is False
    assert not threads.posted


# ── failing quietly ──────────────────────────────────────────────────

def test_a_missing_scope_says_how_to_fix_it(capsys):
    from googleapiclient.errors import HttpError

    class Response:
        status = 403
        reason = "Forbidden"

    error = HttpError(Response(), b'{"error":{"errors":[{"reason":'
                                 b'"insufficientPermissions"}]}}')
    who = _uploader(FakeService(FakeThreads(blows_up=error)))

    assert who.comment("UXgZ9nMFFow", "hi") is False
    assert "--setup-youtube" in capsys.readouterr().out


def test_any_other_failure_is_reported_not_raised(capsys):
    who = _uploader(raises=OSError("no network"))

    assert who.comment("UXgZ9nMFFow", "hi") is False
    assert "no network" in capsys.readouterr().out


# ── the scope, and the token that predates it ────────────────────────

def test_commenting_is_in_the_scopes():
    from utils.youtube_uploader import SCOPES

    assert any("force-ssl" in s for s in SCOPES)


def test_an_old_token_is_spotted_before_it_fails(tmp_path):
    """A token missing a scope fails when it is USED - which for a comment
    is after the video is already live."""
    import json

    from utils.youtube_uploader import needs_reauth

    path = tmp_path / "token.json"
    path.write_text(json.dumps({"scopes": [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly"]}))

    assert needs_reauth(str(path))


def test_a_current_token_is_left_alone(tmp_path):
    import json

    from utils.youtube_uploader import SCOPES, needs_reauth

    path = tmp_path / "token.json"
    path.write_text(json.dumps({"scopes": list(SCOPES)}))

    assert not needs_reauth(str(path))


def test_no_token_is_not_a_reauth(tmp_path):
    from utils.youtube_uploader import needs_reauth

    assert not needs_reauth(str(tmp_path / "gone.json"))


# ── composing it ─────────────────────────────────────────────────────

class _Cfg:
    class youtube:
        link_comment = ""

    class rumble:
        channel_url = ""


def _cfg(template="", rumble=""):
    import copy

    made = copy.deepcopy(_Cfg)
    made.youtube.link_comment = template
    made.rumble.channel_url = rumble
    return made


def _main():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_main_comment", os.path.join(_UPLOADER, "main.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_channel_goes_into_the_comment():
    said = _main().link_comment_text(
        _cfg("Full stream on Rumble: {rumble}", "https://rumble.com/c/BinScripts"))

    assert said == "Full stream on Rumble: https://rumble.com/c/BinScripts"


def test_no_template_means_off():
    """A comment under somebody's own video is theirs to opt into."""
    assert _main().link_comment_text(_cfg("", "https://rumble.com/c/x")) == ""


def test_a_template_wanting_a_link_that_is_not_set_posts_nothing():
    """'Full stream: ' with nothing after it reads as broken - worse than
    no comment at all."""
    assert _main().link_comment_text(_cfg("Full stream: {rumble}", "")) == ""


def test_a_template_with_no_placeholder_still_posts():
    said = _main().link_comment_text(_cfg("New video up", ""))

    assert said == "New video up"


def test_a_failed_upload_gets_no_comment():
    """UPLOADED_NO_URL and the like are not videos to comment on."""
    main = _main()
    posted = []

    class Uploader:
        def comment(self, url, text):
            posted.append(url)
            return True

    cfg = _cfg("Full stream: {rumble}", "https://rumble.com/c/x")
    assert main._leave_link_comment(cfg, Uploader(),
                                    "uploaded (no link)") is False
    assert not posted


def test_a_comment_that_throws_does_not_fail_the_upload(capsys):
    main = _main()

    class Uploader:
        def comment(self, url, text):
            raise OSError("gone")

    cfg = _cfg("Full stream: {rumble}", "https://rumble.com/c/x")
    assert main._leave_link_comment(
        cfg, Uploader(), "https://www.youtube.com/watch?v=UXgZ9nMFFow") is False
    assert "gone" in capsys.readouterr().out
