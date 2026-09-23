"""
"I haven't seen a single X clip or instagram... it's not uploading."

upload-post.com's own docs (docs.upload-post.com/api/upload-status,
confirmed by reading the live page) describe an async job: the initial
POST to /api/upload returns an ack with a request_id and says to poll
GET /api/uploadposts/status?request_id=... for the real, per-platform
outcome - "results": [{"platform": ..., "success": ...}, ...].

This project's own code already had _STATUS_PATH, _POLL_INTERVAL and
_POLL_TIMEOUT defined for exactly that, unused, since before this file
existed - the polling was clearly intended and never wired up. Instead,
the ack itself ("Upload initiated successfully in background...") was
treated as the outcome: every platform in the job was marked "posted"
the moment upload-post accepted the file, whether or not any of them
actually went through on upload-post's own side - an expired token, an
account that was never really connected, anything. From the terminal
that is indistinguishable from "it's not uploading" with nothing to
point at.

clip_queue.offer()'s "covers" list had the same shape of problem one
level up: which platforms upload_post is treated as having reached was
a guess from THIS PROJECT's own `posting.platforms.<name>.enabled`
flags, not from anything upload-post.com actually said. A platform
"enabled" here but not genuinely connected there read as covered and
silently skipped the per-platform fallback that might otherwise have
caught it.
"""

from __future__ import annotations

import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
for _path in (_REPO, _UPLOADER):
    if _path not in sys.path:
        sys.path.insert(0, _path)


# ═════════════════════════════════════════════════════════════════════════════
# platforms_reached(): reading real results, never guessing
# ═════════════════════════════════════════════════════════════════════════════

def test_a_platform_marked_success_is_reached():
    from publishers.upload_post import platforms_reached

    result = {"status": "completed", "results": [
        {"platform": "instagram", "success": True},
        {"platform": "twitter", "success": True},
    ]}
    assert platforms_reached(result) == {"instagram", "x"}


def test_a_platform_marked_failed_is_not_reached():
    """The exact case: upload-post accepted the file and instagram
    still did not go out."""
    from publishers.upload_post import platforms_reached

    result = {"status": "completed", "results": [
        {"platform": "instagram", "success": False,
         "message": "token expired"},
        {"platform": "facebook", "success": True},
    ]}
    assert platforms_reached(result) == {"facebook"}


def test_a_skipped_platform_is_not_reached():
    """skip_reason: profile_platform_not_configured - the account was
    never actually connected on upload-post's side, whatever this
    project's own config says."""
    from publishers.upload_post import platforms_reached

    result = {"status": "completed", "results": [
        {"platform": "twitter", "success": False, "skipped": True,
         "skip_reason": "profile_platform_not_configured"},
    ]}
    assert platforms_reached(result) == set()


def test_a_bare_ack_with_no_results_reaches_nothing():
    """The exact old failure: the initial 'accepted' response, before
    polling ever resolves it - must not be read as success for anyone."""
    from publishers.upload_post import platforms_reached

    ack = {"success": True, "message": "Upload initiated successfully "
          "in background.", "request_id": "abc123", "total_platforms": 4}
    assert platforms_reached(ack) == set()


def test_none_and_malformed_input_reach_nothing():
    from publishers.upload_post import platforms_reached

    assert platforms_reached(None) == set()
    assert platforms_reached({}) == set()
    assert platforms_reached({"results": "not a list"}) == set()
    assert platforms_reached({"results": ["not a dict"]}) == set()


def test_youtube_alias_resolves_to_this_projects_own_name():
    """youtube_shorts and youtube both alias to upload-post's "youtube"
    - the reverse map has to pick the name this project actually uses."""
    from publishers.upload_post import platforms_reached

    result = {"results": [{"platform": "youtube", "success": True}]}
    assert platforms_reached(result) == {"youtube_shorts"}


def test_a_platform_this_project_does_not_use_is_ignored():
    """threads, pinterest, etc. are real upload-post platforms this
    project never posts to - success there must not leak into a set
    this project's own code will act on."""
    from publishers.upload_post import platforms_reached

    result = {"results": [{"platform": "threads", "success": True}]}
    assert platforms_reached(result) == set()


# ═════════════════════════════════════════════════════════════════════════════
# _resolve_final_result(): the polling loop itself
# ═════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def publisher(monkeypatch):
    from publishers.upload_post import UploadPostPublisher

    monkeypatch.setenv("UPLOAD_POST_API_KEY", "k")
    monkeypatch.setenv("UPLOAD_POST_USER", "u")
    return UploadPostPublisher({})


def test_a_synchronous_response_is_returned_unchanged(publisher, monkeypatch):
    """No request_id at all - nothing to poll, and polling must not be
    attempted or block on one."""
    def explode(*a, **k):
        raise AssertionError("should not have tried to poll")

    monkeypatch.setattr(publisher, "check_status", explode)

    result = {"success": True, "message": "posted"}
    assert publisher._resolve_final_result(result) is result


def test_polling_stops_the_moment_the_job_is_terminal(publisher, monkeypatch):
    import publishers.upload_post as up

    monkeypatch.setattr(up.time, "sleep", lambda s: None)
    calls = []

    responses = [
        {"status": "processing", "completed": 1, "total": 3},
        {"status": "processing", "completed": 2, "total": 3},
        {"status": "completed", "completed": 3, "total": 3,
         "results": [{"platform": "instagram", "success": True}]},
    ]

    def fake_check(request_id):
        calls.append(request_id)
        return responses[len(calls) - 1]

    monkeypatch.setattr(publisher, "check_status", fake_check)

    final = publisher._resolve_final_result({"request_id": "abc123"})

    assert len(calls) == 3
    assert final["status"] == "completed"
    assert final["results"][0]["success"] is True


def test_a_failed_job_is_still_returned_not_swallowed(publisher, monkeypatch):
    import publishers.upload_post as up

    monkeypatch.setattr(up.time, "sleep", lambda s: None)
    monkeypatch.setattr(publisher, "check_status",
                        lambda rid: {"status": "failed",
                                     "message": "video rejected"})

    final = publisher._resolve_final_result({"request_id": "abc123"})
    assert final["status"] == "failed"


def test_a_timeout_returns_the_last_seen_status_not_none(publisher, monkeypatch):
    """A clip that is still genuinely processing must not be reported as
    a failure just because polling gave up."""
    import publishers.upload_post as up

    monkeypatch.setattr(up, "_POLL_TIMEOUT", 0.05)
    monkeypatch.setattr(up, "_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(up.time, "sleep", lambda s: None)

    last = {"status": "processing", "completed": 1, "total": 3}
    monkeypatch.setattr(publisher, "check_status", lambda rid: last)

    final = publisher._resolve_final_result({"request_id": "abc123"})
    assert final == last


def test_a_poll_that_cannot_be_reached_keeps_the_earlier_ack(publisher,
                                                              monkeypatch):
    """check_status() failing (network, bad JSON) must not turn into a
    reported failure - it falls back to whatever was known before."""
    import publishers.upload_post as up

    monkeypatch.setattr(up, "_POLL_TIMEOUT", 0.03)
    monkeypatch.setattr(up, "_POLL_INTERVAL", 0.01)
    monkeypatch.setattr(up.time, "sleep", lambda s: None)
    monkeypatch.setattr(publisher, "check_status", lambda rid: None)

    ack = {"success": True, "request_id": "abc123"}
    final = publisher._resolve_final_result(ack)
    assert final is ack


# ═════════════════════════════════════════════════════════════════════════════
# clip_queue.publish(): the tightened success check
# ═════════════════════════════════════════════════════════════════════════════

def test_upload_post_reporting_zero_real_successes_is_not_a_post(monkeypatch,
                                                                  tmp_path):
    """The exact bug: upload-post accepts the file (an ack, then a
    polled 'completed' job) and every platform in it actually failed.
    That must count as a failed post, not a successful one."""
    from utils import clip_queue

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")

    class FakePublisher:
        supports_link_posts = False

        def ready(self):
            return True

        def post_clip(self, path, caption, dry_run):
            return {"status": "completed", "results": [
                {"platform": "instagram", "success": False,
                 "message": "token expired"},
            ]}

    monkeypatch.setattr(clip_queue, "_publisher",
                        lambda platform, config: FakePublisher())
    monkeypatch.setattr(clip_queue, "_censored_clip",
                        lambda platform, path, config: (path, ""))

    detail = {}
    ok = clip_queue.publish("upload_post", str(video), "caption", {},
                            dry_run=False, detail=detail)

    assert ok is False
    assert detail["covers"] == set()


def test_upload_post_reporting_a_real_success_is_a_post(monkeypatch, tmp_path):
    from utils import clip_queue

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")

    class FakePublisher:
        supports_link_posts = False

        def ready(self):
            return True

        def post_clip(self, path, caption, dry_run):
            return {"status": "completed", "results": [
                {"platform": "instagram", "success": True},
                {"platform": "twitter", "success": False},
            ]}

    monkeypatch.setattr(clip_queue, "_publisher",
                        lambda platform, config: FakePublisher())
    monkeypatch.setattr(clip_queue, "_censored_clip",
                        lambda platform, path, config: (path, ""))

    detail = {}
    ok = clip_queue.publish("upload_post", str(video), "caption", {},
                            dry_run=False, detail=detail)

    assert ok is True
    assert detail["covers"] == {"instagram"}


def test_an_old_shaped_response_with_no_results_still_counts_as_posted(
        monkeypatch, tmp_path):
    """Backward compatible: a dry run or an SDK reply that predates
    polling has no `results` at all - the old 'the ack means it worked'
    behaviour still applies rather than a newly-invented failure for a
    question this cannot answer."""
    from utils import clip_queue

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")

    class FakePublisher:
        supports_link_posts = False

        def ready(self):
            return True

        def post_clip(self, path, caption, dry_run):
            return {"success": True, "message": "posted"}

    monkeypatch.setattr(clip_queue, "_publisher",
                        lambda platform, config: FakePublisher())
    monkeypatch.setattr(clip_queue, "_censored_clip",
                        lambda platform, path, config: (path, ""))

    ok = clip_queue.publish("upload_post", str(video), "caption", {},
                            dry_run=False, detail={})
    assert ok is True


def test_other_platforms_are_unaffected_by_detail(monkeypatch, tmp_path):
    """detail is upload_post-specific - a direct publisher's own
    post_clip() outcome must not be reinterpreted through it."""
    from utils import clip_queue

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")

    class FakePublisher:
        supports_link_posts = False

        def ready(self):
            return True

        def post_clip(self, path, caption, dry_run):
            return "https://youtube.example/watch"

    monkeypatch.setattr(clip_queue, "_publisher",
                        lambda platform, config: FakePublisher())
    monkeypatch.setattr(clip_queue, "_censored_clip",
                        lambda platform, path, config: (path, ""))

    ok = clip_queue.publish("youtube_shorts", str(video), "caption", {},
                            dry_run=False, detail={})
    assert ok is True


def test_clip_config_lets_upload_post_see_which_platforms_are_enabled(
        tmp_path):
    """The actual root cause behind "haven't seen a single X clip": every
    caller that builds UploadPostPublisher's config from main._clip_config()
    (real clips) or the announce_upload() literal (full-stream announces)
    passed cfg.posting as a SEPARATE keyword, never inside the config dict
    itself. UploadPostPublisher.post_clip() only ever looks at
    config["posting"]["platforms"] to auto-detect targets - so that lookup
    was always {}, platform_names({}) was always [], and it silently fell
    back to its hardcoded default list (instagram, tiktok, youtube_shorts,
    facebook), which has no "x" in it. Every clip sent through upload_post
    skipped X no matter what posting.platforms.x.enabled said."""
    import sys

    sys.path.insert(0, _UPLOADER)
    from main import _clip_config
    from publishers.upload_post import UploadPostPublisher

    class _General:
        logs_folder = "logs"

    class _YouTube:
        client_secrets_path = ""
        channel = ""

    class _Cfg:
        instagram = {}
        facebook = {}
        clips = {}
        features = {}
        youtube_shorts = {}
        zernio = {}
        posting = {"platforms": {
            "x": {"enabled": True},
            "instagram": {"enabled": True},
        }}
        youtube = _YouTube()
        general = _General()

    built = _clip_config(_Cfg())
    assert "posting" in built, "upload_post cannot see any enabled flags"

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")
    publisher = UploadPostPublisher(built)
    targets = publisher.post_clip(str(video), "caption", dry_run=True)

    assert "x" in targets, (
        "x is enabled in posting.platforms but was not detected as a "
        f"target: {targets}")
