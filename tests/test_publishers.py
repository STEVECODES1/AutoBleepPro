"""
Publisher modules: credential gating and the crop default.

No network calls happen here - every test either stops before the first
request or asserts that it stopped. What is actually being checked is
that a publisher with nothing configured refuses to act, because the
alternative is a half-configured module posting to a real account.
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

_REDDIT_VARS = (
    "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET",
    "REDDIT_USERNAME", "REDDIT_PASSWORD", "REDDIT_SUBREDDIT",
)


@pytest.fixture
def clean_reddit_env(monkeypatch):
    """No Reddit variables of any account, so each test sets its own."""
    for name in list(os.environ):
        if name.startswith("REDDIT_"):
            monkeypatch.delenv(name, raising=False)
    return monkeypatch


# ═════════════════════════════════════════════════════════════════════════════
# Instagram / Facebook - disabled until credentials exist
# ═════════════════════════════════════════════════════════════════════════════

def test_instagram_blocked_without_credentials(monkeypatch):
    monkeypatch.delenv("IG_PAGE_TOKEN", raising=False)
    monkeypatch.delenv("IG_BUSINESS_ACCOUNT_ID", raising=False)
    from auto_uploader.publishers.instagram import InstagramPublisher
    assert InstagramPublisher({}).post_reel("https://example.com/clip.mp4") is False


def test_instagram_blocked_with_partial_credentials(monkeypatch):
    """A token with no account id is not "nearly configured" - it's off."""
    monkeypatch.setenv("IG_PAGE_TOKEN", "token123")
    monkeypatch.delenv("IG_BUSINESS_ACCOUNT_ID", raising=False)
    from auto_uploader.publishers.instagram import InstagramPublisher
    assert InstagramPublisher({}).post_reel("https://example.com/clip.mp4") is False


def test_facebook_blocked_without_credentials(monkeypatch):
    monkeypatch.delenv("FB_PAGE_TOKEN", raising=False)
    monkeypatch.delenv("FB_PAGE_ID", raising=False)
    from auto_uploader.publishers.facebook import FacebookPublisher
    assert FacebookPublisher({}).post_reel("https://example.com/clip.mp4") is False


def test_facebook_blocked_with_partial_credentials(monkeypatch):
    monkeypatch.setenv("FB_PAGE_TOKEN", "token123")
    monkeypatch.delenv("FB_PAGE_ID", raising=False)
    from auto_uploader.publishers.facebook import FacebookPublisher
    assert FacebookPublisher({}).post_reel("https://example.com/clip.mp4") is False


# ═════════════════════════════════════════════════════════════════════════════
# Facebook Groups stay manual, in code
# ═════════════════════════════════════════════════════════════════════════════

def _group_config(tmp_path, **group):
    settings = {"enabled": True, "daily_cap": 5, "manual_approval_only": True}
    settings.update(group)
    return {
        "posting": {
            "enabled": True,
            "kill_switch_file": str(tmp_path / "STOP_POSTING"),
            "platforms": {"facebook_group": settings},
            "circuit_breaker": {"consecutive_failures": 3},
        }
    }


def test_facebook_group_manual_only_regardless_of_config(tmp_path):
    from auto_uploader.publish_guard import PublishGuard
    guard = PublishGuard(_group_config(tmp_path), str(tmp_path / "state.json"))
    ok, reason = guard.can_post("facebook_group")
    assert not ok and "manual-approval" in reason


def test_facebook_group_cannot_be_unlocked_by_clearing_the_flag(tmp_path):
    """Group publishing was withdrawn from the Graph API - there is no
    compliant route, so the block is in code, not in config."""
    from auto_uploader.publish_guard import PublishGuard
    config = _group_config(tmp_path, manual_approval_only=False, daily_cap=100)
    guard = PublishGuard(config, str(tmp_path / "state.json"))
    ok, reason = guard.can_post("facebook_group")
    assert not ok and "manual-approval" in reason


# ═════════════════════════════════════════════════════════════════════════════
# Reddit - a separate, named account
# ═════════════════════════════════════════════════════════════════════════════

def test_reddit_uses_separate_account_env_vars(clean_reddit_env):
    """Must read the account-2 vars, never the primary REDDIT_* ones."""
    clean_reddit_env.setenv("REDDIT_CLIENT_ID", "PRIMARY_ACCOUNT")
    clean_reddit_env.setenv("REDDIT_CLIENT_SECRET", "PRIMARY_SECRET")
    clean_reddit_env.setenv("REDDIT_USERNAME", "primary_user")
    clean_reddit_env.setenv("REDDIT_PASSWORD", "primary_pass")
    clean_reddit_env.setenv("REDDIT_SUBREDDIT", "stackswopo")

    from auto_uploader.publishers.reddit import RedditPublisher
    pub = RedditPublisher({})
    assert pub._ready() is False, "primary credentials must not satisfy account 2"
    assert pub.post_link("Test", "https://example.com") is False


def test_reddit_ready_with_correct_2_creds(clean_reddit_env):
    clean_reddit_env.setenv("REDDIT_CLIENT_ID_2", "id2")
    clean_reddit_env.setenv("REDDIT_CLIENT_SECRET_2", "secret2")
    clean_reddit_env.setenv("REDDIT_USERNAME_2", "user2")
    clean_reddit_env.setenv("REDDIT_PASSWORD_2", "pass2")
    clean_reddit_env.setenv("REDDIT_SUBREDDIT", "stackswopo")

    from auto_uploader.publishers.reddit import RedditPublisher
    assert RedditPublisher({})._ready() is True


def test_reddit_readiness_does_not_depend_on_praw_being_installed(clean_reddit_env):
    """"pip install praw" and "fill in your .env" are different problems;
    reporting one as the other sent people to fix the wrong thing."""
    clean_reddit_env.setenv("REDDIT_CLIENT_ID_2", "id2")
    clean_reddit_env.setenv("REDDIT_CLIENT_SECRET_2", "secret2")
    clean_reddit_env.setenv("REDDIT_USERNAME_2", "user2")
    clean_reddit_env.setenv("REDDIT_PASSWORD_2", "pass2")
    clean_reddit_env.setenv("REDDIT_SUBREDDIT", "stackswopo")

    from auto_uploader.publishers.reddit import RedditPublisher
    pub = RedditPublisher({})
    assert pub._ready() is True
    assert pub._missing_credentials() == []


def test_reddit_needs_a_subreddit(clean_reddit_env):
    for field in ("CLIENT_ID", "CLIENT_SECRET", "USERNAME", "PASSWORD"):
        clean_reddit_env.setenv(f"REDDIT_{field}_2", "x")
    from auto_uploader.publishers.reddit import RedditPublisher
    assert RedditPublisher({})._ready() is False


def test_reddit_account_name_comes_from_config(clean_reddit_env):
    """A third account needs no code change - only config plus .env."""
    clean_reddit_env.setenv("REDDIT_CLIENT_ID_3", "id3")
    clean_reddit_env.setenv("REDDIT_CLIENT_SECRET_3", "secret3")
    clean_reddit_env.setenv("REDDIT_USERNAME_3", "user3")
    clean_reddit_env.setenv("REDDIT_PASSWORD_3", "pass3")
    clean_reddit_env.setenv("REDDIT_SUBREDDIT", "stackswopo")

    from auto_uploader.publishers.reddit import RedditPublisher
    cfg = {"features": {"social_promoter": {"reddit_account": "3"}}}
    assert RedditPublisher(cfg)._ready() is True
    assert RedditPublisher({})._ready() is False, "account 2 is still unconfigured"


def test_reddit_credentials_name_the_variable_they_wanted(clean_reddit_env):
    from auto_uploader.utils.social_promoter import reddit_credentials
    with pytest.raises(KeyError) as excinfo:
        reddit_credentials("2")
    assert "REDDIT_CLIENT_ID_2" in str(excinfo.value)


def test_reddit_credentials_read_a_named_account(clean_reddit_env):
    from auto_uploader.utils.social_promoter import reddit_credentials
    for field in ("CLIENT_ID", "CLIENT_SECRET", "USERNAME", "PASSWORD"):
        clean_reddit_env.setenv(f"REDDIT_{field}_ALT", f"alt-{field.lower()}")
    creds = reddit_credentials("ALT")
    assert creds["username"] == "alt-username"


def test_reddit_credentials_accept_the_prefix_layout_too(clean_reddit_env):
    """REDDIT_ALT_CLIENT_ID reads as naturally as REDDIT_CLIENT_ID_ALT."""
    from auto_uploader.utils.social_promoter import reddit_credentials
    for field in ("CLIENT_ID", "CLIENT_SECRET", "USERNAME", "PASSWORD"):
        clean_reddit_env.setenv(f"REDDIT_ALT_{field}", f"alt-{field.lower()}")
    assert reddit_credentials("ALT")["client_id"] == "alt-client_id"


def test_primary_reddit_credentials_are_not_used_for_a_named_account(clean_reddit_env):
    from auto_uploader.utils.social_promoter import reddit_credentials
    for field in ("CLIENT_ID", "CLIENT_SECRET", "USERNAME", "PASSWORD"):
        clean_reddit_env.setenv(f"REDDIT_{field}", "primary")
    with pytest.raises(KeyError):
        reddit_credentials("2")


# ═════════════════════════════════════════════════════════════════════════════
# Crop strategy - centre by default, for gameplay
# ═════════════════════════════════════════════════════════════════════════════

def test_gameplay_default_crop_is_center():
    """Face tracking on GTA locks onto NPC faces and the crop jitters
    around the scene, so centre is the default and face is opt-in."""
    from autoreel.crop_strategy import (
        CROP_CENTER, DEFAULT_CROP_STRATEGY, resolve_crop_strategy)
    assert DEFAULT_CROP_STRATEGY == CROP_CENTER
    assert resolve_crop_strategy({}) == CROP_CENTER
    assert resolve_crop_strategy(None) == CROP_CENTER
    assert resolve_crop_strategy({"clips": {}}, "gameplay") == CROP_CENTER


def test_face_tracking_is_off_by_default():
    from autoreel.crop_strategy import face_tracking_enabled
    assert face_tracking_enabled({}) is False
    assert face_tracking_enabled({"clips": {"crop_strategy": "auto"}}) is False


def test_face_tracking_is_available_when_asked_for():
    from autoreel.crop_strategy import CROP_FACE, face_tracking_enabled, resolve_crop_strategy
    config = {"clips": {"crop_strategy": "face"}}
    assert resolve_crop_strategy(config) == CROP_FACE
    assert face_tracking_enabled(config) is True


def test_facecam_content_may_default_to_face():
    from autoreel.crop_strategy import CROP_FACE, resolve_crop_strategy
    assert resolve_crop_strategy({"clips": {"crop_strategy": "auto"}},
                                 "facecam") == CROP_FACE


def test_unknown_content_kind_falls_back_to_center():
    """Centre is the option that cannot track the wrong thing."""
    from autoreel.crop_strategy import CROP_CENTER, resolve_crop_strategy
    assert resolve_crop_strategy({}, "some-new-format") == CROP_CENTER


def test_a_misspelled_strategy_is_an_error_not_a_silent_fallback(tmp_path):
    """"centre" quietly becoming something else is how a channel's clips
    end up cropped a way nobody chose."""
    from autoreel.crop_strategy import CropStrategyError, resolve_crop_strategy
    with pytest.raises(CropStrategyError):
        resolve_crop_strategy({"clips": {"crop_strategy": "centre"}})


def test_shipped_config_uses_center_for_gameplay():
    import json
    with open(os.path.join(_UPLOADER, "config.json")) as f:
        shipped = json.load(f)
    from autoreel.crop_strategy import CROP_CENTER, resolve_crop_strategy
    assert resolve_crop_strategy(shipped) == CROP_CENTER
    assert shipped["clips"]["content_kind"] == "gameplay"


# ═════════════════════════════════════════════════════════════════════════════
# Reddit through the guard
#
# It used to post on the older unguarded path, which had no cap and no
# spacing. Behind the guard it gets both, and that is the whole reason a
# daily figure like 8 is safe to set at all.
# ═════════════════════════════════════════════════════════════════════════════

def test_reddit_is_reachable_as_a_guarded_publisher():
    from utils.social_promoter import _publisher_for

    publisher = _publisher_for("reddit", {})
    assert publisher is not None
    assert publisher.supports_link_posts is True
    assert callable(getattr(publisher, "ready", None))


def test_a_multi_line_announcement_becomes_a_one_line_title(monkeypatch):
    """Reddit titles are one line and cap at 300 characters; submitting
    the whole announcement would be rejected."""
    from auto_uploader.publishers.reddit import RedditPublisher

    sent = {}
    publisher = RedditPublisher({}, account="2")
    monkeypatch.setattr(publisher, "_ready", lambda: True)
    monkeypatch.setattr(publisher, "_submit",
                        lambda what, **kw: sent.update(kw) or True)

    publisher.post_link("🎬 New upload: Big Stream\n▶️ YouTube: https://y/1",
                        "https://y/1")
    assert "\n" not in sent["title"]
    assert sent["title"] == "🎬 New upload: Big Stream"


def test_a_very_long_title_is_truncated(monkeypatch):
    from auto_uploader.publishers.reddit import RedditPublisher

    sent = {}
    publisher = RedditPublisher({}, account="2")
    monkeypatch.setattr(publisher, "_ready", lambda: True)
    monkeypatch.setattr(publisher, "_submit",
                        lambda what, **kw: sent.update(kw) or True)

    publisher.post_link("x" * 500, "https://y/1")
    assert len(sent["title"]) == 300


def test_an_empty_message_still_gets_a_title(monkeypatch):
    from auto_uploader.publishers.reddit import RedditPublisher

    sent = {}
    publisher = RedditPublisher({}, account="2")
    monkeypatch.setattr(publisher, "_ready", lambda: True)
    monkeypatch.setattr(publisher, "_submit",
                        lambda what, **kw: sent.update(kw) or True)

    publisher.post_link("", "https://y/1")
    assert sent["title"] == "New upload"


# ═════════════════════════════════════════════════════════════════════════════
# Instagram CAN take a clip, even though it cannot take a link
#
# The documented flow wants a video_url that Meta fetches server-side,
# which means every clip needs public hosting first - and a Rumble page
# is not a fetchable video file, so there was nothing to hand it. The
# resumable upload removes that requirement entirely.
# ═════════════════════════════════════════════════════════════════════════════

def test_instagram_advertises_reels_even_though_it_refuses_links():
    from auto_uploader.publishers.instagram import InstagramPublisher

    assert InstagramPublisher.supports_link_posts is False
    assert InstagramPublisher.supports_reels is True


def test_the_container_is_created_for_a_direct_upload(monkeypatch, tmp_path):
    from auto_uploader.publishers import instagram as ig

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video bytes")
    seen = {}

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "container-1", "status_code": "FINISHED"}

    def fake_post(url, **kw):
        seen.setdefault("posts", []).append((url, kw))
        return Response()

    monkeypatch.setattr(ig.requests, "post", fake_post)
    monkeypatch.setattr(ig.requests, "get", lambda *a, **k: Response())
    monkeypatch.setenv("IG_PAGE_TOKEN", "tok")
    monkeypatch.setenv("IG_BUSINESS_ACCOUNT_ID", "123")

    assert ig.InstagramPublisher({}).post_reel_from_file(str(clip), "hi") is True

    create = seen["posts"][0][1]["data"]
    assert create["upload_type"] == "resumable", \
        "without this Meta expects a hosted URL, which is the whole problem"
    assert create["media_type"] == "REELS"
    assert "video_url" not in create


def test_the_bytes_go_to_the_upload_host_with_the_size(monkeypatch, tmp_path):
    """offset and file_size are required headers; without them the upload
    is rejected."""
    from auto_uploader.publishers import instagram as ig

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x" * 4096)
    seen = []

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"id": "container-1", "status_code": "FINISHED"}

    monkeypatch.setattr(ig.requests, "post",
                        lambda url, **kw: seen.append((url, kw)) or Response())
    monkeypatch.setattr(ig.requests, "get", lambda *a, **k: Response())
    monkeypatch.setenv("IG_PAGE_TOKEN", "tok")
    monkeypatch.setenv("IG_BUSINESS_ACCOUNT_ID", "123")

    ig.InstagramPublisher({}).post_reel_from_file(str(clip), "hi")

    upload = [(u, kw) for u, kw in seen if "rupload" in u]
    assert upload, "the file was never uploaded"
    url, kw = upload[0]
    assert kw["headers"]["file_size"] == "4096"
    assert kw["headers"]["offset"] == "0"
    assert kw["headers"]["Authorization"].startswith("OAuth ")
    assert kw["data"] == b"x" * 4096


def test_a_missing_file_is_refused_before_any_network_call(monkeypatch):
    from auto_uploader.publishers import instagram as ig

    def explode(*a, **k):
        raise AssertionError("called the API for a file that does not exist")

    monkeypatch.setattr(ig.requests, "post", explode)
    monkeypatch.setenv("IG_PAGE_TOKEN", "tok")
    monkeypatch.setenv("IG_BUSINESS_ACCOUNT_ID", "123")
    assert ig.InstagramPublisher({}).post_reel_from_file("nope.mp4") is False


def test_a_full_stream_is_refused_before_uploading_for_an_hour(monkeypatch, tmp_path):
    from auto_uploader.publishers import instagram as ig

    big = tmp_path / "stream.mp4"
    big.write_bytes(b"x" * 16)
    monkeypatch.setattr(ig, "MAX_REEL_BYTES", 8)
    monkeypatch.setattr(ig.requests, "post",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("started uploading anyway")))
    monkeypatch.setenv("IG_PAGE_TOKEN", "tok")
    monkeypatch.setenv("IG_BUSINESS_ACCOUNT_ID", "123")
    assert ig.InstagramPublisher({}).post_reel_from_file(str(big)) is False


def test_a_reel_still_goes_through_the_guard(tmp_path):
    """A clip route must not become a way around the cap and spacing."""
    import sys
    sys.path.insert(0, os.path.join(_REPO, "auto_uploader"))
    from utils.social_promoter import post_clip_to_instagram

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video")
    posting = {
        "enabled": True,
        "kill_switch_file": __file__,          # exists -> everything halts
        "state_path": str(tmp_path / "state.json"),
        "platforms": {"instagram": {"enabled": True, "daily_cap": 5}},
    }
    assert post_clip_to_instagram(posting, str(clip), "caption") is False


# ═════════════════════════════════════════════════════════════════════════════
# Making a posted clip look like the ones already on the account
# ═════════════════════════════════════════════════════════════════════════════

def test_the_caption_follows_the_account_s_own_format():
    import json
    import sys
    sys.path.insert(0, os.path.join(_REPO, "auto_uploader"))
    from utils.social_promoter import build_caption

    with open(os.path.join(_REPO, "auto_uploader", "config.json")) as f:
        template = json.load(f)["instagram"]["caption_template"]

    caption = build_caption(template, "Stackswopo twitch clips ban that....mp4")
    assert caption.startswith("ban that")
    assert "#stackswopo" in caption
    assert "youtube.com/@StacksDailyDose" in caption
    assert "rumble.com/user/BinScripts" in caption


def test_the_clip_name_survives_the_recorder_s_prefix():
    import sys
    sys.path.insert(0, os.path.join(_REPO, "auto_uploader"))
    from utils.social_promoter import clip_title

    assert clip_title("Stackswopo twitch clips who put stacks on slots.mp4") \
        == "who put stacks on slots"
    assert clip_title("Stackswopo twitch ff.mp4") == "ff"
    # Nothing to strip - a hand-dropped clip keeps its own name.
    assert clip_title("my highlight.mp4") == "my highlight"


def test_a_broken_template_costs_the_style_not_the_post():
    import sys
    sys.path.insert(0, os.path.join(_REPO, "auto_uploader"))
    from utils.social_promoter import build_caption

    assert build_caption("{nonsense}", "clip.mp4") == "clip"
    assert build_caption("", "clip.mp4") == "clip"


def test_a_landscape_clip_is_cropped_to_full_bleed_vertical(monkeypatch):
    """Instagram letterboxes a 16:9 video into black bars with the picture
    a third of the height - that is a video someone forgot to crop, not a
    Reel."""
    from autoreel.clip_maker import VERTICAL_HEIGHT, VERTICAL_WIDTH, crop_filter

    chain = crop_filter("center")
    assert "crop=" in chain
    assert f"scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}" in chain
    assert VERTICAL_HEIGHT / VERTICAL_WIDTH == 16 / 9


def test_the_audio_is_not_re_encoded_when_re_framing(monkeypatch, tmp_path):
    """Only the framing changes."""
    from autoreel import clip_maker

    seen = {}
    out = tmp_path / "vertical.mp4"

    def fake_run(args, **kw):
        seen["args"] = args
        out.write_bytes(b"reframed")
        return type("R", (), {"returncode": 0, "stderr": b""})()

    monkeypatch.setattr(clip_maker, "have_ffmpeg", lambda: True)
    monkeypatch.setattr(clip_maker.subprocess, "run", fake_run)
    assert clip_maker.make_vertical("in.mp4", str(out)) == str(out)
    assert seen["args"][seen["args"].index("-c:a") + 1] == "copy"


def test_a_failed_re_frame_still_posts_the_clip(monkeypatch, tmp_path):
    """Letterboxed beats not posted."""
    import sys
    sys.path.insert(0, os.path.join(_REPO, "auto_uploader"))
    from utils.social_promoter import _vertical_copy

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video")
    monkeypatch.setattr("autoreel.clip_maker.make_vertical",
                        lambda *a, **k: None)
    path, temp = _vertical_copy(str(clip), {"vertical": True}, {})
    assert path == str(clip)
    assert temp == ""


def test_re_framing_can_be_turned_off(tmp_path):
    import sys
    sys.path.insert(0, os.path.join(_REPO, "auto_uploader"))
    from utils.social_promoter import _vertical_copy

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video")
    path, temp = _vertical_copy(str(clip), {"vertical": False}, {})
    assert path == str(clip) and temp == ""
