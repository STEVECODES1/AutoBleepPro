"""
Reading the Rumble channel PAGE, because there is no feed.

Rumble publishes no RSS - not <channel>/index.xml, not <channel>/rss -
so every run has printed:

    [Rumble] No channel feed - Rumble publishes no RSS. Using local
             upload history instead.

Local history only knows what THIS tool uploaded. Anything posted from
a phone or from rumble.com is invisible to it, and a finished upload
cannot be confirmed to have actually landed - which is the check that
catches a Rumble upload that filled in the form and silently never
published.

The channel page has all of it and sits behind Cloudflare. Everything
here is about turning that page into the same ExistingVideo records the
feed used to produce.
"""

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (_REPO, os.path.join(_REPO, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

import pytest  # noqa: E402

from utils.rumble_checker import (  # noqa: E402
    _parse_channel_markdown,
    channel_page_for,
    fetch_rumble_videos,
    firecrawl_key,
)

# Shaped exactly like the real scrape of rumble.com/user/BinScripts on
# 2026-09-21 - 2.4s, 21556 characters, 33 videos - including the parts
# that broke a naive parser: a bolded featured title, the same video
# appearing twice, and the site's own navigation links.
CHANNEL_PAGE = """
[Home](https://rumble.com/)
[Videos](https://rumble.com/videos)
[Browse](https://rumble.com/browse)

[**"shadows" 8/4/26 Stackswopo Stream**](https://rumble.com/v7dqrxq-shadows-8426-stackswopo-stream.html)

[**"shadows" 8/4/26 Stackswopo Stream**](https://rumble.com/v7dqrxq-shadows-8426-stackswopo-stream.html)

[\"aye bruh wtw\" 9/18/26 Stackswopo Stream](https://rumble.com/v7fabcd-aye-bruh-wtw-91826-stackswopo-stream.html)

[\"yo whats the motive\" 9/17/26 Stackswopo Stream](https://rumble.com/v7fzzzz-yo-whats-the-motive-91726-stackswopo-stream.html)

[Terms & Conditions](https://rumble.com/s/terms)
"""


def test_the_videos_come_off_the_page():
    videos = _parse_channel_markdown(CHANNEL_PAGE)

    assert [v.title for v in videos] == [
        '"shadows" 8/4/26 Stackswopo Stream',
        '"aye bruh wtw" 9/18/26 Stackswopo Stream',
        '"yo whats the motive" 9/17/26 Stackswopo Stream',
    ]
    assert videos[0].video_id == "v7dqrxq-shadows-8426-stackswopo-stream.html"


def test_the_featured_video_is_not_counted_twice():
    """A channel page shows its featured video in the banner AND in the
    grid. Counting it twice would make "already uploaded" depend on
    which copy was read first."""
    videos = _parse_channel_markdown(CHANNEL_PAGE)
    assert len(videos) == len({v.url for v in videos})


def test_the_bold_markers_are_not_part_of_the_title():
    """Rumble bolds the featured title. Leaving the asterisks in stops it
    matching the title that was uploaded, which reads as "not uploaded
    yet" and re-uploads the stream."""
    assert not any(v.title.startswith("*") for v in
                   _parse_channel_markdown(CHANNEL_PAGE))


def test_navigation_links_are_not_mistaken_for_videos():
    """/videos, /browse and /s/terms are on every channel page. A pattern
    matching a bare "/v" prefix takes the first of those as a video -
    that exact bug once recorded rumble.com/videos as an uploaded
    video's URL and posted it to Discord."""
    urls = [v.url for v in _parse_channel_markdown(CHANNEL_PAGE)]
    assert "https://rumble.com/videos" not in urls
    assert all(".html" in u for u in urls)


def test_an_empty_page_yields_nothing_rather_than_raising():
    assert _parse_channel_markdown("") == []
    assert _parse_channel_markdown("no links here at all") == []


# ── which page to read ────────────────────────────────────────────────

def test_the_channel_url_is_used_when_configured():
    assert channel_page_for("", "https://rumble.com/user/BinScripts/") == \
        "https://rumble.com/user/BinScripts"


def test_the_page_is_derived_from_the_feed_url_otherwise():
    """The feed address is <channel>/index.xml, so the channel is what is
    left after dropping the filename."""
    assert channel_page_for("https://rumble.com/user/BinScripts/index.xml") == \
        "https://rumble.com/user/BinScripts"


# ── staying optional ──────────────────────────────────────────────────

def test_without_a_key_the_old_behaviour_is_unchanged(monkeypatch):
    """This route must never become a NEW way for the check to fail. No
    key means it is skipped, and the error still names every route."""
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    assert firecrawl_key() == ""

    with pytest.raises(RuntimeError) as raised:
        fetch_rumble_videos("https://rumble.invalid/nope/index.xml")

    message = str(raised.value)
    assert "direct:" in message
    assert "FIRECRAWL_API_KEY" in message, \
        "the message should say which route was unavailable and why"


def test_a_scrape_that_finds_nothing_falls_through_loudly(monkeypatch):
    """An empty result is not an empty channel - it is a page whose
    layout changed. Returning [] would read as "nothing is on Rumble"
    and re-upload every stream."""
    import utils.rumble_checker as checker

    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(checker, "_fetch_plain",
                        lambda url: (None, "no feed"))
    monkeypatch.setattr(checker, "_fetch_via_firecrawl",
                        lambda page, key: (None, "layout changed"))

    with pytest.raises(RuntimeError) as raised:
        checker.fetch_rumble_videos("https://rumble.invalid/u/x/index.xml")
    assert "layout changed" in str(raised.value)


def test_a_successful_scrape_is_returned(monkeypatch):
    import utils.rumble_checker as checker

    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test")
    monkeypatch.setattr(checker, "_fetch_plain", lambda url: (None, "no feed"))
    monkeypatch.setattr(
        checker, "_fetch_via_firecrawl",
        lambda page, key: (_parse_channel_markdown(CHANNEL_PAGE), ""))

    videos = checker.fetch_rumble_videos(
        "https://rumble.invalid/u/x/index.xml",
        channel_url="https://rumble.com/user/BinScripts")
    assert len(videos) == 3
