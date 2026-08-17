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


def test_it_ships_off():
    """Nobody's channel gets posted to because they cloned a repo."""
    import json

    with open(os.path.join(_UPLOADER, "config.example.json"),
              encoding="utf-8") as f:
        shipped = json.load(f)

    assert shipped["posting"]["platforms"]["youtube_shorts"]["enabled"] \
        is False


def test_the_caps_stay_low_and_nothing_goes_public_by_itself():
    """Twenty clips from one VOD posted in an afternoon is what
    'repetitious content' means in YouTube's policy, whoever made them.

    Shorts may be switched ON here - that is the point of the feature -
    but the caps and the privacy are what make it safe to leave on, so
    those are what this guards. "public" is deliberately not allowed to
    arrive by edit: a Short is censored by a pass that has to actually
    work, and the first clip to prove it should not be the first one
    strangers see."""
    import json

    with open(os.path.join(_UPLOADER, "config.json"), encoding="utf-8") as f:
        config = json.load(f)
    settings = config["posting"]["platforms"]["youtube_shorts"]

    assert settings["daily_cap"] <= 5
    assert settings["min_minutes_between"] >= 60
    assert config["youtube_shorts"]["privacy"] in ("private", "unlisted")


def test_the_clip_config_carries_the_shorts_settings():
    """The publisher reads its settings out of the dict _clip_config
    builds. When youtube_shorts was absent it resolved an empty
    token_path, answered ready() False, and every clip logged
    "skipped - not configured yet" - while --posting-status and --verify
    both looked perfect. Nothing ever reached the channel."""
    import sys

    sys.path.insert(0, _UPLOADER)
    from main import _clip_config
    from publishers.youtube_shorts import YouTubeShortsPublisher

    class _General:
        logs_folder = "logs"

    class _YouTube:
        client_secrets_path = "/tmp/secrets.json"
        channel = "@StackswopoGames"

    class _Cfg:
        instagram = {}
        facebook = {}
        clips = {}
        features = {}
        youtube_shorts = {"token_path": "./youtube_shorts_token.json",
                          "channel": "@STACKSWOPO10K"}
        zernio = {}
        youtube = _YouTube()
        general = _General()

    built = _clip_config(_Cfg())
    assert "youtube_shorts" in built, "the publisher cannot see its own settings"

    publisher = YouTubeShortsPublisher(built)
    assert publisher.token_path(), "an empty token_path means ready() is always False"
    assert publisher.token_path().endswith("youtube_shorts_token.json")


def test_the_clip_config_carries_the_shared_client_secrets():
    import sys

    sys.path.insert(0, _UPLOADER)
    from main import _clip_config

    class _General:
        logs_folder = "logs"

    class _YouTube:
        client_secrets_path = "/tmp/secrets.json"
        channel = "@StackswopoGames"

    class _Cfg:
        instagram = {}
        facebook = {}
        clips = {}
        features = {}
        youtube_shorts = {}
        zernio = {}
        youtube = _YouTube()
        general = _General()

    built = _clip_config(_Cfg())
    assert built["youtube"]["client_secrets_path"] == "/tmp/secrets.json"


def test_a_platform_can_be_switched_on_without_editing_json(tmp_path):
    """config.json is untracked so a pull cannot collide with a switch the
    operator flipped - which also means merge_new_settings never updates a
    setting that already exists there. Turning Shorts on was therefore a
    hand edit, in a 700-line file, on Windows."""
    import importlib.util
    import json
    import sys

    sys.path.insert(0, _UPLOADER)
    spec = importlib.util.spec_from_file_location(
        "_main_enable", os.path.join(_UPLOADER, "main.py"))
    main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main)

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"posting": {"platforms": {
        "youtube_shorts": {"enabled": False, "daily_cap": 3,
                           "min_minutes_between": 180}}}}))

    said = main.set_platform_enabled(str(path), "youtube_shorts", on=True)

    assert "ON" in said
    assert json.loads(path.read_text())["posting"]["platforms"][
        "youtube_shorts"]["enabled"] is True
    # The caps are NOT touched - they are what makes leaving it on safe.
    settings = json.loads(path.read_text())["posting"]["platforms"][
        "youtube_shorts"]
    assert settings["daily_cap"] == 3
    assert settings["min_minutes_between"] == 180

    # Idempotent, and honest about a name it does not know.
    assert main.set_platform_enabled(str(path), "youtube_shorts",
                                     on=True) is None
    assert "no such platform" in main.set_platform_enabled(
        str(path), "tiktok", on=True)


def test_shorts_privacy_is_a_command_not_a_json_edit(tmp_path):
    """Same reason as --enable: config.json is untracked, so a setting
    already in it is never updated by a pull.

    The distinction this exists to make usable: PRIVATE is nobody at all,
    including anyone holding the link - which is why a channel full of
    private Shorts reads 0 views - while UNLISTED is watchable by link
    and checkable before it is made public."""
    import importlib.util
    import json
    import sys

    sys.path.insert(0, _UPLOADER)
    spec = importlib.util.spec_from_file_location(
        "_main_privacy", os.path.join(_UPLOADER, "main.py"))
    main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main)

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"youtube_shorts": {"privacy": "private"}}))

    said = main.set_shorts_privacy(str(path), "unlisted")

    assert "unlisted" in said
    assert json.loads(path.read_text())["youtube_shorts"]["privacy"] == \
        "unlisted"
    assert main.set_shorts_privacy(str(path), "unlisted") is None
    assert "not one of" in main.set_shorts_privacy(str(path), "secret")
    assert json.loads(path.read_text())["youtube_shorts"]["privacy"] == \
        "unlisted", "a rejected value still changed the file"


# ── the title is the line, the decoration goes in the description ────

@pytest.mark.parametrize("caption,expected", [
    ("Gumball a** animations \U0001f923\U0001f923\U0001f923#stackswopo",
     "Gumball a** animations"),
    ("Just shut up please \U0001f923\U0001f923\U0001f923#stackswopo",
     "Just shut up please"),
    ("yall b***** be wild \U0001f923 #stackswopo #funny",
     "yall b***** be wild"),
    ("Wifi Cooked #stackswopo #funny", "Wifi Cooked"),
    ("He got me a couple gift cards", "He got me a couple gift cards"),
])
def test_the_title_is_the_line_without_the_emoji_tail(tmp_path, caption,
                                                      expected):
    """A caption's first line is the spoken line plus the emoji and the
    channel tag. That is right in a description and reads as a bot on a
    title - which is what went on the channel:
    "vertical Stackswopo Love Yall 20250914 204409 - Clip 03 ...#st..."."""
    publisher = YouTubeShortsPublisher(_config(tmp_path))

    assert publisher.title_for(caption, "/x/clip.mp4") == expected


def test_an_emoji_inside_the_sentence_is_part_of_it(tmp_path):
    """Only the TAIL comes off. An earlier version allowed words after
    the first emoji and cut "yo (emoji) that was crazy" down to "yo"."""
    publisher = YouTubeShortsPublisher(_config(tmp_path))

    assert publisher.title_for("yo \U0001f923 that was crazy",
                               "/x/clip.mp4") == "yo \U0001f923 that was crazy"


def test_a_title_that_is_only_emoji_keeps_them(tmp_path):
    """Stripping it to empty would be worse than leaving it."""
    publisher = YouTubeShortsPublisher(_config(tmp_path))

    assert publisher.title_for("\U0001f923\U0001f923\U0001f923",
                               "/x/clip.mp4") == "\U0001f923\U0001f923\U0001f923"


def test_the_description_still_carries_everything(tmp_path):
    """The tags and emoji are not thrown away - they move to where they
    belong, which is where they already went."""
    publisher = YouTubeShortsPublisher(_config(tmp_path))
    caption = ("Gumball a** animations \U0001f923#stackswopo\n\n"
               "#monkeyapp #funnymoments")

    body = publisher.description_for(caption)

    assert "#stackswopo" in body and "#monkeyapp" in body
    assert SHORTS_TAG in body


# ── a refresh token is not forever ───────────────────────────────────
#
#   invalid_grant: Token has been expired or revoked.
#   [ABORTED] Refusing to run --batch ... Fix the YouTube auth issue
#             above, then try again.
#
# Google retires a refresh token on a password change, on the app being
# removed from the account's third-party access, and automatically after
# six months unused. It came out as a raw exception with no way forward
# printed - and there was no command to re-authorise the VOD channel at
# all, only the Shorts one.

def test_a_revoked_token_says_which_command_fixes_it(tmp_path, monkeypatch):
    import sys

    sys.path.insert(0, _UPLOADER)
    from utils.youtube_uploader import YouTubeUploader

    secrets = tmp_path / "client_secrets.json"
    secrets.write_text("{}")
    token = tmp_path / "youtube_token.json"
    token.write_text("{}")

    class _Revoked:
        valid = False
        expired = True
        refresh_token = "r"

        def refresh(self, _request):
            raise RuntimeError(
                "('invalid_grant: Token has been expired or revoked.', ...)")

    monkeypatch.setattr(
        "utils.youtube_uploader.Credentials.from_authorized_user_file",
        lambda *a, **k: _Revoked())

    uploader = YouTubeUploader(str(secrets), str(token))
    try:
        uploader._get_credentials()
    except RuntimeError as exc:
        message = str(exc)
        assert "--setup-youtube" in message, \
            "it says it is broken without saying what to run"
        # The ROOT cause, not just the ritual. A testing-mode app's
        # refresh tokens are killed after seven days by design, so
        # signing in again fixes it for a week and then it is back -
        # which is a loop somebody could stay in indefinitely without
        # ever being told there is a switch.
        assert "Testing mode" in message or "SEVEN DAYS" in message
        assert "console.cloud.google.com" in message
    else:
        raise AssertionError("a revoked token did not raise")


def test_the_batch_abort_names_the_command():
    """"Fix the YouTube auth issue above" described the problem to
    somebody who had just read the problem."""
    import re

    with open(os.path.join(_UPLOADER, "main.py"), encoding="utf-8") as f:
        source = f.read()

    abort = source[source.index("Refusing to run --batch"):][:600]
    assert "--setup-youtube" in abort


def test_setup_youtube_exists_alongside_setup_shorts():
    """There were two channels and only one way to sign in."""
    with open(os.path.join(_UPLOADER, "main.py"), encoding="utf-8") as f:
        source = f.read()

    assert '"--setup-youtube"' in source
    assert '"--setup-shorts"' in source
