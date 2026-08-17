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
    # Nor whisper. "Nothing to censor" is what censor_video answers by
    # handing back the path it was given - see _censored_clip.
    monkeypatch.setattr("utils.censor.censor_video",
                        lambda path, *a, **k: _Unchanged(path))
    return clip_queue


class _Unchanged:
    """A censor pass that found nothing."""

    def __init__(self, path):
        self.output_path = path
        self.violation_count = 0


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


def test_an_expired_token_is_a_wait_not_a_failure(tmp_path, monkeypatch):
    """"FAIL 8" reads as eight broken clips. The true answer is one
    expired credential and eight clips waiting on it, and which of those
    it is decides whether you look at the code or at a token."""
    import utils.clip_queue as clip_queue
    from publishers.errors import NotConfigured

    written = []
    monkeypatch.setattr(clip_queue, "_journal",
                        lambda cfg, status, platform, path, detail="":
                        written.append(status))

    def expired(*a, **k):
        raise NotConfigured("Facebook cannot publish a Reel with this token: "
                            "Session has expired")

    monkeypatch.setattr(clip_queue, "publish", expired)
    monkeypatch.setattr(clip_queue, "_publisher",
                        lambda platform, config: type(
                            "P", (), {"ready": lambda self: True,
                                      "supports_reels": True})())

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    posting = {"enabled": True, "queue_path": str(tmp_path / "q.json"),
               "state_path": str(tmp_path / "s.json"),
               "platforms": {"facebook": {"enabled": True, "max_per_day": 10,
                                          "min_minutes_between": 0}}}

    clip_queue.offer(posting, {"logs_folder": str(tmp_path)}, str(clip),
                     "cap", platforms=("facebook",))

    assert "FAIL" not in written, \
        "an expired credential was counted as a failed post"
    assert "wait" in written


# ═════════════════════════════════════════════════════════════════════════════
# THE CAPTION IS WRITTEN WHEN IT POSTS, NOT WHEN IT IS QUEUED
#
# It used to be composed at enqueue and stored on the job, so a clip queued
# before a wording fix kept the old text for as long as it sat in the queue.
# X posts one clip an hour, so a backlog kept publishing pre-fix captions
# for hours after the fix had landed - three real posts went out reading
# "vertical Stackswopo Love Yall 20250914 204409 - Clip 03", and an
# Instagram post carried a "LINK IN BIO" line already deleted from the
# template.
# ═════════════════════════════════════════════════════════════════════════════

OLD_CONFIG = {"instagram": {"caption_template":
                            "{title} - YESTERDAYS WORDING"},
              "facebook": {}, "clips": {}}
NEW_CONFIG = {"instagram": {"caption_template": "{title} #stackswopo"},
              "facebook": {}, "clips": {}}


def test_a_queued_clip_posts_todays_wording_not_the_day_it_was_queued(
        publisher, posting, clips):
    """THE regression. Queue two under the old template, fix the template,
    and the one still waiting must go out with the new one."""
    publisher.offer(posting, OLD_CONFIG, clips[0], platforms=("instagram",))
    publisher.offer(posting, OLD_CONFIG, clips[1], platforms=("instagram",))

    # The first went straight out and carries the old wording - it is
    # already published and nothing can change that.
    assert "YESTERDAYS WORDING" in FakePublisher.posted[0][1]

    _rewind(posting, minutes=30)
    publisher.drain(posting, NEW_CONFIG, quiet=True)

    _name, caption = FakePublisher.posted[-1]
    assert "YESTERDAYS WORDING" not in caption, \
        "the queued clip published a caption written before the fix"
    assert "#stackswopo" in caption


def test_a_caption_that_cannot_be_rebuilt_falls_back_to_the_stored_one(
        publisher, posting, clips, monkeypatch):
    """Composing a caption must never be the reason a clip does not go
    out. The stored one is the fallback, which is what used to be posted
    anyway."""
    publisher.offer(posting, NEW_CONFIG, clips[0], platforms=("instagram",))
    publisher.offer(posting, NEW_CONFIG, clips[1], platforms=("instagram",))

    def explode(*_a, **_k):
        raise RuntimeError("no sidecar, no filename, nothing")

    monkeypatch.setattr(publisher, "caption_for", explode)

    _rewind(posting, minutes=30)
    posted = publisher.drain(posting, NEW_CONFIG, quiet=True)

    assert posted == {"instagram": 1}, "a caption failure lost the clip"
    assert FakePublisher.posted[-1][1], "it posted with no caption at all"


def test_recaption_rewrites_what_is_still_waiting(publisher, posting, clips):
    """So the backlog can be SEEN to be fixed rather than taken on trust."""
    publisher.offer(posting, OLD_CONFIG, clips[0], platforms=("instagram",))
    publisher.offer(posting, OLD_CONFIG, clips[1], platforms=("instagram",))

    changed = publisher.recaption(posting, NEW_CONFIG)

    assert len(changed) == 1, "the one waiting clip was not reworded"
    _platform, _clip, before, after = changed[0]
    assert "YESTERDAYS WORDING" in before
    assert "YESTERDAYS WORDING" not in after

    # And it sticks: a fresh read of the queue file sees the new text.
    from job_queue import ACTIVE_STATES, JobQueue

    reopened = JobQueue(path=posting["queue_path"])
    waiting = reopened.list_jobs(ACTIVE_STATES)
    assert waiting and "YESTERDAYS WORDING" not in waiting[0].caption


def test_recaption_says_nothing_when_nothing_needs_it(publisher, posting,
                                                      clips):
    """A command that always reports work done is a command nobody reads."""
    publisher.offer(posting, NEW_CONFIG, clips[0], platforms=("instagram",))
    publisher.offer(posting, NEW_CONFIG, clips[1], platforms=("instagram",))

    assert publisher.recaption(posting, NEW_CONFIG) == []


# ═════════════════════════════════════════════════════════════════════════════
# THE CLIP'S AUDIO, NOT JUST ITS CAPTION
#
# Clips are cut from the RAW stream - every call site passes the original
# video, never the censored copy - and nothing bleeped them afterwards
# either. Only the caption TEXT was cleaned. So Shorts went to YouTube
# carrying whatever was actually said, on the one channel where that is a
# strike rather than a deleted post.
# ═════════════════════════════════════════════════════════════════════════════

def test_a_short_is_bleeped_before_it_goes_to_youtube(publisher, tmp_path,
                                                      monkeypatch):
    from utils import clip_queue

    clean = tmp_path / "clean.mp4"
    clean.write_bytes(b"bleeped")
    raw = tmp_path / "raw.mp4"
    raw.write_bytes(b"not bleeped")

    class Result:
        output_path = str(clean)
        violation_count = 3

    monkeypatch.setitem(sys.modules, "_stub", None)
    monkeypatch.setattr("utils.censor.censor_video",
                        lambda *a, **k: Result())

    upload, temporary = clip_queue._censored_clip(
        "youtube_shorts", str(raw), {"general": {}})

    assert upload == str(clean), "the raw clip was about to go to YouTube"
    assert temporary == str(clean), "the censored copy would be left behind"


def test_instagram_bleeps_slurs_but_not_ordinary_swearing(publisher):
    """It does not demonetise over language, so bleeping every swear for
    it would flatten the voice the channel is there for. But it REMOVED a
    post under hateful conduct, and those removals escalate to a disabled
    account - so slurs are a different question from swearing."""
    from utils.clip_queue import CENSOR_AUDIO_DEFAULTS, _CENSOR_SCOPES

    assert CENSOR_AUDIO_DEFAULTS["instagram"] == "slurs"
    assert _CENSOR_SCOPES["slurs"] == ("hate_speech",)


def test_rumble_audio_is_never_touched(publisher):
    """The uncensored channel is the point of the split."""
    from utils.clip_queue import CENSOR_AUDIO_DEFAULTS

    assert "rumble" not in CENSOR_AUDIO_DEFAULTS


def test_censoring_can_be_turned_off_per_platform(publisher, tmp_path):
    from utils import clip_queue

    raw = tmp_path / "raw.mp4"
    raw.write_bytes(b"x")

    assert clip_queue._censored_clip(
        "youtube_shorts", str(raw),
        {"youtube_shorts": {"censor_uploads": False}}) == (str(raw), "")


def test_a_clip_that_cannot_be_censored_is_not_posted_uncensored(
        publisher, tmp_path, monkeypatch):
    """The one failure worth losing a post over. Falling back to the raw
    audio on the platform that asked for it is how a channel gets a
    strike from a tool that was meant to prevent one."""
    from utils import clip_queue

    raw = tmp_path / "raw.mp4"
    raw.write_bytes(b"x")

    def explode(*_a, **_k):
        raise RuntimeError("no whisper model")

    monkeypatch.setattr("utils.censor.censor_video", explode)

    upload, _temp = clip_queue._censored_clip("youtube_shorts", str(raw),
                                              {"general": {}})

    assert upload == "", "it was about to post the uncensored clip anyway"


def test_nothing_flagged_means_no_second_copy(publisher, tmp_path,
                                              monkeypatch):
    """A clip with no profanity in it must not be re-encoded for nothing."""
    from utils import clip_queue

    raw = tmp_path / "raw.mp4"
    raw.write_bytes(b"x")

    class Result:
        output_path = str(raw)
        violation_count = 0

    monkeypatch.setattr("utils.censor.censor_video", lambda *a, **k: Result())

    assert clip_queue._censored_clip("youtube_shorts", str(raw),
                                     {"general": {}}) == (str(raw), "")


# ── ...and a Reel is a clip too ───────────────────────────────────────
#
# _censored_clip only ever ran on the post_clip path, which is Shorts.
# Instagram and Facebook go out through post_reel_from_file and came
# straight off the raw cut, so CENSOR_AUDIO_DEFAULTS said "slurs" for
# both of them and nothing read it. A slur Shorts muted reached
# Instagram intact - and a Reel came down for hateful conduct.

def test_a_reel_is_censored_before_it_is_posted(publisher, tmp_path,
                                                monkeypatch):
    from utils import clip_queue, social_promoter

    raw = tmp_path / "raw.mp4"
    raw.write_bytes(b"not bleeped")
    clean = tmp_path / "clean.mp4"
    clean.write_bytes(b"bleeped")

    monkeypatch.setattr("utils.censor.censor_video",
                        lambda *a, **k: type(
                            "R", (), {"output_path": str(clean),
                                      "violation_count": 2})())
    framed = []
    monkeypatch.setattr(social_promoter, "_vertical_copy",
                        lambda path, s, c: (framed.append(path) or path, ""))

    assert clip_queue.publish("instagram", str(raw), "cap", {"general": {}})

    assert framed == [str(clean)], \
        "the re-frame was made from the raw audio, not the censored copy"
    assert FakePublisher.posted[-1][0] == "clean.mp4"


def test_a_reel_that_cannot_be_censored_is_not_posted_uncensored(
        publisher, tmp_path, monkeypatch):
    from utils import clip_queue

    raw = tmp_path / "raw.mp4"
    raw.write_bytes(b"x")

    def explode(*_a, **_k):
        raise RuntimeError("no whisper model")

    monkeypatch.setattr("utils.censor.censor_video", explode)

    assert clip_queue.publish("instagram", str(raw), "cap",
                              {"general": {}}) is False
    assert not FakePublisher.posted


def test_the_censored_copy_of_a_reel_is_cleaned_up(publisher, tmp_path,
                                                   monkeypatch):
    from utils import clip_queue

    raw = tmp_path / "raw.mp4"
    raw.write_bytes(b"x")
    clean = tmp_path / "clean.mp4"
    clean.write_bytes(b"bleeped")

    monkeypatch.setattr("utils.censor.censor_video",
                        lambda *a, **k: type(
                            "R", (), {"output_path": str(clean),
                                      "violation_count": 1})())

    assert clip_queue.publish("instagram", str(raw), "cap", {"general": {}})

    assert not clean.exists(), "a censored copy per clip fills the disk"
    assert raw.exists(), "the clip itself is not this function's to delete"


# ═════════════════════════════════════════════════════════════════════════════
# RUMBLE HAVING SEEN A CLIP SAYS NOTHING ABOUT YOUTUBE
#
# The offer to Instagram / Facebook / Shorts / X / TikTok used to sit inside
# `if newly_uploaded:` in main.py, alongside the announcement. So a clip
# Rumble skipped as a duplicate was never offered to any of the other five
# platforms either - and a clip that reached Rumble but never reached
# Shorts could never reach Shorts, on any later run. Shorts stayed empty
# while Rumble filled up.
#
# What makes offering it again safe is here: the queue answers per
# platform, not per clip.
# ═════════════════════════════════════════════════════════════════════════════

def test_a_clip_posted_to_one_platform_is_still_offered_to_the_others(
        publisher, posting, clips):
    """The guarantee the un-gating leans on."""
    posting["platforms"]["youtube_shorts"] = {"enabled": True,
                                              "daily_cap": 50,
                                              "min_minutes_between": 0}

    first = publisher.offer(posting, CONFIG, clips[0],
                            platforms=("instagram",))
    assert first["instagram"] == "posted"

    # The same clip, offered again now that another platform is on.
    again = publisher.offer(posting, CONFIG, clips[0],
                            platforms=("instagram", "youtube_shorts"))

    assert again["instagram"] == "skipped: already posted", \
        "it was about to post the same clip to Instagram twice"
    assert again["youtube_shorts"] != "skipped: already posted", \
        "a platform that never saw this clip was skipped anyway"


def test_offering_the_same_clip_twice_never_double_posts(publisher, posting,
                                                         clips):
    """Every pass of the watcher offers whatever it is holding. That must
    be free when there is nothing new to do."""
    publisher.offer(posting, CONFIG, clips[0], platforms=("instagram",))
    before = len(FakePublisher.posted)

    for _ in range(3):
        publisher.offer(posting, CONFIG, clips[0], platforms=("instagram",))

    assert len(FakePublisher.posted) == before


# ═════════════════════════════════════════════════════════════════════════════
# ONE CLIP, TWO PATHS, TWO UPLOADS
#
# "Clip 01" is on the Shorts channel twice, byte for byte identical,
# eighteen seconds each. The queue matched jobs on the exact path string,
# and one clip legitimately has more than one path: watch_folder/X.mp4 when
# it is already 9:16, censored/_vertical_X.mp4 when a re-frame happened.
# Two paths, two jobs, two uploads - and the Shorts publisher has no dedup
# of its own to catch it.
# ═════════════════════════════════════════════════════════════════════════════

def test_the_reframed_copy_is_the_same_clip(publisher):
    from utils.clip_queue import clip_key

    assert clip_key("/watch/Wifi Cooked - Clip 01.mp4") == \
        clip_key("/censored/_vertical_Wifi Cooked - Clip 01.mp4")
    assert clip_key("/a/Clip 01.mp4") != clip_key("/a/Clip 02.mp4")


def test_a_clip_is_not_posted_twice_under_its_other_name(publisher, posting,
                                                         tmp_path):
    """THE duplicate. Offer it as the plain clip, then as the re-framed
    copy - the second must recognise the first."""
    plain = tmp_path / "Wifi Cooked - Clip 01.mp4"
    plain.write_bytes(b"x")
    reframed = tmp_path / "_vertical_Wifi Cooked - Clip 01.mp4"
    reframed.write_bytes(b"x")

    first = publisher.offer(posting, CONFIG, str(plain),
                            platforms=("instagram",))
    assert first["instagram"] == "posted"

    again = publisher.offer(posting, CONFIG, str(reframed),
                            platforms=("instagram",))

    assert again["instagram"] == "skipped: already posted", \
        "the re-framed copy uploaded the same clip a second time"
    assert len(FakePublisher.posted) == 1


def test_two_different_clips_are_still_two_clips(publisher, posting, clips):
    """The normalisation must not collapse a whole stream into one job."""
    publisher.offer(posting, CONFIG, clips[0], platforms=("instagram",))
    _rewind(posting, minutes=30)
    publisher.offer(posting, CONFIG, clips[1], platforms=("instagram",))

    assert len(FakePublisher.posted) == 2
