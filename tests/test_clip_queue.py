"""
A clip the guard defers must come back, not disappear.

This is the regression test for the day that produced ten clips and one
Instagram Reel: the other nine hit the 25-minute spacing rule, printed the
reason, and were dropped on the floor.
"""

import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))


@pytest.fixture
def posting(tmp_path):
    return {
        "enabled": True,
        "kill_switch_file": str(tmp_path / "STOP_POSTING"),
        "state_path": str(tmp_path / "posting_state.json"),
        "queue_path": str(tmp_path / "clip_jobs.json"),
        "platforms": {
            "instagram": {"enabled": True, "daily_cap": 50,
                          "min_minutes_between": 25},
            "facebook": {"enabled": True, "daily_cap": 10,
                         "min_minutes_between": 80},
        },
        "circuit_breaker": {"consecutive_failures": 3},
    }


@pytest.fixture
def clips(tmp_path):
    made = []
    for n in range(3):
        path = tmp_path / f"clip{n:02d}.mp4"
        path.write_bytes(b"not really a video")
        made.append(str(path))
    return made


class FakePublisher:
    """Stands in for the Meta publishers - records, never uploads."""
    supports_reels = True

    posted: list = []
    fails = False

    def __init__(self, *_args, **_kwargs):
        pass

    def ready(self):
        return True

    def post_reel_from_file(self, path, caption="", share_to_feed=True):
        if FakePublisher.fails:
            return False
        FakePublisher.posted.append((os.path.basename(path), caption))
        return True


@pytest.fixture
def publisher(monkeypatch):
    from utils import clip_queue, social_promoter

    FakePublisher.posted = []
    FakePublisher.fails = False
    monkeypatch.setattr(social_promoter, "_publisher_for",
                        lambda platform, config: FakePublisher())
    # No ffmpeg in the test environment, and framing is not what is
    # under test - post the file as it is.
    monkeypatch.setattr(social_promoter, "_vertical_copy",
                        lambda path, ig, cl: (path, ""))
    return clip_queue


CONFIG = {"instagram": {"caption_template": "{title} #stackswopo"},
          "facebook": {}, "clips": {}}


def test_a_deferred_clip_is_kept_not_dropped(publisher, posting, clips):
    """The exact failure: clip one posts, clip two is inside the spacing."""
    first = publisher.offer(posting, CONFIG, clips[0], platforms=("instagram",))
    second = publisher.offer(posting, CONFIG, clips[1], platforms=("instagram",))

    assert first["instagram"] == "posted"
    assert second["instagram"] == "queued", \
        "a clip inside the spacing window was thrown away again"
    assert len(FakePublisher.posted) == 1


def test_the_queued_clip_goes_out_once_the_wait_is_up(publisher, posting, clips):
    publisher.offer(posting, CONFIG, clips[0], platforms=("instagram",))
    publisher.offer(posting, CONFIG, clips[1], platforms=("instagram",))
    assert len(FakePublisher.posted) == 1

    # Nothing is due yet.
    assert publisher.drain(posting, CONFIG, quiet=True) == {}

    _rewind(posting, minutes=30)
    posted = publisher.drain(posting, CONFIG, quiet=True)

    assert posted == {"instagram": 1}
    assert [name for name, _ in FakePublisher.posted] == \
        ["clip00.mp4", "clip01.mp4"]


def test_draining_respects_the_spacing_between_queued_clips(publisher, posting,
                                                            clips):
    """Two waiting clips do not both go out the moment one becomes due."""
    for clip in clips:
        publisher.offer(posting, CONFIG, clip, platforms=("instagram",))
    _rewind(posting, minutes=30)

    posted = publisher.drain(posting, CONFIG, quiet=True)

    assert posted == {"instagram": 1}, "the queue fired a burst"


def test_a_disabled_platform_is_not_queued(publisher, posting, clips):
    """Nothing to wait for means nothing to keep - otherwise turning the
    platform back on would fire a month of clips at once."""
    posting["platforms"]["instagram"]["enabled"] = False

    outcome = publisher.offer(posting, CONFIG, clips[0], platforms=("instagram",))

    assert outcome["instagram"].startswith("skipped")
    assert publisher.drain(posting, CONFIG, quiet=True) == {}


def test_the_kill_switch_is_not_a_wait(publisher, posting, clips, tmp_path):
    (tmp_path / "STOP_POSTING").write_text("stop")

    outcome = publisher.offer(posting, CONFIG, clips[0], platforms=("instagram",))

    assert outcome["instagram"].startswith("skipped")


def test_a_clip_deleted_before_its_turn_does_not_retry_forever(
        publisher, posting, clips):
    publisher.offer(posting, CONFIG, clips[0], platforms=("instagram",))
    publisher.offer(posting, CONFIG, clips[1], platforms=("instagram",))
    os.remove(clips[1])
    _rewind(posting, minutes=30)

    assert publisher.drain(posting, CONFIG, quiet=True) == {}
    from job_queue import JobQueue
    queue = JobQueue(path=posting["queue_path"])
    assert queue.counts().get("failed", 0) == 1


def test_a_stale_clip_is_dropped_rather_than_posted_late(publisher, posting,
                                                         clips):
    publisher.offer(posting, CONFIG, clips[0], platforms=("instagram",))
    publisher.offer(posting, CONFIG, clips[1], platforms=("instagram",))

    from job_queue import JobQueue
    queue = JobQueue(path=posting["queue_path"])
    for job in queue.list_jobs():
        job.created_at -= publisher.MAX_DEFERRED_AGE_S + 60
    queue._save()
    _rewind(posting, minutes=30)

    assert publisher.drain(posting, CONFIG, quiet=True) == {}
    assert len(FakePublisher.posted) == 1


def test_the_same_clip_is_never_posted_twice(publisher, posting, clips):
    publisher.offer(posting, CONFIG, clips[0], platforms=("instagram",))
    again = publisher.offer(posting, CONFIG, clips[0], platforms=("instagram",))

    assert again["instagram"] == "skipped: already posted"
    assert len(FakePublisher.posted) == 1


def test_facebook_gets_the_clip_as_a_reel(publisher, posting, clips):
    outcome = publisher.offer(posting, CONFIG, clips[0],
                              platforms=("instagram", "facebook"))

    assert outcome == {"instagram": "posted", "facebook": "posted"}
    assert len(FakePublisher.posted) == 2


def test_facebook_falls_back_to_the_instagram_caption(publisher, posting, clips):
    """One voice across both accounts; a second template would drift."""
    caption = publisher.caption_for("facebook", clips[0], "fallback", CONFIG)

    assert "#stackswopo" in caption


def _rewind(posting, minutes):
    """Make `minutes` appear to have passed.

    Both clocks have to move: the guard's, which records when each post
    went out, and the queue's, which records when a deferred job is due
    back. They are set from each other, so shifting only one would leave
    the pair disagreeing in a way they never do in real time.
    """
    import json

    shift = minutes * 60
    with open(posting["state_path"], "r", encoding="utf-8") as f:
        state = json.load(f)
    for platform, stamps in (state.get("posts") or {}).items():
        state["posts"][platform] = [t - shift for t in stamps]
    with open(posting["state_path"], "w", encoding="utf-8") as f:
        json.dump(state, f)

    from job_queue import JobQueue

    queue = JobQueue(path=posting["queue_path"])
    for job in queue.list_jobs():
        if job.not_before:
            job.not_before -= shift
    queue._save()
