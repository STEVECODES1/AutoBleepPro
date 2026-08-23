"""TikTok, and sending its viewers to Rumble.

Stackswopo's clips do real numbers on TikTok in other people's hands -
the reposts run 45K to 200K likes - and not one of those posts sends
anybody to the Rumble channel. A clip that travels without a destination
is somebody else's traffic.

The plumbing for TikTok already existed: zernio_tiktok has been a
separate guarded destination since the X split, with its own cap and
spacing, because TikTok's spam checks punish rapid near-identical posting
far harder than X does. It was switched off because there was no account.
There is one now.

Two things had to change. --setup-zernio found the account and then
printed a line asking somebody to go and edit JSON, which is not setup;
and no caption said where to watch the rest.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (_REPO, os.path.join(_REPO, "auto_uploader")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from utils.clip_queue import promo_line, with_promo  # noqa: E402


@pytest.fixture
def config():
    return {"rumble": {"channel_url": "rumble.com/user/stackswopo10k"},
            "zernio": {"promote": {
                "tiktok": "Full uncensored streams on Rumble: {rumble}",
                "twitter": ""}}}


# ── the pointer itself ───────────────────────────────────────────────────

def test_tiktok_points_at_the_rumble_channel(config):
    assert promo_line("zernio_tiktok", config) == (
        "Full uncensored streams on Rumble: rumble.com/user/stackswopo10k")


def test_x_is_left_alone_by_default(config):
    """280 characters is the whole budget there, and X demotes posts
    carrying an external link."""
    assert promo_line("zernio_twitter", config) == ""


def test_an_unset_channel_url_posts_nothing_rather_than_a_placeholder(config):
    """Otherwise every clip goes out saying "{rumble}"."""
    config["rumble"]["channel_url"] = ""

    assert promo_line("zernio_tiktok", config) == ""


def test_a_platform_with_no_promo_configured_gets_none(config):
    assert promo_line("instagram", config) == ""


def test_a_broken_promo_block_is_not_a_crash(config):
    config["zernio"]["promote"] = "not a mapping"

    assert promo_line("zernio_tiktok", config) == ""


# ── how it joins the caption ─────────────────────────────────────────────

def test_it_goes_after_the_caption_that_was_written_for_the_clip(config):
    caption = with_promo("Imma switch yo ahh #stackswopo", "zernio_tiktok",
                         config, limit=2200)

    assert caption.startswith("Imma switch yo ahh #stackswopo")
    assert caption.endswith("rumble.com/user/stackswopo10k")


def test_it_is_not_added_twice(config):
    once = with_promo("clip", "zernio_tiktok", config, limit=2200)
    twice = with_promo(once, "zernio_tiktok", config, limit=2200)

    assert once == twice


def test_it_is_dropped_rather_than_cutting_the_caption(config):
    """The line written for the clip is what earns the post; the pointer
    is the extra. Truncating the first to fit the second is backwards."""
    long_caption = "x" * 2190

    assert with_promo(long_caption, "zernio_tiktok", config,
                      limit=2200) == long_caption


def test_it_fits_inside_tiktoks_limit(config):
    caption = with_promo("a real caption", "zernio_tiktok", config, limit=2200)

    assert len(caption) <= 2200


def test_an_empty_caption_still_gets_the_pointer(config):
    assert with_promo("", "zernio_tiktok", config, limit=2200) == (
        "Full uncensored streams on Rumble: rumble.com/user/stackswopo10k")


# ── setup turns it on rather than asking ─────────────────────────────────

def _main_source() -> str:
    with open(os.path.join(_REPO, "auto_uploader", "main.py"),
              encoding="utf-8") as handle:
        return handle.read()


def test_setup_zernio_enables_what_it_found():
    """A connected TikTok that posts nothing looks exactly like a broken
    one."""
    body = _main_source()
    spot = body.index("Saved {len(found)} account id(s)")
    before = body[spot - 2500:spot]

    assert 'entry["enabled"] = True' in before
    assert 'raw.setdefault("posting", {})' in before


def test_setup_zernio_says_which_ones_it_switched_on():
    body = _main_source()

    assert "TURNED ON" in body
    assert "already on" in body


def test_enabling_does_not_bypass_the_publishing_authority():
    """PublishGuard stays the only thing that decides whether a clip goes
    out - this flips one flag it reads."""
    body = _main_source()
    spot = body.index('entry["enabled"] = True')

    assert "PublishGuard" in body[spot - 1200:spot]


# ── the shipped config carries it ────────────────────────────────────────

def test_the_shipped_config_has_a_tiktok_promo():
    with open(os.path.join(_REPO, "auto_uploader", "config.json"),
              encoding="utf-8") as handle:
        shipped = json.load(handle)

    promote = shipped["zernio"]["promote"]
    assert "{rumble}" in promote["tiktok"]
    assert promote["twitter"] == ""


def test_tiktok_keeps_its_own_cap_and_spacing():
    """TikTok's spam checks punish rapid near-identical posting far harder
    than X's do, which is why they were split in the first place."""
    from publish_guard import SPLIT_PLATFORM_LIMITS

    tiktok = SPLIT_PLATFORM_LIMITS["zernio_tiktok"]
    twitter = SPLIT_PLATFORM_LIMITS["zernio_twitter"]

    assert tiktok["daily_cap"] < twitter["daily_cap"]
    assert tiktok["min_minutes_between"] > twitter["min_minutes_between"]
