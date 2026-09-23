"""
Upload-Post was configured, verified, allowed - and never used.

There are two clip paths. social_promoter.announce_upload uses
Upload-Post as the primary route and says so in a comment. The clips cut
out of a VOD do not go through that function - they go through
clip_queue.offer, iterating CLIP_PLATFORMS, and upload_post was not in
it.

So the real run printed exactly six [Clips] lines - instagram,
facebook, tiktok, zernio_twitter, zernio_tiktok, youtube_shorts - and
never one for upload_post. The key was set, --posting-status said
"ALLOW upload_post (10 left today)", the live check said
"OK upload_post as profile 'default'", and not one clip ever went
through it.

The second half of this is the reason it could not simply be appended:
one Upload-Post call already reaches Instagram, Facebook, X, TikTok and
YouTube. Posting those again with the direct publishers is the same
Reel twice on the same account minutes apart.
"""

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (_REPO, os.path.join(_REPO, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

from utils.clip_queue import (  # noqa: E402
    CLIP_PLATFORMS,
    UPLOAD_POST_COVERS,
)


def test_upload_post_is_in_the_clip_pipeline_at_all():
    """The whole bug in one assertion."""
    assert "upload_post" in CLIP_PLATFORMS


def test_it_runs_before_the_platforms_it_covers():
    """Order is what makes the de-duplication possible: what it covers
    can only be skipped if it has already run."""
    order = list(CLIP_PLATFORMS)
    assert order[0] == "upload_post"
    for name in ("instagram", "facebook", "youtube_shorts"):
        assert order.index("upload_post") < order.index(name)


def test_the_overlap_is_declared():
    """One call reaches all of these, so all of these are candidates for
    a double post."""
    for name in ("instagram", "facebook", "tiktok", "x", "youtube_shorts"):
        assert name in UPLOAD_POST_COVERS


def test_the_platforms_it_does_not_cover_are_not_suppressed():
    """Rumble is not in it - that is the uncensored full-VOD
    destination and has nothing to do with this. Neither are the
    zernio routes, which upload_post replaced rather than duplicated."""
    for name in ("rumble", "zernio_twitter", "zernio_tiktok", "reddit"):
        assert name not in UPLOAD_POST_COVERS


def test_a_covered_platform_is_skipped_after_upload_post_posts():
    """The de-duplication itself, read off the source: a platform in
    `covered` must be skipped BEFORE anything else happens to it, or
    the queue records an attempt that never ran."""
    body = open(os.path.join(_REPO, "auto_uploader", "utils",
                             "clip_queue.py"), encoding="utf-8").read()

    loop = body.index("for platform in platforms:")
    skip = body.index("if platform in covered:", loop)
    already = body.index("_already_posted(queue, platform", loop)
    assert skip < already, \
        "the covered check has to come first or a skipped platform is queued"
    assert "not posting it twice" in body


def test_only_enabled_platforms_are_suppressed():
    """The real, polled per-platform result (see publish()'s `detail`
    and publishers.upload_post.platforms_reached) is what decides
    `covered` now - not a guess from this project's own config. But
    when there is truly nothing real to read (a dry run, an old-shaped
    response with no polled results), it still falls back to the old
    rule: suppressing a platform this project has switched off would be
    suppressing nothing, and would read as though upload_post had
    covered it."""
    body = open(os.path.join(_REPO, "auto_uploader", "utils",
                             "clip_queue.py"), encoding="utf-8").read()

    # offer()'s own upload_post branch, not publish()'s (the file has
    # two `if platform == "upload_post":` blocks now - one decides
    # what publish() reports back, the other decides what offer() does
    # with it).
    marker = 'covered = detail.get("covers")'
    assert marker in body
    block = body[body.index(marker):]
    assert '.get("enabled")' in block[:400]
