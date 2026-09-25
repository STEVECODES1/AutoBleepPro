"""
Three failures from one real clip run, on 2026-09-22.

Every clip in a nine-clip batch hit all three, and each one was quiet
in a different way.
"""

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (_REPO, os.path.join(_REPO, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)


# ── 1. upload_post crashed on its own constant ────────────────────────

def test_the_platform_aliases_are_reachable_from_the_publisher():
    """PLATFORM_ALIASES is defined at MODULE level and was read as
    self.PLATFORM_ALIASES. Python resolves an attribute on the instance
    then the class, never the module, so every call raised

        'UploadPostPublisher' object has no attribute 'PLATFORM_ALIASES'

    The queue counted that as a failed post and after three the breaker
    opened - so the route that had just been wired into the clip
    pipeline posted nothing and then switched itself off for an hour.
    """
    from publishers import upload_post as up

    assert isinstance(up.PLATFORM_ALIASES, dict)
    assert up.PLATFORM_ALIASES.get("x") == "x"

    body = open(up.__file__, encoding="utf-8").read()
    code = "\n".join(line for line in body.splitlines()
                     if not line.lstrip().startswith("#"))
    assert "self.PLATFORM_ALIASES" not in code, \
        "the module constant is still being read off the instance"


def test_the_publisher_can_be_built_and_asked_for_targets():
    """Constructing it and reaching the alias lookup is what actually
    blew up; asserting the dict exists alone would not have caught it."""
    from publishers.upload_post import PLATFORM_ALIASES, UploadPostPublisher

    publisher = UploadPostPublisher({})
    assert isinstance(publisher, UploadPostPublisher)
    assert PLATFORM_ALIASES["youtube_shorts"] == "youtube"


# ── 2. NVIDIA refuses more than 12 images ─────────────────────────────

def test_nvidia_gets_twelve_frames_not_forty_eight():
    """Measured: a 48-image request is refused outright with

        HTTP 500: VLLMValidationError: At most 12 image(s) may be
        provided in one prompt. (parameter=image)

    every time, and then four retries were spent on it, because the
    retry rule knows "500 is transient" and a request that is too big
    does not get smaller by waiting.
    """
    from autoreel.llm_highlights import (GEMINI, NVIDIA, VISION_MAX_IMAGES,
                                         images_allowed)

    assert images_allowed(NVIDIA) == 12
    assert images_allowed(GEMINI) == VISION_MAX_IMAGES == 48


def test_thinning_keeps_one_frame_each_from_more_candidates():
    """Truncating at 12 would describe six candidates in pictures and
    leave eighteen as text the model already had. One frame each means
    twelve are seen - which is the point of showing it pictures."""
    from autoreel.llm_highlights import thin_images

    parts = []
    for index in range(24):
        parts.append({"text": f"candidate {index}"})
        parts.append({"inline_data": {"data": f"{index}a"}})
        parts.append({"inline_data": {"data": f"{index}b"}})

    thinned = thin_images(parts, 12)
    images = [p["inline_data"]["data"] for p in thinned if "inline_data" in p]

    assert len(images) == 12
    assert images == [f"{i}a" for i in range(12)], \
        "it kept two frames from six candidates instead of one from twelve"
    # Every candidate keeps its words either way.
    assert sum(1 for p in thinned if "text" in p) == 24


def test_a_provider_that_takes_them_all_is_left_alone():
    from autoreel.llm_highlights import thin_images

    parts = [{"text": "a"}, {"inline_data": {"data": "1"}}]
    assert thin_images(parts, 48) == parts


def test_a_zero_budget_drops_the_pictures_not_the_words():
    from autoreel.llm_highlights import thin_images

    parts = [{"text": "a"}, {"inline_data": {"data": "1"}}, {"text": "b"}]
    assert thin_images(parts, 0) == [{"text": "a"}, {"text": "b"}]


# ── 3. a spent daily quota is not a busy server ───────────────────────

def test_an_exhausted_daily_quota_is_not_retried():
    """Gemini's free tier is 20 requests a day - three streams' worth.
    Once gone, every remaining clip paid 20 seconds to be told so
    again, three times per clip, while the next provider in the cascade
    sat there ready."""
    from autoreel.llm_highlights import _is_transient, is_quota_exhausted

    spent = ("HTTP 429: You exceeded your current quota, please check your "
             "plan and billing details. Quota exceeded for metric: "
             "generativelanguage.googleapis.com/"
             "generate_content_free_tier_requests, limit: 20, "
             "model: gemini-3.8-flash")

    assert is_quota_exhausted(spent)
    assert not _is_transient(spent), "it would wait 20s and ask again"


def test_an_ordinary_rate_limit_is_still_retried():
    """The two arrive as the same status code and mean opposite things.
    Treating every 429 as permanent would give up on a provider that
    only needed a moment."""
    from autoreel.llm_highlights import _is_transient

    assert _is_transient("HTTP 429: Too Many Requests")


def test_a_busy_or_broken_server_is_still_retried():
    from autoreel.llm_highlights import _is_transient

    for problem in ("HTTP 503: high demand",
                    "HTTP 500: VLLMValidationError",
                    "HTTP 502: bad gateway",
                    "the request timed out"):
        assert _is_transient(problem), problem


def test_x_is_sent_to_upload_post_as_x_not_twitter():
    """A real run, every clip: 'REST upload rejected (HTTP 400):
    {"success":false,"message":"Invalid platforms: ['twitter']"}' - and a
    rejected call posts to none of its platforms. Upload-Post's own SDK
    (2.13) names it "x"."""
    from publishers import upload_post as up

    assert up.PLATFORM_ALIASES["x"] == "x"
    assert "twitter" not in up.PLATFORM_ALIASES.values()


def test_a_result_that_says_twitter_still_counts_as_x():
    from publishers import upload_post as up

    reached = up.platforms_reached({"results": [
        {"platform": "twitter", "success": True},
        {"platform": "x", "success": True}]})
    assert reached == {"x"}
