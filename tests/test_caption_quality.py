"""Captions that read as a person wrote them.

What actually reached Instagram:

    vertical Typooooooooooo - Clip 01 🤣🤣🤣💀💀💀#stackswopo
    vertical Stackswopo Love Yall 20250914 204409 - Clip 03 🤣...
    vertical Stackswopovods - Clip 11 🤣🤣🤣💀💀💀#stackswopo

"vertical" is the temp copy's prefix, "- Clip 01" is the index, and
20250914 204409 is the recorder's timestamp. None of it is a title, and
the line actually spoken in the clip was sitting in a .txt beside the
video the whole time - nothing read it back.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from utils.social_promoter import (  # noqa: E402
    ALWAYS_TAGS, TAG_LIMITS, build_caption, clip_title, hashtags_for,
    spoken_line, tidy_stem)


# ── the spoken line wins ─────────────────────────────────────────────

def test_the_line_from_the_clip_is_used(tmp_path):
    clip = tmp_path / "Monkey Howl - Clip 02.mp4"
    clip.write_bytes(b"x")
    (tmp_path / "Monkey Howl - Clip 02.txt").write_text("He got me a couple gift cards")
    assert clip_title(str(clip)) == "He got me a couple gift cards"


def test_the_caption_sidecar_is_read_too(tmp_path):
    clip = tmp_path / "a - Clip 01.mp4"
    clip.write_bytes(b"x")
    (tmp_path / "a - Clip 01_caption.txt").write_text("what do you have")
    assert clip_title(str(clip)) == "what do you have"


def test_the_vertical_copy_finds_the_original_s_line(tmp_path):
    """_vertical_copy renames the file; the sidecar belongs to the clip."""
    (tmp_path / "Monkey Howl - Clip 02.txt").write_text("Show me Q50")
    copy = tmp_path / "_vertical_Monkey Howl - Clip 02.mp4"
    copy.write_bytes(b"x")
    assert clip_title(str(copy)) == "Show me Q50"


def test_an_empty_sidecar_is_ignored(tmp_path):
    clip = tmp_path / "Monkey Howl - Clip 02.mp4"
    clip.write_bytes(b"x")
    (tmp_path / "Monkey Howl - Clip 02.txt").write_text("   \n")
    assert clip_title(str(clip)) == "Monkey Howl"


def test_no_sidecar_falls_back_to_the_filename(tmp_path):
    clip = tmp_path / "Monkey Howl - Clip 02.mp4"
    clip.write_bytes(b"x")
    assert clip_title(str(clip)) == "Monkey Howl"


# ── the filename is scrubbed ─────────────────────────────────────────

@pytest.mark.parametrize("stem,expected", [
    ("_vertical_Typooooooooooo - Clip 01", "Typooooooooooo"),
    ("_vertical_Stackswopovods - Clip 11", "Stackswopovods"),
    ("Stackswopo Love Yall 20250914 204409 - Clip 03", "Stackswopo Love Yall"),
    ("Monkey N Gamble Howl [v70rbpc] - Clip 02", "Monkey N Gamble Howl"),
    ("a 2026-08-15 - Clip 7", "a"),
])
def test_machinery_is_taken_out_of_the_filename(stem, expected):
    assert tidy_stem(stem) == expected


def test_the_clip_index_never_survives():
    for stem in ("x - Clip 01", "x - clip 7", "x -  Clip  12  "):
        assert "clip" not in tidy_stem(stem).lower()


def test_a_filename_that_is_all_machinery_is_not_empty():
    """An empty caption is worse than a plain one."""
    assert clip_title("_vertical_ - Clip 01.mp4")


# ── tags ─────────────────────────────────────────────────────────────

def test_the_channel_tag_is_always_there():
    for tag in ALWAYS_TAGS:
        assert f"#{tag}" in hashtags_for("anything", "instagram")


def test_a_monkey_clip_is_not_tagged_gtarp():
    """The wrong tags are how a small account gets buried."""
    tags = hashtags_for("Monkey N Gamble Howl", "instagram")
    assert "#monkeyapp" in tags
    assert "#gtarp" not in tags


def test_a_gta_clip_is_not_tagged_monkeyapp():
    tags = hashtags_for("stackswopo gta D10 Lifestyle RP", "instagram")
    assert "#gtarp" in tags
    assert "#monkeyapp" not in tags


def test_a_clip_that_says_nothing_still_gets_tags():
    """An untagged post is invisible."""
    assert hashtags_for("He got me a couple gift cards", "instagram").count("#") >= 4


@pytest.mark.parametrize("platform,limit", sorted(TAG_LIMITS.items()))
def test_each_platform_gets_the_count_it_rewards(platform, limit):
    tags = hashtags_for("Monkey N Gamble Howl gta slots", platform)
    assert 0 < tags.count("#") <= limit


def test_x_gets_far_fewer_than_instagram():
    """More than about two gets a post demoted on X, and they eat the
    280 characters the caption needs."""
    assert (hashtags_for("Monkey Howl", "zernio_twitter").count("#")
            < hashtags_for("Monkey Howl", "instagram").count("#"))


def test_the_specific_tags_come_before_the_generic_ones():
    tags = hashtags_for("Monkey N Gamble Howl", "instagram").split()
    assert tags.index("#monkeyapp") < tags.index("#funnymoments")


def test_no_tag_is_repeated():
    tags = hashtags_for("monkey gta slots react", "instagram").split()
    assert len(tags) == len(set(tags))


def test_a_zero_limit_means_no_tags():
    assert hashtags_for("anything", "instagram", limit=0) == ""


# ── the whole caption ────────────────────────────────────────────────

def test_the_template_carries_the_tags(tmp_path):
    clip = tmp_path / "Monkey Howl - Clip 02.mp4"
    clip.write_bytes(b"x")
    built = build_caption("{title}\n\n{tags}", str(clip),
                          tags="#stackswopo #monkeyapp")
    assert "#monkeyapp" in built
    assert "Monkey Howl" in built


def test_a_template_without_tags_still_works(tmp_path):
    clip = tmp_path / "a - Clip 01.mp4"
    clip.write_bytes(b"x")
    assert build_caption("{title} only", str(clip), tags="#x") == "a only"


def test_a_typoed_placeholder_does_not_cost_the_post(tmp_path):
    clip = tmp_path / "a - Clip 01.mp4"
    clip.write_bytes(b"x")
    assert build_caption("{titel}", str(clip), tags="#x")


def test_the_shipped_template_uses_both_placeholders():
    import json

    raw = json.load(open(os.path.join(ROOT, "auto_uploader",
                                      "config.example.json"), encoding="utf-8"))
    template = raw["instagram"]["caption_template"]
    assert "{title}" in template and "{tags}" in template
