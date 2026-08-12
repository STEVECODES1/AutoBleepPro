"""
Posting clips to a second YouTube channel as Shorts.

YouTube is the strictest destination in this project about volume and
repetition, and a channel is far harder to get back than a post is to
delete - so the defaults here are conservative on purpose and the tests
say why.
"""

import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
for _path in (_REPO, _UPLOADER):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from publishers.errors import NotConfigured
from publishers.youtube_shorts import SHORTS_TAG, YouTubeShortsPublisher


def _config(tmp_path, **overrides):
    secrets = tmp_path / "client_secrets.json"
    secrets.write_text("{}")
    settings = {"token_path": str(tmp_path / "shorts_token.json"),
                "channel": "@STACKSWOPO10K"}
    settings.update(overrides)
    return {"youtube": {"client_secrets_path": str(secrets)},
            "youtube_shorts": settings}


def test_it_is_not_ready_until_that_channel_is_signed_into(tmp_path):
    """A config that says yes while the token is missing sends every
    clip into an interactive OAuth prompt, and --watch has nobody at the
    keyboard to answer it."""
    publisher = YouTubeShortsPublisher(_config(tmp_path))

    assert publisher.ready() is False


def test_being_signed_in_is_a_file_not_a_flag(tmp_path):
    config = _config(tmp_path)
    open(config["youtube_shorts"]["token_path"], "w").write("{}")

    assert YouTubeShortsPublisher(config).ready() is True


def test_the_setup_message_says_to_pick_the_right_channel(tmp_path):
    """A YouTube token is bound to the CHANNEL picked on the consent
    screen. Pick the VOD channel there and every Short lands on it."""
    publisher = YouTubeShortsPublisher(_config(tmp_path))

    with pytest.raises(NotConfigured) as raised:
        publisher.post_clip("/some/clip.mp4", "caption")

    message = str(raised.value)
    assert "--setup-shorts" in message
    assert "SHORTS channel" in message


def test_the_client_secrets_are_shared_with_the_vod_uploader(tmp_path):
    """It identifies the app, not the channel - a second one would mean
    a second Google Cloud project for no reason."""
    config = _config(tmp_path)

    assert YouTubeShortsPublisher(config).client_secrets_path() == \
        config["youtube"]["client_secrets_path"]


def test_uploads_are_private_until_changed(tmp_path):
    """The first batch is reviewed by a person rather than discovered by
    the channel's audience."""
    publisher = YouTubeShortsPublisher(_config(tmp_path))

    assert publisher.settings.get("privacy", "private") == "private"


def test_the_title_is_the_first_line_of_the_caption(tmp_path):
    publisher = YouTubeShortsPublisher(_config(tmp_path))

    title = publisher.title_for("he did NOT just say that\n\n#stackswopo",
                                "/x/clip.mp4")

    assert title == "he did NOT just say that"


def test_a_long_title_is_cut_on_a_word(tmp_path):
    """YouTube rejects over 100 characters outright, and one that runs to
    the limit is truncated with an ellipsis in every feed it appears
    in."""
    publisher = YouTubeShortsPublisher(_config(tmp_path))

    title = publisher.title_for("word " * 60, "/x/clip.mp4")

    assert len(title) <= 90
    assert not title.endswith("wor")


def test_an_empty_caption_still_produces_a_title(tmp_path):
    publisher = YouTubeShortsPublisher(_config(tmp_path))

    assert publisher.title_for("", "/x/my great clip.mp4") == "my great clip"


def test_the_shorts_tag_is_added_once(tmp_path):
    publisher = YouTubeShortsPublisher(
        _config(tmp_path, description_template="[CAPTION]\n\nmore below"))

    body = publisher.description_for("the caption")

    assert body.count(SHORTS_TAG) == 1
    assert "the caption" in body and "more below" in body


def test_a_caption_that_already_tags_shorts_is_left_alone(tmp_path):
    publisher = YouTubeShortsPublisher(_config(tmp_path))

    assert publisher.description_for("funny #shorts").count("#") == 1


def test_a_dry_run_uploads_nothing(tmp_path):
    config = _config(tmp_path)
    open(config["youtube_shorts"]["token_path"], "w").write("{}")
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")

    assert YouTubeShortsPublisher(config).post_clip(
        str(clip), "caption", dry_run=True) == "dry-run"


def test_youtube_is_the_last_clip_platform():
    """It posts after the others, so a problem shows up on a destination
    that is easier to recover from first."""
    from utils.clip_queue import CLIP_PLATFORMS

    assert CLIP_PLATFORMS[-1] == "youtube_shorts"


def test_it_ships_off_with_low_caps():
    """Twenty clips from one VOD posted in an afternoon is what
    'repetitious content' means in YouTube's policy, whoever made
    them."""
    import json

    with open(os.path.join(_UPLOADER, "config.json"), encoding="utf-8") as f:
        config = json.load(f)
    settings = config["posting"]["platforms"]["youtube_shorts"]

    assert settings["enabled"] is False
    assert settings["max_per_day"] <= 5
    assert settings["min_minutes_between"] >= 60
    assert config["youtube_shorts"]["privacy"] == "private"
