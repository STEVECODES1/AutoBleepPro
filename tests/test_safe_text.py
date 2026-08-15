"""Titles that will not get a channel struck.

Real titles this pipeline produced and posted:

    Fuck up from youtube i'm just steve williams
    Bro, what type of nerd ass faggot shit you got?
    Niggas be with trannies bro

Those are the lines actually spoken, which is why they read like a
person wrote them - and on Rumble that is the point. YouTube and
Instagram apply their rules to the TEXT too, and the Shorts channel took
a year to reach 749 subscribers.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from autoreel.safe_text import (  # noqa: E402
    DROP_ENTIRELY, KEEP_PLAIN, clean, clean_lines, clean_title, is_clean, mask)

SAFE = "Stackswopo clip"


# ── masking keeps the sentence ───────────────────────────────────────

def test_a_swear_is_masked_not_removed():
    assert clean("Fuck up from youtube") == "F*** up from youtube"


def test_the_first_letter_survives():
    assert mask("shit") == "s***"


def test_punctuation_around_a_word_survives():
    assert mask("(shit)") == "(s***)"


def test_ordinary_words_are_untouched():
    line = "He got me a couple gift cards"
    assert clean(line) == line


@pytest.mark.parametrize("word", KEEP_PLAIN)
def test_borderline_words_are_left_plain(word):
    """The compliance list is tuned for AUDIO, where a bleep on a
    borderline word costs nothing. Starring an ordinary word in a title
    makes the channel look like it is hiding something it is not."""
    assert clean(f"this is {word} today") == f"this is {word} today"


# ── slurs are removed, not starred ───────────────────────────────────

@pytest.mark.parametrize("slur", ["nigga", "faggot", "tranny", "retard"])
def test_a_slur_is_gone_entirely(slur):
    """Nobody reads `n****` as a different word, so a starred stump is
    the appearance of moderation rather than the fact of it."""
    out = clean(f"some {slur} here")
    assert slur not in out.lower()
    assert "*" not in out


def test_every_dropped_word_is_actually_dropped():
    for slur in DROP_ENTIRELY:
        assert slur not in clean(f"a {slur} b").lower()


def test_removing_a_word_does_not_leave_a_double_space():
    assert "  " not in clean("Bro, what type of nerd faggot shit you got?")


def test_removing_a_word_does_not_leave_a_stranded_comma():
    assert not clean("nigga, hello there friend").startswith(",")


# ── a wrecked title is not published ─────────────────────────────────

def test_a_title_that_loses_a_word_falls_back():
    """"Niggas be with trannies bro" cleans to "be with bro", which is
    not a title anybody would write - and the arithmetic misses it,
    because only two words of eight went."""
    assert clean_title("Niggas be with trannies bro oh my god", SAFE) == SAFE


def test_a_masked_title_is_still_used():
    """Masking is a fix; dropping is a demolition."""
    out = clean_title("Fuck up from youtube i'm just steve williams", SAFE)
    assert out.startswith("F***")
    assert out != SAFE


def test_a_clean_title_passes_straight_through():
    line = "He got me a couple gift cards"
    assert clean_title(line, SAFE) == line


def test_an_empty_title_falls_back():
    assert clean_title("", SAFE) == SAFE


def test_with_no_fallback_it_returns_what_it_can():
    """Never empty: an empty title is not an improvement on a bad one."""
    assert clean_title("Niggas be with trannies bro oh my god", "")


# ── multi-line captions ──────────────────────────────────────────────

def test_line_breaks_survive():
    text = "Fuck this\n\nYouTube: @BinScript"
    out = clean_lines(text)
    assert out.count("\n") == text.count("\n")
    assert "@BinScript" in out


def test_the_channel_links_are_not_mangled():
    text = "hi\n\nRumble: rumble.com/user/BinScripts"
    assert "rumble.com/user/BinScripts" in clean_lines(text)


def test_is_clean_agrees_with_clean():
    assert is_clean("He got me a couple gift cards")
    assert not is_clean("Fuck up from youtube")


# ── Rumble is deliberately untouched ─────────────────────────────────

def test_rumble_is_not_in_the_cleaned_set():
    """Rumble is the uncensored channel. Filtering it would flatten the
    exact thing its audience is there for."""
    sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))
    from utils.clip_queue import CLEAN_TEXT_PLATFORMS

    assert "rumble" not in CLEAN_TEXT_PLATFORMS
    assert "instagram" in CLEAN_TEXT_PLATFORMS
    assert "youtube_shorts" in CLEAN_TEXT_PLATFORMS


def test_the_shorts_publisher_cleans_its_title():
    sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))
    from publishers.youtube_shorts import YouTubeShortsPublisher

    pub = YouTubeShortsPublisher({"youtube_shorts": {}})
    assert pub.title_for("Fuck up from youtube", "/c/a.mp4").startswith("F***")


def test_the_shorts_publisher_falls_back_on_a_wrecked_title():
    sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))
    from publishers.youtube_shorts import YouTubeShortsPublisher

    pub = YouTubeShortsPublisher({"youtube_shorts": {"safe_title": "Clip time"}})
    assert pub.title_for("Niggas be with trannies bro oh my god",
                         "/c/a.mp4") == "Clip time"


def test_the_shorts_description_is_cleaned():
    sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))
    from publishers.youtube_shorts import YouTubeShortsPublisher

    pub = YouTubeShortsPublisher({"youtube_shorts": {}})
    assert "Fuck" not in pub.description_for("Fuck up from youtube")
