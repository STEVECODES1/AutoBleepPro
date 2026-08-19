"""Tags that describe the clip, because that is what the rankings read.

Every platform here ranks on whether a tag matches what is actually in
the video. A wrong tag is not neutral - it is a demotion. So these are
matched against the clip rather than pasted onto everything, and the
narrowest justified tag leads.

What is deliberately NOT here is as important: #viral and #fyp were in
the filler list and have been taken out. They describe nothing, they are
the most recognisable mark of an automated repost account, and asking an
algorithm to promote a post is not how any of these platforms work.
"""

from __future__ import annotations

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
for _path in (_REPO, _UPLOADER):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from utils.social_promoter import (ALWAYS_TAGS, CONTENT_TAGS,  # noqa: E402
                                   FILLER_TAGS, PLATFORM_TAGS, TAG_LIMITS,
                                   hashtags_for)


def _tags(title, platform="instagram"):
    return hashtags_for(title, platform).split()


# ── reach-bait is out ────────────────────────────────────────────────

def test_no_reach_bait_anywhere():
    """#viral and #fyp ask an algorithm for promotion. None of these
    platforms works that way, and all of them read it as spam."""
    everything = set(ALWAYS_TAGS) | set(FILLER_TAGS)
    for _needles, tags in CONTENT_TAGS:
        everything |= set(tags)
    for tags in PLATFORM_TAGS.values():
        everything |= set(tags)

    banned = {"viral", "fyp", "foryou", "foryoupage", "explorepage",
              "followforfollow", "f4f", "like4like", "trending"}
    assert not (everything & banned)


def test_it_is_gone_from_a_real_caption():
    assert "#viral" not in hashtags_for("some clip", "instagram")
    assert "#fyp" not in hashtags_for("some clip", "instagram")


# ── the tags match the clip ──────────────────────────────────────────

def test_a_gta_clip_is_not_tagged_as_monkey():
    tags = _tags("stackswopo gta D10 lifestyle RP")

    assert "#gtarp" in tags
    assert "#monkeyapp" not in tags


def test_a_monkey_clip_is_not_tagged_gtarp():
    tags = _tags("Stackswopo monkey app trolling")

    assert "#monkeyapp" in tags
    assert "#gtarp" not in tags


def test_the_narrowest_server_tag_is_used_when_it_is_named():
    """nopixel and fivem are what people actually search for; 'gtarp' is
    the broad one they fall back to."""
    assert "#nopixel" in _tags("stackswopo nopixel rp")
    assert "#fivem" in _tags("stackswopo fivem server")


def test_a_clip_about_nothing_in_particular_still_gets_tagged():
    """Filler exists so a clip whose title says nothing is not posted
    bare - but it is filler, not the lead."""
    tags = _tags("Clip 03")

    assert tags
    assert tags[0] == "#stackswopo"


# ── per platform ─────────────────────────────────────────────────────

def test_youtube_gets_the_shorts_tag():
    """The one tag here that changes how a platform FILES a video rather
    than how it ranks it."""
    assert "#Shorts" in _tags("some clip", "youtube_shorts")


def test_no_other_platform_gets_the_shorts_tag():
    for platform in ("instagram", "facebook", "zernio_twitter"):
        assert "#Shorts" not in _tags("some clip", platform), platform


def test_x_gets_barely_any():
    """More than about two is demoted there and they eat the 280
    characters the caption needs."""
    assert len(_tags("stackswopo gta rp", "zernio_twitter")) <= 2


def test_instagram_gets_a_full_set():
    assert len(_tags("stackswopo gta rp", "instagram")) >= 8


def test_every_platform_stays_inside_its_own_limit():
    for platform, limit in TAG_LIMITS.items():
        found = _tags("stackswopo gta rp nopixel fivem fail", platform)
        assert len(found) <= limit, platform


def test_the_channel_name_leads_everywhere():
    """It is the one tag that is true of every clip and searched by the
    people already looking for him."""
    for platform in TAG_LIMITS:
        assert _tags("anything", platform)[0] == "#stackswopo", platform
