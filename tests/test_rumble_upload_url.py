"""
The upload page has to be an upload page.

A real run on 2026-09-22 had rumble.upload_url set to

    https://rumble.com/API?a=video/upload&id=&k=&ext=&v2=

Rumble has no route for that, so it parsed "/API" as a USERNAME and
served a channel page belonging to a user called API - 83 followers, no
videos. A channel page has no file input, so the attach waited the full
60 seconds and reported

    [Rumble] The upload form's file input never appeared

which is true, and says nothing about why. The browser meanwhile sat on
a stranger's profile looking like a failed upload.

config.json is gitignored, so a wrong value there cannot be corrected by
pulling. The check has to live in code.
"""

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (_REPO, os.path.join(_REPO, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

from utils.config import (  # noqa: E402
    DEFAULT_RUMBLE_UPLOAD_URL,
    _rumble_upload_url,
)

THE_REAL_BAD_VALUE = "https://rumble.com/API?a=video/upload&id=&k=&ext=&v2="


def test_the_value_that_broke_it_is_rejected(capsys):
    assert _rumble_upload_url({"upload_url": THE_REAL_BAD_VALUE}) == \
        DEFAULT_RUMBLE_UPLOAD_URL

    printed = capsys.readouterr().out
    assert "not a Rumble upload page" in printed
    # Name the offending value, or the message is just as unactionable
    # as the one it replaces.
    assert THE_REAL_BAD_VALUE in printed
    assert "config.json" in printed


def test_a_real_upload_page_is_left_alone():
    for url in ("https://rumble.com/upload.php",
                "https://rumble.com/upload",
                "https://rumble.com/studio/upload"):
        assert _rumble_upload_url({"upload_url": url}) == url


def test_an_unset_value_gets_the_default():
    assert _rumble_upload_url({}) == DEFAULT_RUMBLE_UPLOAD_URL
    assert _rumble_upload_url({"upload_url": ""}) == DEFAULT_RUMBLE_UPLOAD_URL
    assert _rumble_upload_url({"upload_url": None}) == DEFAULT_RUMBLE_UPLOAD_URL


def test_a_channel_page_is_not_an_upload_page():
    """The shape of the mistake, not just the one instance of it."""
    for url in ("https://rumble.com/user/BinScripts",
                "https://rumble.com/c/c-413755",
                "https://rumble.com/"):
        assert _rumble_upload_url({"upload_url": url}) == \
            DEFAULT_RUMBLE_UPLOAD_URL


def test_the_check_is_narrow_enough_not_to_second_guess_rumble():
    """It exists to catch a value that cannot possibly carry an upload
    form - not to outlaw a path Rumble might rename to tomorrow."""
    assert _rumble_upload_url(
        {"upload_url": "https://rumble.com/upload-v2?beta=1"}) == \
        "https://rumble.com/upload-v2?beta=1"


def test_the_error_says_which_page_it_actually_waited_on():
    """"The file input never appeared" is equally true of the upload
    page on a slow night and of a page that was never the upload page.
    The URL is what tells them apart."""
    body = open(os.path.join(_REPO, "auto_uploader", "utils",
                             "rumble_uploader.py"), encoding="utf-8").read()

    assert "The page it waited on was" in body
    assert "rumble.upload_url in config.json" in body
