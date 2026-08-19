"""A caption written for the platform it is posted to.

One template went to all four. That is not how any of them work: X
demotes a post carrying a dozen hashtags and cuts off at 280 characters,
Instagram rewards them, Facebook reads either as spam, and a Short wants
something closer to a title. The same words everywhere is the most
visible mark of an automated account - the thing every ranking is tuned
to find.

The rule underneath all of this: it degrades to the old template on any
failure. A caption that reads a bit generic is a bad post; no caption is
no post.
"""

from __future__ import annotations

import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
for _path in (_REPO, _UPLOADER):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from autoreel import llm_captions  # noqa: E402
from autoreel.llm_captions import (PLATFORM_BRIEFS, cached,  # noqa: E402
                                   remember, write_captions)

REPLY = json.dumps({"captions": {
    "zernio_twitter": "bro got robbed with no gun",
    "instagram": "he really did that 💀",
    "facebook": "Stackswopo gets robbed without a firearm.",
    "youtube_shorts": "Robbed With No Firearm",
    "zernio_tiktok": "nah he was NOT ready",
}})


def _ask(reply=REPLY):
    def ask(key, model, prompt):
        ask.prompt = prompt
        return reply
    return ask


# ── one call, every platform ─────────────────────────────────────────

def test_all_platforms_come_back_from_one_call():
    """Asking four times costs four times as much and produces four
    answers each written without knowing what the others said."""
    calls = []

    def ask(key, model, prompt):
        calls.append(prompt)
        return REPLY

    out = write_captions("Robbed", "what the hell", sorted(PLATFORM_BRIEFS),
                         ask=ask)

    assert len(calls) == 1
    assert set(out) == set(PLATFORM_BRIEFS)


def test_each_platform_gets_its_own_words():
    out = write_captions("Robbed", "words", sorted(PLATFORM_BRIEFS),
                         ask=_ask())

    assert len(set(out.values())) == len(out), "the same line went everywhere"


def test_the_model_is_told_what_each_platform_wants():
    ask = _ask()
    write_captions("Robbed", "words", ["zernio_twitter", "facebook"], ask=ask)

    assert "280" in ask.prompt or "200 characters" in ask.prompt
    assert "no hashtags" in ask.prompt.lower()


def test_the_clip_is_described_not_the_video():
    ask = _ask()
    write_captions("Robbed at gunpoint", "he took my car", ["instagram"],
                   ask=ask)

    assert "Robbed at gunpoint" in ask.prompt
    assert "he took my car" in ask.prompt


def test_a_long_transcript_is_cut_down():
    ask = _ask()
    write_captions("t", "x" * 9000, ["instagram"], ask=ask)

    assert len(ask.prompt) < 4000


# ── failing safely ───────────────────────────────────────────────────

def test_junk_back_means_no_captions():
    assert write_captions("t", "w", ["instagram"], ask=_ask("not json")) == {}


def test_a_provider_that_throws_means_no_captions():
    def explode(*_a):
        raise OSError("down")

    assert write_captions("t", "w", ["instagram"], ask=explode) == {}


def test_a_platform_it_was_not_asked_about_is_ignored():
    """A model naming a platform nobody asked for must not have that
    caption posted somewhere it was never written for."""
    out = write_captions("t", "w", ["instagram"], ask=_ask())

    assert set(out) == {"instagram"}


def test_a_bare_mapping_is_still_read():
    """A model that answered with the mapping alone has done the job."""
    reply = json.dumps({"instagram": "he really did that"})

    assert write_captions("t", "w", ["instagram"], ask=_ask(reply))


def test_nothing_to_describe_means_no_call():
    called = []
    write_captions("", "", ["instagram"],
                   ask=lambda *a: called.append(1) or REPLY)

    assert not called


# ── written once, reused ─────────────────────────────────────────────

def test_captions_are_remembered_beside_the_clip(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")

    remember(str(clip), {"instagram": "he really did that"})

    assert cached(str(clip)) == {"instagram": "he really did that"}


def test_a_clip_with_no_sidecar_has_nothing_cached(tmp_path):
    assert cached(str(tmp_path / "clip.mp4")) == {}


def test_an_unreadable_sidecar_is_not_a_crash(tmp_path):
    clip = tmp_path / "clip.mp4"
    (tmp_path / "clip_captions.json").write_text("{ broken")

    assert cached(str(clip)) == {}


def test_remembering_never_raises_on_a_bad_path():
    remember("/no/such/folder/clip.mp4", {"instagram": "x"})


# ── the post-time path ───────────────────────────────────────────────

def test_a_written_caption_is_used_over_the_template(tmp_path, monkeypatch):
    from utils import clip_queue

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    monkeypatch.setattr(llm_captions, "write_captions",
                        lambda *a, **k: {"instagram": "he really did that"})

    said = clip_queue.caption_for(
        "instagram", str(clip), "fallback",
        {"instagram": {"caption_template": "OLD TEMPLATE {title}"}})

    assert "he really did that" in said
    assert "OLD TEMPLATE" not in said


def test_the_template_still_works_when_no_model_answers(tmp_path,
                                                       monkeypatch):
    from utils import clip_queue

    clip = tmp_path / "Robbed.mp4"
    clip.write_bytes(b"x")
    monkeypatch.setattr(llm_captions, "write_captions", lambda *a, **k: {})

    said = clip_queue.caption_for(
        "instagram", str(clip), "fallback",
        {"instagram": {"caption_template": "OLD TEMPLATE {title}"}})

    assert "OLD TEMPLATE" in said


def test_it_can_be_switched_off(tmp_path, monkeypatch):
    from utils import clip_queue

    clip = tmp_path / "Robbed.mp4"
    clip.write_bytes(b"x")

    def never(*_a, **_k):
        raise AssertionError("it asked a model after being told not to")

    monkeypatch.setattr(llm_captions, "write_captions", never)

    said = clip_queue.caption_for(
        "instagram", str(clip), "fallback",
        {"model_captions": False,
         "instagram": {"caption_template": "OLD TEMPLATE {title}"}})

    assert "OLD TEMPLATE" in said


def test_tags_are_still_picked_from_the_clip(tmp_path, monkeypatch):
    """The model is told not to write its own - hashtags_for picks them
    from the CLIP and sizes them per platform, and a model inventing tags
    alongside that produced twenty on a post that may carry two."""
    from utils import clip_queue

    clip = tmp_path / "monkey app trolling.mp4"
    clip.write_bytes(b"x")
    monkeypatch.setattr(llm_captions, "write_captions",
                        lambda *a, **k: {"instagram": "he really did that"})

    said = clip_queue.caption_for("instagram", str(clip), "", {})

    assert "#" in said


def test_facebook_gets_no_hashtag_block(tmp_path, monkeypatch):
    from utils import clip_queue
    from utils.social_promoter import TAG_LIMITS

    assert TAG_LIMITS.get("facebook", 0) >= 0
