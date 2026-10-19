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


class _Box:
    def __init__(self, label, checked=False):
        self.label, self.checked = label, checked

    def is_checked(self):
        return self.checked

    def is_disabled(self):
        return False

    def get_attribute(self, name):
        return None

    def evaluate(self, js):
        return self.label

    def check(self, force=True, timeout=0):
        self.checked = True


class _Page:
    def __init__(self, boxes):
        self.boxes = boxes

    def locator(self, sel):
        page = self

        class L:
            def all(self):
                return page.boxes
        return L()


def _uploader(promote):
    up = RumbleUploader.__new__(RumbleUploader)
    up._promote = promote
    return up


def test_feature_box_is_ticked_again_at_submit_for_a_full_stream():
    feature = _Box("Feature video on the top of your profile")
    terms = _Box("I agree to the terms of service")
    _uploader(True)._accept_terms(_Page([feature, terms]))
    assert feature.checked and terms.checked


def test_a_clip_leaves_the_feature_box_alone():
    feature = _Box("Feature video on the top of your profile")
    _uploader(False)._accept_terms(_Page([feature]))
    assert not feature.checked


def test_only_full_streams_are_promoted():
    src = open(os.path.join(ROOT, "auto_uploader", "main.py"), encoding="utf-8").read()
    assert "promote=not is_clip," in src
