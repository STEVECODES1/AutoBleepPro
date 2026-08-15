"""The upload that reported success and published nothing.

A full VOD was logged as "uploaded successfully ->
https://rumble.com/v7e6wni-monkey-trolling-on-omegle.html" - an
unrelated video. The real one never reached the channel.

Rumble's upload page carries links to other videos. _find_video_url
accepted any of them, _submit stopped as soon as "a link appeared", and
so the rights/terms form on submit step 2 was never filled. The video
stayed a draft.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader", "utils"))

from utils.channel_vods import slug_matches_title  # noqa: E402

REAL_TITLE = '"copyrighting all yall plug channels" 8/13/26 Stackswopo Stream'
STRAY = "https://rumble.com/v7e6wni-monkey-trolling-on-omegle.html"
REAL = "https://rumble.com/v7e8abc-copyrighting-all-yall-plug-channels.html"


# ── the slug check itself ────────────────────────────────────────────

def test_the_stray_link_is_rejected():
    assert slug_matches_title(STRAY, REAL_TITLE) is False


def test_the_real_link_is_accepted():
    assert slug_matches_title(REAL, REAL_TITLE) is True


def test_a_truncated_slug_still_matches():
    """Rumble cuts long slugs; a near miss must not read as a failure."""
    short = "https://rumble.com/v7e8abc-copyrighting-all-yall.html"
    assert slug_matches_title(short, REAL_TITLE) is True


def test_a_title_too_generic_to_judge_says_so():
    assert slug_matches_title(STRAY, "Full Live Stream") is None


def test_no_title_at_all_says_so():
    assert slug_matches_title(STRAY, "") is None


# ── _find_video_url against a page that has both links ───────────────

class _FakePage:
    """Stands in for the upload page after submit step 1."""

    def __init__(self, anchors=(), inputs=(), text=""):
        self._anchors = list(anchors)
        self._inputs = list(inputs)
        self._text = text

    def evaluate(self, script):
        if "input,textarea" in script:
            return self._inputs
        if "a[href]" in script:
            return self._anchors
        return self._text


@pytest.fixture
def uploader():
    from utils.rumble_uploader import RumbleUploader

    return RumbleUploader.__new__(RumbleUploader)


def test_a_sidebar_link_is_not_mistaken_for_the_upload(uploader):
    page = _FakePage(anchors=[STRAY, "/premium", "/videos"])
    assert uploader._find_video_url(page, REAL_TITLE) is None


def test_the_real_link_is_found_among_sidebar_links(uploader):
    page = _FakePage(anchors=[STRAY, REAL, "/videos"])
    assert uploader._find_video_url(page, REAL_TITLE) == REAL


def test_the_direct_link_field_is_used(uploader):
    page = _FakePage(inputs=[REAL], anchors=[STRAY])
    assert uploader._find_video_url(page, REAL_TITLE) == REAL


def test_without_a_title_the_old_behaviour_stands(uploader):
    """Nothing to compare against - the first link is the best guess."""
    page = _FakePage(anchors=[STRAY])
    assert uploader._find_video_url(page, "") == STRAY


def test_a_generic_title_falls_back_rather_than_failing(uploader):
    page = _FakePage(anchors=[STRAY])
    assert uploader._find_video_url(page, "Full Live Stream") == STRAY


def test_an_empty_page_still_returns_nothing(uploader):
    assert uploader._find_video_url(_FakePage(), REAL_TITLE) is None


def test_the_upload_page_itself_is_never_the_answer(uploader):
    page = _FakePage(anchors=["https://rumble.com/upload.php"])
    assert uploader._find_video_url(page, REAL_TITLE) is None


# ── submit must reach step 2 ─────────────────────────────────────────

class _Button:
    def __init__(self, page):
        self.page = page

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def click(self, timeout=None):
        self.page.clicks += 1


class _SubmitPage(_FakePage):
    """Shows a stray video link from the very first render, and only
    reveals the real one after the SECOND submit - like the real site."""

    def __init__(self):
        super().__init__()
        self.clicks = 0

    def evaluate(self, script):
        links = [STRAY] + ([REAL] if self.clicks >= 2 else [])
        if "a[href]" in script:
            return links
        if "input,textarea" in script:
            return []
        return ""

    def get_by_role(self, *a, **k):
        return _Locator(self)

    def locator(self, *a, **k):
        return _Locator(self)

    def get_by_text(self, *a, **k):
        return _Empty()

    def wait_for_timeout(self, ms):
        pass


class _Empty:
    def count(self):
        return 0


class _Locator:
    def __init__(self, page):
        self.page = page

    def or_(self, other):
        return self

    def all(self):
        return [_Button(self.page)]

    def count(self):
        return 0


def test_submit_reaches_step_two_instead_of_stopping_on_a_stray_link(uploader):
    page = _SubmitPage()
    monkey = lambda *a, **k: None
    uploader._accept_terms = monkey
    uploader._select_categories = monkey

    assert uploader._submit(page, REAL_TITLE) == REAL
    assert page.clicks >= 2, "the rights/terms form on step 2 never ran"


# ── waiting for the publish, not glancing at it ──────────────────────

class _SlowPage(_SubmitPage):
    """Rumble finishes, but not within three seconds of the click.

    This is a multi-GB upload; expecting the link inside one fixed pause
    is what pressed submit a second time on a form that had already gone
    through.
    """

    def __init__(self, polls_before_link=4):
        super().__init__()
        self.polls = 0
        self.polls_before_link = polls_before_link

    def evaluate(self, script):
        if "a[href]" in script:
            self.polls += 1
            ready = self.clicks >= 1 and self.polls > self.polls_before_link
            return [STRAY] + ([REAL] if ready else [])
        if "input,textarea" in script:
            return []
        return ""


def test_a_slow_publish_is_waited_for_not_clicked_again(uploader):
    page = _SlowPage()
    uploader._accept_terms = lambda *a, **k: None
    uploader._select_categories = lambda *a, **k: None

    assert uploader._submit(page, REAL_TITLE) == REAL
    assert page.clicks == 1, "submitted twice - that is the duplicate video"


def test_the_wait_gives_up_eventually(uploader, monkeypatch):
    """A submit that truly did not take must not hang the run."""
    page = _SlowPage(polls_before_link=10_000)
    monkeypatch.setattr(type(uploader), "PUBLISH_WAIT_SECONDS", 0.2)
    monkeypatch.setattr(type(uploader), "PUBLISH_POLL_SECONDS", 0.05)
    assert uploader._await_published(page, REAL_TITLE) is None


def test_a_link_already_there_is_returned_at_once(uploader):
    page = _SlowPage(polls_before_link=0)
    page.clicks = 1
    assert uploader._await_published(page, REAL_TITLE) == REAL


def test_the_wait_still_ignores_a_stray_link(uploader, monkeypatch):
    page = _FakePage(anchors=[STRAY])
    page.wait_for_timeout = lambda ms: None
    monkeypatch.setattr(type(uploader), "PUBLISH_WAIT_SECONDS", 0.2)
    monkeypatch.setattr(type(uploader), "PUBLISH_POLL_SECONDS", 0.05)
    assert uploader._await_published(page, REAL_TITLE) is None
