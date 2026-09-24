"""
PostPlanify: the fan-out route without a monthly quota.

upload_post's free plan is 10 uploads a month, so its cap had to be one
clip a day. PostPlanify is a paid plan whose only limit is per account
(6/hour, 25/day). These tests pin what that route must never get wrong:

  - it posts to every connected account this project posts clips to, and
    to nothing the config has switched off,
  - one account refusing does not cost the others their post,
  - a bad key is "not configured", not a failure for the breaker,
  - what it reached is read from what actually happened, and nothing
    after it posts the same clip to the same account again,
  - its audio is censored like every other destination except Rumble.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

from publishers import postplanify  # noqa: E402
from publishers.errors import NotConfigured, PermanentlyRejected  # noqa: E402


# ── a scripted stand-in for the API ─────────────────────────────────────────

class _Response:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.ok = 200 <= status < 300

    def json(self):
        return self._body


def _ok(data, status=200):
    return _Response(status, {"ok": True, "data": data})


def _err(status, code, message):
    return _Response(status, {"ok": False,
                              "error": {"code": code, "message": message}})


ACCOUNTS = [
    {"id": "acc-ig", "workspaceId": "ws", "platform": "INSTAGRAM",
     "accountName": "stackswopomanz"},
    {"id": "acc-tt", "workspaceId": "ws", "platform": "TIKTOK",
     "accountName": "stevebottin"},
    {"id": "acc-x", "workspaceId": "ws", "platform": "X",
     "accountName": "BinScripts"},
    {"id": "acc-li", "workspaceId": "ws", "platform": "LINKEDIN",
     "accountName": "somebody"},
]


class FakeAPI:
    """Answers the four calls the publisher makes, and records them."""

    def __init__(self, accounts=ACCOUNTS):
        self.accounts = list(accounts)
        self.scheduled = []          # payloads sent to POST /posts
        self.uploads = 0
        self.refuse = {}             # socialAccountId -> response
        self.final = {}              # socialAccountId -> post body on poll
        self.accounts_response = None

    # requests.get
    def get(self, url, headers=None, timeout=None):
        assert headers["Authorization"] == "Bearer sk_live_test"
        if url.endswith("/social-accounts"):
            return self.accounts_response or _ok(self.accounts)
        post_id = url.rsplit("/", 1)[-1]
        account = post_id.replace("post-", "")
        return _ok(self.final.get(account,
                                  {"id": post_id, "status": "PUBLISHED",
                                   "logs": []}))

    # requests.post
    def post(self, url, headers=None, files=None, json=None, timeout=None):
        assert headers["Authorization"] == "Bearer sk_live_test"
        if url.endswith("/media/upload"):
            self.uploads += 1
            return _ok({"id": "media-1"}, 201)
        if url.endswith("/posts"):
            self.scheduled.append(json)
            refused = self.refuse.get(json["socialAccountId"])
            if refused is not None:
                return refused
            return _ok({"id": f"post-{json['socialAccountId']}",
                        "status": "SCHEDULED"}, 201)
        raise AssertionError(f"unexpected POST {url}")


@pytest.fixture
def api(monkeypatch):
    fake = FakeAPI()
    monkeypatch.setattr(postplanify, "requests", fake)
    monkeypatch.setattr(postplanify, "_REQUESTS_OK", True)
    monkeypatch.setattr(postplanify, "POLL_INTERVAL_S", 0)
    monkeypatch.setattr(postplanify.time, "sleep", lambda s: None)
    monkeypatch.setenv("POSTPLANIFY_API_KEY", "sk_live_test")
    return fake


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "Stackswopo - Clip 01.mp4"
    path.write_bytes(b"not really a video")
    return str(path)


def _publisher(posting=None, **settings):
    cfg = {"posting": posting or {"platforms": {}}}
    # Never wait on a real clock in a test: every post has either an
    # outcome on the first poll, or is left SCHEDULED on purpose.
    cfg["postplanify"] = dict({"lead_seconds": 0,
                               "poll_timeout_seconds": 1}, **settings)
    return postplanify.PostPlanifyPublisher(cfg)


def _by_platform(result):
    return {entry["platform"]: entry for entry in result["results"]}


# ── which accounts ──────────────────────────────────────────────────────────

def test_every_connected_clip_account_gets_the_clip(api, clip):
    result = _publisher().post_clip(clip, "Title line\n\n#gta #rp")

    assert api.uploads == 1, "the file is uploaded once and reused"
    posted_to = {p["socialAccountId"] for p in api.scheduled}
    assert posted_to == {"acc-ig", "acc-tt", "acc-x"}
    assert postplanify.platforms_reached(result) == \
        {"instagram", "tiktok", "x"}


def test_platforms_this_project_does_not_post_clips_to_are_left_alone(
        api, clip):
    _publisher().post_clip(clip, "t")
    assert "acc-li" not in {p["socialAccountId"] for p in api.scheduled}


def test_a_platform_switched_off_in_config_is_not_posted_to(api, clip):
    posting = {"platforms": {"x": {"enabled": False}}}
    _publisher(posting).post_clip(clip, "t")
    assert "acc-x" not in {p["socialAccountId"] for p in api.scheduled}


def test_a_platform_with_no_config_block_is_still_posted_to():
    """tiktok has no block under posting.platforms today. The account
    being connected on PostPlanify is the opt-in; only an explicit
    enabled: false is a refusal."""
    assert postplanify._enabled({"platforms": {}}, "tiktok")
    assert postplanify._enabled({"platforms": {"tiktok": {}}}, "tiktok")
    assert not postplanify._enabled(
        {"platforms": {"tiktok": {"enabled": False}}}, "tiktok")


# ── the request itself ──────────────────────────────────────────────────────

def test_each_post_carries_the_media_and_a_future_time(api, clip):
    _publisher().post_clip(clip, "t")
    for payload in api.scheduled:
        assert payload["mediaIds"] == ["media-1"]
        assert payload["workspaceId"] == "ws"
        assert payload["scheduledAt"].endswith("Z"), "the API is UTC-only"


def test_tiktok_is_posted_publicly_not_to_drafts(api, clip):
    _publisher().post_clip(clip, "t")
    tiktok = next(p for p in api.scheduled if p["socialAccountId"] == "acc-tt")
    assert tiktok["tiktokParams"]["privacyLevel"] == "PUBLIC_TO_EVERYONE"
    assert "isDraftType" not in tiktok["tiktokParams"]


def test_youtube_gets_the_title_it_requires(api, clip):
    api.accounts = [{"id": "acc-yt", "workspaceId": "ws",
                     "platform": "YOUTUBE", "accountName": "BinScript"}]
    _publisher().post_clip(clip, "He calls someone daddy\n\n#gta")
    params = api.scheduled[0]["youtubeParams"]
    assert params["title"] == "He calls someone daddy"
    assert params["categoryId"] == "20", "Gaming, not People & Blogs"


def test_x_gets_a_caption_x_will_accept(api, clip):
    long_tags = " ".join(f"#tag{n}" for n in range(80))
    _publisher().post_clip(clip, f"The line he said\n\n{long_tags}")
    x = next(p for p in api.scheduled if p["socialAccountId"] == "acc-x")
    ig = next(p for p in api.scheduled if p["socialAccountId"] == "acc-ig")
    assert len(x["caption"]) <= 280
    assert x["caption"].startswith("The line he said")
    assert len(ig["caption"]) > 280, "Instagram keeps the whole thing"


def test_fit_caption_sheds_hashtags_before_the_title():
    caption = "Title\n\n" + " ".join(["#averylonghashtag"] * 40)
    fitted = postplanify.fit_caption("x", caption)
    assert fitted.startswith("Title")
    assert len(fitted) <= 280
    assert "#averylonghashtag" in fitted, "keeps as many tags as fit"


# ── outcomes ────────────────────────────────────────────────────────────────

def test_one_account_refusing_does_not_cost_the_others(api, clip):
    api.refuse["acc-tt"] = _err(429, "RATE_LIMIT_EXCEEDED",
                                "Too many posts for this account")
    result = _by_platform(_publisher().post_clip(clip, "t"))
    assert not result["tiktok"]["success"]
    assert "RATE_LIMIT" in result["tiktok"]["error"]
    assert result["instagram"]["success"] and result["x"]["success"]


def test_a_publish_error_is_not_counted_as_reached(api, clip):
    api.final["acc-x"] = {"id": "post-acc-x", "status": "SCHEDULED",
                          "logs": [{"type": "ERROR",
                                    "message": "The user is suspended"}]}
    result = _publisher().post_clip(clip, "t")
    assert "x" not in postplanify.platforms_reached(result)
    assert _by_platform(result)["x"]["error"] == "The user is suspended"


def test_still_scheduled_after_the_wait_counts_as_accepted(api, clip):
    api.final["acc-ig"] = {"id": "post-acc-ig", "status": "SCHEDULED",
                           "logs": []}
    entry = _by_platform(_publisher().post_clip(clip, "t"))["instagram"]
    assert entry["success"] and entry["published"] is False


def test_a_bad_key_is_not_configured_not_a_failure(api, clip):
    api.accounts_response = _err(401, "UNAUTHORIZED", "Invalid API key")
    with pytest.raises(NotConfigured):
        _publisher().post_clip(clip, "t")


def test_a_lapsed_subscription_is_not_configured(api, clip):
    api.refuse["acc-ig"] = _err(403, "FORBIDDEN",
                                "Workspace requires an active subscription")
    with pytest.raises(NotConfigured):
        _publisher().post_clip(clip, "t")


def test_nothing_connected_is_not_configured(api, clip):
    api.accounts = [a for a in ACCOUNTS if a["platform"] == "LINKEDIN"]
    with pytest.raises(NotConfigured):
        _publisher().post_clip(clip, "t")
    assert api.uploads == 0, "nothing uploaded when there is nowhere to go"


def test_a_file_over_the_upload_limit_is_rejected_for_good(
        api, clip, monkeypatch):
    monkeypatch.setattr(postplanify, "MAX_UPLOAD_BYTES", 5)
    with pytest.raises(PermanentlyRejected):
        _publisher().post_clip(clip, "t")


def test_no_key_is_not_ready(monkeypatch):
    monkeypatch.delenv("POSTPLANIFY_API_KEY", raising=False)
    assert not postplanify.PostPlanifyPublisher({}).ready()


def test_a_dry_run_touches_nothing(api, clip):
    result = _publisher().post_clip(clip, "t", dry_run=True)
    assert api.uploads == 0 and not api.scheduled
    assert postplanify.platforms_reached(result) == set()


# ── how the clip queue uses it ──────────────────────────────────────────────

def test_it_runs_first_and_its_audio_is_censored():
    from utils.clip_queue import (CENSOR_AUDIO_DEFAULTS, CLEAN_TEXT_PLATFORMS,
                                  CLIP_PLATFORMS)

    assert CLIP_PLATFORMS[0] == "postplanify"
    assert CENSOR_AUDIO_DEFAULTS["postplanify"] == "slurs", \
        "missing here would default to the ORIGINAL audio on every account"
    assert "postplanify" in CLEAN_TEXT_PLATFORMS


def test_it_is_a_known_publisher():
    from utils.social_promoter import _publisher_for

    assert isinstance(_publisher_for("postplanify", {}),
                      postplanify.PostPlanifyPublisher)


@pytest.fixture
def queue_run(tmp_path, monkeypatch):
    """offer() with every publisher faked: records which route was asked
    for which targets, and reports back what it 'reached'."""
    import utils.clip_queue as clip_queue

    calls = []
    reaches = {"postplanify": {"instagram", "tiktok"}}

    def fake_publish(platform, video_path, caption, config, dry_run=False,
                     detail=None):
        targets = (config or {}).get("target_platforms")
        calls.append((platform, targets))
        if detail is not None and platform in reaches:
            detail["covers"] = reaches[platform]
        if detail is not None and platform == "upload_post" and targets:
            detail["covers"] = set(targets)
        return True

    ready = type("P", (), {"ready": lambda self: True,
                           "supports_reels": True})
    monkeypatch.setattr(clip_queue, "publish", fake_publish)
    monkeypatch.setattr(clip_queue, "_publisher", lambda p, c: ready())
    monkeypatch.setattr(clip_queue, "_journal", lambda *a, **k: None)
    monkeypatch.setattr(clip_queue, "caption_for", lambda *a, **k: "cap")

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    open_cap = {"enabled": True, "daily_cap": 50, "min_minutes_between": 0}
    posting = {"enabled": True, "queue_path": str(tmp_path / "q.json"),
               "state_path": str(tmp_path / "s.json"),
               "kill_switch_file": str(tmp_path / "STOP"),
               "platforms": {name: dict(open_cap) for name in (
                   "postplanify", "upload_post", "instagram", "facebook",
                   "x")}}
    return clip_queue, posting, str(clip), calls, reaches


def test_what_postplanify_reached_is_not_posted_again(queue_run):
    clip_queue, posting, clip, calls, _ = queue_run
    outcome = clip_queue.offer(posting, {}, clip, "cap")

    assert outcome["postplanify"] == "posted"
    assert outcome["instagram"] == "skipped: sent by postplanify"
    assert outcome["tiktok"] == "skipped: sent by postplanify"
    assert outcome["facebook"] == "skipped: sent by upload_post"


def test_upload_post_is_only_asked_for_what_is_left(queue_run):
    clip_queue, posting, clip, calls, _ = queue_run
    clip_queue.offer(posting, {}, clip, "cap")

    upload_post = [targets for platform, targets in calls
                   if platform == "upload_post"]
    assert upload_post == [["facebook", "x"]], \
        "Instagram is connected on both services - asking upload_post " \
        "for it again is the same Reel twice"


def test_upload_post_quota_is_not_spent_when_nothing_is_left(queue_run):
    clip_queue, posting, clip, calls, reaches = queue_run
    reaches["postplanify"] = {"instagram", "tiktok", "facebook", "x"}
    outcome = clip_queue.offer(posting, {}, clip, "cap")

    assert "upload_post" not in [platform for platform, _ in calls]
    assert outcome["upload_post"] == "skipped: sent by postplanify"


def test_upload_post_is_not_queued_with_a_list_it_would_forget(queue_run):
    """A narrowed call cannot be queued: the job stores no target list,
    so it would drain later to upload_post's FULL list and repost the
    accounts postplanify already covered."""
    clip_queue, posting, clip, calls, _ = queue_run
    posting["platforms"]["upload_post"]["min_minutes_between"] = 120
    clip_queue.offer(posting, {}, clip, "cap")        # first clip: posts
    calls.clear()

    second = os.path.join(os.path.dirname(clip), "clip2.mp4")
    open(second, "wb").write(b"y")
    outcome = clip_queue.offer(posting, {}, second, "cap")

    assert outcome["upload_post"].startswith("skipped")
    from job_queue import JobQueue
    waiting = [job for job in JobQueue(path=posting["queue_path"]).list_jobs()
               if job.platform == "upload_post" and job.state != "done"]
    assert not waiting


def test_when_postplanify_reaches_nothing_the_old_routes_still_run(queue_run):
    clip_queue, posting, clip, calls, reaches = queue_run
    reaches["postplanify"] = set()
    outcome = clip_queue.offer(posting, {}, clip, "cap")

    upload_post = [targets for platform, targets in calls
                   if platform == "upload_post"]
    assert upload_post == [None], "its own full list, unchanged"
    assert outcome["upload_post"] == "posted"
