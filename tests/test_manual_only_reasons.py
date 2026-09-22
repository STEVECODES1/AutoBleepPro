"""
"Queued for a human" has to mean something was queued.

facebook_group is in ALWAYS_MANUAL because Meta has no approved Graph
API route for posting to a group - not because this project chose to
hold it back. The announcer knows that and explicitly does NOT write it
to the manual queue ("no route at all, and nothing useful to hand a
human").

The guard said the same sentence for both cases, so --posting-status
read:

    BLOCK facebook_group   facebook_group is manual-approval only -
                           queued for a human, never auto-posted

...and anybody following that to logs/manual_posts.txt found nothing
there, because nothing is ever written.
"""

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (_REPO, os.path.join(_REPO, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

from publish_guard import ALWAYS_MANUAL, PublishGuard  # noqa: E402


def _guard(tmp_path, platforms):
    return PublishGuard({"enabled": True, "platforms": platforms},
                        str(tmp_path / "state.json"))


def test_facebook_group_says_why_it_can_never_be_automated(tmp_path):
    """Not "we chose not to" - there is no endpoint. Meta removed
    publish_to_groups, and a reader who does not know that will keep
    asking for it to be switched on."""
    guard = _guard(tmp_path, {"facebook_group": {"enabled": True,
                                                 "manual_approval_only": True}})
    reason = guard.check("facebook_group").reason

    assert "no approved API route" in reason
    assert "publish_to_groups" in reason
    assert "never be automated" in reason


def test_it_says_where_the_post_went(tmp_path):
    """A blocked platform with no next step is just a wall. The text is
    written out and pinged to Discord, so posting it is a paste."""
    reason = _guard(tmp_path, {"facebook_group": {"enabled": True}}).check(
        "facebook_group").reason
    assert "manual posts file" in reason
    assert "Discord" in reason


def test_a_deliberately_manual_platform_still_says_queued(tmp_path):
    """Reddit is held back by CHOICE - it has a supported API and its
    posts really are written to the manual file. That message was
    correct and must stay correct."""
    guard = _guard(tmp_path, {"reddit": {"enabled": True,
                                         "manual_approval_only": True}})
    reason = guard.check("reddit").reason

    assert "queued for a human" in reason
    assert "manual posts file" in reason
    assert "publish_to_groups" not in reason, \
        "Reddit has a working API - that is the whole difference"


def test_reddit_is_not_permanently_manual():
    """The distinction the two messages rest on. Reddit can be switched
    to automatic once a healthy account exists; facebook_group cannot,
    whatever the config says."""
    assert "facebook_group" in ALWAYS_MANUAL
    assert "reddit" not in ALWAYS_MANUAL


def test_the_config_flag_cannot_turn_the_hard_block_off(tmp_path):
    """enabled: true and manual_approval_only: false, and it is still
    blocked - because the reason is Meta's API surface, not a setting."""
    guard = _guard(tmp_path, {"facebook_group": {"enabled": True,
                                                 "manual_approval_only": False}})
    decision = guard.check("facebook_group")

    assert not decision
    assert "no approved API route" in decision.reason


# ── not automatable is not the same as lost ───────────────────────────

def test_the_group_post_is_written_out_rather_than_dropped():
    """Meta removed publish_to_groups, so there is no endpoint and this
    cannot be automated. It was therefore skipped entirely - the comment
    said there was "nothing useful to hand a human", which was wrong.
    The finished text is exactly what a person needs, and writing it out
    makes posting a paste instead of a rewrite."""
    body = open(os.path.join(_REPO, "auto_uploader", "utils",
                             "social_promoter.py"), encoding="utf-8").read()

    block = body[body.index('if platform == "facebook_group":'):]
    head = block[:1400]

    assert "queue_manual_post(platform" in head, \
        "the group post is dropped instead of being queued"
    assert "continue   # no route at all" not in body
