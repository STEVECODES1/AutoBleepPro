"""
A key written twice in config.json is used once, silently.

JSON allows it and every parser keeps the LAST copy without a word. The
file still opens, still validates, still looks like what was meant -
and behaves like something else.

On 2026-09-22 the rumble block had been edited twice and carried two
copies of five keys:

    "cdp_url": "http://localhost:9222",    <- what the file appeared to say
    ...
    "cdp_url": null,                       <- what was actually used

So Chrome was never attached; the upload went to
rumble.com/api/v1/video/upload, which has no upload form on it; and the
login went to a tools page with no login form. Three failures, each
chased separately over a night, all of them this.
"""

import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (_REPO, os.path.join(_REPO, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

from utils.config import _warn_on_duplicate_keys  # noqa: E402

# The real block, trimmed to the keys that were doubled.
THE_REAL_FILE = """
{
  "login_url": "https://rumble.com/login.php",
  "upload_url": "https://rumble.com/upload.php",
  "cdp_url": "http://localhost:9222",
  "primary_category": "Gaming",
  "login_url": "https://rumble.com/tools/browser-login",
  "upload_url": "https://rumble.com/api/v1/video/upload",
  "cdp_url": null,
  "primary_category": "Entertainment"
}
"""


def _load(text):
    return json.loads(text, object_pairs_hook=_warn_on_duplicate_keys)


def test_the_last_copy_is_what_was_really_in_effect(capsys):
    """Not a claim about what SHOULD happen - a demonstration of what
    already did, so the warning's explanation is accurate."""
    loaded = _load(THE_REAL_FILE)

    assert loaded["cdp_url"] is None
    assert loaded["upload_url"] == "https://rumble.com/api/v1/video/upload"
    assert loaded["login_url"] == "https://rumble.com/tools/browser-login"
    capsys.readouterr()


def test_every_doubled_key_is_named(capsys):
    _load(THE_REAL_FILE)
    printed = capsys.readouterr().out

    for key in ("cdp_url", "upload_url", "login_url", "primary_category"):
        assert key in printed, f"{key} was doubled and went unmentioned"


def test_it_prints_the_value_actually_in_effect(capsys):
    """Naming the key is half of it. The whole confusion was that the
    file showed one value and the program used another, so the message
    has to show which one won."""
    _load(THE_REAL_FILE)
    printed = capsys.readouterr().out

    assert "the one in effect" in printed
    assert "cdp_url = None" in printed


def test_a_clean_file_says_nothing(capsys):
    loaded = _load('{"cdp_url": "http://localhost:9222", "privacy": "public"}')

    assert loaded["cdp_url"] == "http://localhost:9222"
    assert capsys.readouterr().out == "", "a correct file must stay quiet"


def test_the_same_key_in_two_different_blocks_is_fine(capsys):
    """censor_uploads legitimately appears in youtube, rumble, instagram
    and more. Only a repeat within ONE object is a mistake."""
    loaded = _load("""
    {
      "youtube": {"censor_uploads": true},
      "rumble":  {"censor_uploads": false}
    }
    """)

    assert loaded["youtube"]["censor_uploads"] is True
    assert loaded["rumble"]["censor_uploads"] is False
    assert capsys.readouterr().out == ""


def test_a_doubled_comment_key_is_not_worth_shouting_about(capsys):
    """The _comment keys carry documentation, not behaviour. Warning
    about those would train the reader to skip the warning."""
    _load('{"_comment": "a", "cdp_url": 1, "_comment": "b"}')
    assert capsys.readouterr().out == ""


def test_it_warns_rather_than_refusing_to_start():
    """The file is valid JSON and the last-wins value may well be the
    wanted one. Refusing would turn a warning into an outage."""
    assert _load('{"a": 1, "a": 2}') == {"a": 2}


def test_the_shipped_example_config_has_no_duplicates(capsys):
    """The template people copy from must not teach the mistake."""
    path = os.path.join(_REPO, "auto_uploader", "config.example.json")
    with open(path, encoding="utf-8") as handle:
        _load(handle.read())

    assert capsys.readouterr().out == "", \
        "config.example.json itself contains a duplicated key"
