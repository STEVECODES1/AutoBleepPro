"""Full streams go on top of the Rumble profile and ping followers (owner,
10-18); clips never do - they would knock the full stream off the top and
spend the one push Rumble allows a day."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

from utils.rumble_uploader import RumbleUploader  # noqa: E402

P = RumbleUploader._PROMOTE_PATTERN


def test_promote_boxes_match_and_terms_do_not():
    assert P.search("Feature video on the top of your profile")
    assert P.search("Send mobile push notification to followers")
    assert not P.search("I agree to the terms and conditions")
    assert not RumbleUploader._TERMS_PATTERN.search("Send mobile push notification to followers")


def test_only_full_streams_are_promoted():
    src = open(os.path.join(ROOT, "auto_uploader", "main.py"), encoding="utf-8").read()
    assert "promote=not is_clip," in src
