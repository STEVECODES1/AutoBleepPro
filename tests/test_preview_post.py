"""
Seeing a post before it is posted.

WHY THIS EXISTS
Every wrong post this project has made was SILENT. A caption frozen at
the moment it was queued; hashtags computed and then dropped because the
template had no placeholder; the stream's name where the clip's line
belonged; a video title carrying the emoji tail; a Short uploading
uncensored; a platform switched off in config. None of them raised
anything. All of them were found hours later by scrolling a phone.

All six were visible in the composed text before it went anywhere.
--posting-status answers "may I post" - caps, credentials, kill switch.
This answers "what would I post", which is the question those six
failures were hiding in.
"""

import importlib.util
import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
for _path in (_REPO, _UPLOADER):
    if _path not in sys.path:
        sys.path.insert(0, _path)


@pytest.fixture(scope="module")
def main():
    spec = importlib.util.spec_from_file_location(
        "_main_preview", os.path.join(_UPLOADER, "main.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def cfg(tmp_path):
    """A config shaped like the real one, pointed at a temp folder."""
    clip = tmp_path / "Wifi Cooked - Clip 01.mp4"
    clip.write_bytes(b"x")
    (tmp_path / "Wifi Cooked - Clip 01.txt").write_text(
        "Gumball ass animations")
    (tmp_path / "Wifi Cooked - Clip 01_subject.txt").write_text(
        "Wifi Cooked monkey")

    class _General:
        logs_folder = str(tmp_path)
        watch_folder = str(tmp_path)
        uploaded_folder = str(tmp_path)
        censored_folder = str(tmp_path)
        supported_formats = (".mp4",)

    class _YouTube:
        client_secrets_path = str(tmp_path / "secrets.json")
        channel = "@StackswopoGames"
        tags = []

    class _Cfg:
        project_root = str(tmp_path)
        general = _General()
        youtube = _YouTube()
        instagram = {"caption_template":
                     "{title} \U0001f923#stackswopo\n\nYouTube: @BinScript"}
        facebook = {}
        clips = {}
        features = {}
        youtube_shorts = {"token_path": str(tmp_path / "t.json")}
        zernio = {"caption_template": "{title} \U0001f923#stackswopo"}
        posting = {"state_path": str(tmp_path / "state.json"),
                   "queue_path": str(tmp_path / "jobs.json"),
                   "kill_switch_file": str(tmp_path / "STOP"),
                   "enabled": True,
                   "platforms": {
                       "instagram": {"enabled": True, "daily_cap": 50,
                                     "min_minutes_between": 25},
                       "youtube_shorts": {"enabled": True, "daily_cap": 3,
                                          "min_minutes_between": 180},
                       "zernio_twitter": {"enabled": False, "daily_cap": 12,
                                          "min_minutes_between": 60}}}

    _Cfg.clip = str(clip)
    return _Cfg()


def test_it_shows_the_line_that_would_be_posted(main, cfg, capsys):
    """The failure that took days to find: the STREAM title where the
    clip's own line belonged."""
    assert main._preview_post(cfg, cfg.clip) == 0

    out = capsys.readouterr().out
    assert "Gumball a** animations" in out, \
        "it does not show what the caption would actually say"
    assert "Wifi Cooked \U0001f923" not in out, \
        "that is the stream title, not the clip's line"


def test_it_shows_the_tags(main, cfg, capsys):
    """Hashtags were computed and silently dropped for weeks."""
    main._preview_post(cfg, cfg.clip)

    out = capsys.readouterr().out
    assert "#monkeyapp" in out


def test_it_shows_the_shorts_TITLE_separately_from_the_description(main, cfg,
                                                                   capsys):
    """YouTube shows a title, and it is NOT the caption's first line
    verbatim - the emoji and tags come off it. That difference is exactly
    what put "...Clip 03 [emoji] #stackswopo" on the channel."""
    main._preview_post(cfg, cfg.clip)

    out = capsys.readouterr().out
    assert "title: Gumball a** animations" in out
    assert "description:" in out


def test_it_says_whether_the_audio_gets_bleeped(main, cfg, capsys):
    """Shorts went up uncensored and nothing said so.

    Every social platform is on "slurs" now - Instagram removed a post
    under hateful conduct while its ordinary swearing broke nothing, and
    that is the line everywhere. The preview still has to say it out
    loud, because "bleeped" and "as recorded" are the two ways a clip
    can go out and only one of them is safe on these platforms."""
    main._preview_post(cfg, cfg.clip)

    out = capsys.readouterr().out
    assert "slurs only" in out, "the slur-only bleep is not shown"
    assert "ordinary swearing kept" in out, \
        "the preview does not say the swearing survives"


def test_it_names_a_platform_that_is_switched_off(main, cfg, capsys):
    """youtube_shorts sat disabled in config while everything else looked
    perfect, and nothing ever reached the channel."""
    main._preview_post(cfg, cfg.clip)

    out = capsys.readouterr().out
    assert "zernio_twitter" in out
    assert "disabled" in out.lower()


def test_it_posts_nothing(main, cfg, capsys, monkeypatch):
    """A preview that can publish is not a preview."""
    from utils import clip_queue

    def explode(*_a, **_k):
        raise AssertionError("preview published something")

    monkeypatch.setattr(clip_queue, "publish", explode)
    monkeypatch.setattr(clip_queue, "offer", explode)

    assert main._preview_post(cfg, cfg.clip) == 0
    assert "Nothing was posted" in capsys.readouterr().out


def test_an_empty_queue_says_how_to_preview_a_file(main, cfg, capsys):
    """A command that answers "nothing" and stops there is a command
    nobody runs twice."""
    assert main._preview_post(cfg) == 0

    out = capsys.readouterr().out
    assert "--preview-post" in out


def test_a_missing_file_is_reported_not_guessed(main, cfg, capsys):
    assert main._preview_post(cfg, "no such clip anywhere.mp4") == 1
    assert "not found" in capsys.readouterr().out.lower()
