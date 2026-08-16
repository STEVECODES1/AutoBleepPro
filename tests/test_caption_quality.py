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


def test_a_template_without_tags_gets_them_appended(tmp_path):
    """str.format silently ignores a keyword the template never mentions,
    so tags computed for a template without {tags} went NOWHERE - every
    post carried only the one tag typed into the template by hand.

    The live config.json is exactly that template. Requiring someone to
    notice a missing placeholder is not a fix."""
    clip = tmp_path / "a - Clip 01.mp4"
    clip.write_bytes(b"x")

    caption = build_caption("{title} only", str(clip), tags="#x #y")

    assert caption.startswith("a only")
    assert "#x" in caption and "#y" in caption


def test_tags_the_template_already_carries_are_not_repeated(tmp_path):
    """The shipped templates hard-code #stackswopo and hashtags_for
    always returns it too. Printing it twice reads as a bot."""
    clip = tmp_path / "a - Clip 01.mp4"
    clip.write_bytes(b"x")

    caption = build_caption("{title} #stackswopo", str(clip),
                            tags="#stackswopo #funny")

    assert caption.lower().count("#stackswopo") == 1
    assert "#funny" in caption


def test_a_template_with_the_placeholder_is_left_alone(tmp_path):
    """It already put the tags where the author wanted them."""
    clip = tmp_path / "a - Clip 01.mp4"
    clip.write_bytes(b"x")

    assert build_caption("{title} | {tags}", str(clip),
                         tags="#x") == "a | #x"


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


def test_tags_are_picked_from_the_filename_too(tmp_path):
    """The headline is usually the line SPOKEN in the clip, and nobody
    announces which app they are on. The VOD it was cut from is called
    "monkey_n_gamble_howl", which says exactly what it is - so matching
    only the headline meant the specific tags almost never fired."""
    import sys

    sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))
    from utils.clip_queue import caption_for

    clip = tmp_path / "monkey_n_gamble_howl - Clip 03.mp4"
    clip.write_bytes(b"x")

    caption = caption_for("instagram", str(clip), "",
                          {"instagram": {"caption_template": "{title}"}})

    assert "#monkeyapp" in caption, \
        "a Monkey clip went out with only the generic filler tags"


def test_a_gameplay_clip_is_not_tagged_as_monkey(tmp_path):
    """The whole reason tags are picked per clip. A wrong tag is worse
    than a missing one - every platform here demotes a post whose tags do
    not match what is in it."""
    import sys

    sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))
    from utils.clip_queue import caption_for

    clip = tmp_path / "stackswopo gta rp lifestyle - Clip 01.mp4"
    clip.write_bytes(b"x")

    caption = caption_for("instagram", str(clip), "",
                          {"instagram": {"caption_template": "{title}"}})

    assert "#gtarp" in caption
    assert "#monkeyapp" not in caption


# ── the live config.json goes stale, and nothing rewrites it ─────────

def test_a_line_deleted_from_the_template_stops_posting(tmp_path):
    """config.json is not tracked, so it is whatever it was the day it
    was written. "LINK IN BIO" was deleted from the shipped template and
    kept going out for DAYS, because the file anyone actually runs still
    had it. There is no link in the bio for these clips, and it is the
    most recognisable mark of an automated repost account."""
    import sys

    sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))
    from utils.clip_queue import caption_for

    clip = tmp_path / "monkey_n_gamble_howl - Clip 02.mp4"
    clip.write_bytes(b"x")
    stale = ("{title} #stackswopo\n\n"
             "\U0001f44b Monkey vids + full stream - LINK IN BIO\n\n"
             "YouTube: @BinScript")

    caption = caption_for("instagram", str(clip), "",
                          {"instagram": {"caption_template": stale}})

    assert "LINK IN BIO" not in caption
    assert "Monkey vids + full stream" not in caption
    assert "YouTube: @BinScript" in caption, "it took out too much"
    assert "\n\n\n" not in caption, "it left a hole where the line was"


def test_what_the_clip_is_survives_until_it_posts(tmp_path):
    """By post time all that is left is the filename, and "Stackswopo
    Love Yall - Clip 02" does not say it is Monkey footage - so every
    clip went out with only the generic tags. The stream title and the
    framing profile are known when the clip is CUT, and get written
    down beside it."""
    import sys

    sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))
    from utils.clip_queue import caption_for

    stem = "Stackswopo Love Yall 20250914 204409 - Clip 02"
    (tmp_path / f"{stem}.mp4").write_bytes(b"x")
    (tmp_path / f"{stem}_subject.txt").write_text(
        "Stackswopo Love Yall monkey_n_gamble_howl monkey")

    caption = caption_for("instagram", str(tmp_path / f"{stem}.mp4"), "",
                          {"instagram": {"caption_template": "{title}"}})

    assert "#monkeyapp" in caption, \
        "the clip's own subject note was not read, so it got filler tags"


def test_the_vertical_copy_finds_the_subject_note_too(tmp_path):
    """_vertical_copy renames the file; the note belongs to the clip."""
    import sys

    sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))
    from utils.clip_queue import caption_for

    stem = "Stackswopo Love Yall - Clip 02"
    (tmp_path / f"_vertical_{stem}.mp4").write_bytes(b"x")
    (tmp_path / f"{stem}_subject.txt").write_text("gta rp lifestyle gta")

    caption = caption_for("instagram", str(tmp_path / f"_vertical_{stem}.mp4"),
                          "", {"instagram": {"caption_template": "{title}"}})

    assert "#gtarp" in caption
    assert "#monkeyapp" not in caption


def test_no_subject_note_is_not_an_error(tmp_path):
    """Every clip cut before this existed has no note beside it."""
    import sys

    sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))
    from utils.clip_queue import caption_for

    clip = tmp_path / "a - Clip 01.mp4"
    clip.write_bytes(b"x")

    assert caption_for("instagram", str(clip), "",
                       {"instagram": {"caption_template": "{title}"}})


# ── the sidecar is a document, not a headline ────────────────────────

def test_caption_scaffolding_is_never_used_as_the_title(tmp_path):
    """_caption.txt is a whole caption - hook, "From: <stream>", tags -
    and the poster only ever wanted its first line. On a clip with no
    per-clip title that first line is "From: Stackswopo Love Yall",
    which is structure, not something anybody said."""
    clip = tmp_path / "Stackswopo Love Yall 20250914 204409 - Clip 02.mp4"
    clip.write_bytes(b"x")
    (tmp_path / "Stackswopo Love Yall 20250914 204409 - Clip 02_caption.txt"
     ).write_text("\nFrom: Stackswopo Love Yall\n\n#stackswopo #funny")

    assert clip_title(str(clip)) == "Stackswopo Love Yall", \
        "it used the caption's scaffolding as the headline"


def test_a_real_hook_in_the_caption_file_is_still_used(tmp_path):
    clip = tmp_path / "a - Clip 02.mp4"
    clip.write_bytes(b"x")
    (tmp_path / "a - Clip 02_caption.txt").write_text(
        "Taylor, you know who\n\nFrom: Stackswopo Love Yall\n\n#stackswopo")

    assert clip_title(str(clip)) == "Taylor, you know who"


def test_the_line_file_wins_over_the_caption_file(tmp_path):
    """One value per file cannot be misread the way a formatted document
    can."""
    clip = tmp_path / "a - Clip 02.mp4"
    clip.write_bytes(b"x")
    (tmp_path / "a - Clip 02_caption.txt").write_text("From: something")
    (tmp_path / "a - Clip 02_line.txt").write_text("N**** you got call")

    assert clip_title(str(clip)) == "N**** you got call"


def test_the_vertical_copy_finds_the_line_file(tmp_path):
    (tmp_path / "a - Clip 02_line.txt").write_text("Show me Q50")
    copy = tmp_path / "_vertical_a - Clip 02.mp4"
    copy.write_bytes(b"x")

    assert clip_title(str(copy)) == "Show me Q50"


def test_the_clip_sidecar_no_longer_says_link_in_bio(tmp_path):
    """The same dead slogan lived in a SECOND place - written into every
    clip's caption file, where cleaning the config template could not
    reach it."""
    import sys
    import types

    sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))
    from utils.clip_runner import caption_for as clip_caption

    spec = types.SimpleNamespace(title="Taylor, you know who")
    clip = types.SimpleNamespace(spec=spec)

    written = clip_caption(clip, "Stackswopo Love Yall", ["stackswopo"])

    assert "link in bio" not in written.lower()
    assert "Taylor, you know who" in written


# ── the filename IS the title, when a person typed it ────────────────

def test_a_hand_typed_filename_is_used_as_the_title():
    """"WIFI COOKED.mp4" has no quotes, no date and no video id, so every
    pattern-based check declined it - and the stream went up called
    "Gaming Stream" while its real title sat in the filename."""
    import sys

    sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))
    from utils.templating import title_from_plain_filename

    assert title_from_plain_filename("WIFI COOKED.mp4") == "WIFI COOKED"
    assert title_from_plain_filename("monkey_n_gamble_howl.mp4") == \
        "monkey n gamble howl"
    assert title_from_plain_filename("GG.mp4") == "GG", \
        "a one-word stream title is still a title"


@pytest.mark.parametrize("name", [
    "20250914 204409.mp4",      # a timestamp
    "12345678.mp4",             # digits
    "video.mp4",                # what a tool called it
    "output.mp4",
    "recording final.mp4",
    "VID_20240101.mp4",         # a phone
    "[v70rbpc].mp4",            # a yt-dlp id and nothing else
    "1080p60.mp4",              # an encoding setting
])
def test_a_machine_name_is_refused(name):
    """A wrong title gets published; a missing one is only a default.
    Only one of those can be taken back."""
    import sys

    sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))
    from utils.templating import title_from_plain_filename

    assert title_from_plain_filename(name) is None


def test_the_machinery_still_comes_off_a_real_name():
    import sys

    sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))
    from utils.templating import title_from_plain_filename

    assert title_from_plain_filename(
        "Stackswopo Love Yall 20250914 204409.mp4") == "Stackswopo Love Yall"


# ── the notes have to follow the clip ────────────────────────────────
#
# Rumble uploads the clip itself and was titled "Gumball ass animations" -
# the line actually said in it. Instagram and Facebook upload the 9:16
# RE-FRAME, which is written into censored/ while the clip's notes stay in
# the watch folder - so they found nothing, fell back to the filename, and
# posted the STREAM title ("Wifi Cooked") on every single clip.

def test_the_reframed_copy_carries_the_clips_notes(tmp_path):
    import sys

    sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))
    from utils.social_promoter import copy_sidecars

    watch = tmp_path / "watch"
    censored = tmp_path / "censored"
    watch.mkdir()
    censored.mkdir()
    base = "Wifi Cooked - Clip 01"
    clip = watch / f"{base}.mp4"
    clip.write_bytes(b"x")
    (watch / f"{base}.txt").write_text("Gumball ass animations")
    (watch / f"{base}_subject.txt").write_text("Wifi Cooked monkey")
    reframed = censored / f"_vertical_{base}.mp4"
    reframed.write_bytes(b"x")

    assert clip_title(str(reframed)) == "Wifi Cooked", \
        "the stream title is what it used to fall back to"

    assert copy_sidecars(str(clip), str(reframed)) == 2

    assert clip_title(str(reframed)) == "Gumball ass animations", \
        "the re-framed copy still cannot see the clip's own title"
    assert clip_title(str(clip)) == clip_title(str(reframed)), \
        "Rumble and Instagram are titling the same clip differently"


def test_copying_notes_that_are_not_there_is_not_an_error(tmp_path):
    import sys

    sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))
    from utils.social_promoter import copy_sidecars

    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"x")

    assert copy_sidecars(str(clip), str(tmp_path / "_vertical_a.mp4")) == 0
    assert copy_sidecars("", "") == 0
    assert copy_sidecars(str(clip), str(clip)) == 0, \
        "copying a file onto itself would truncate its own notes"


def test_instagram_gets_the_clips_line_with_the_language_filtered(tmp_path):
    """Same title as Rumble, but Instagram's rules apply to the TEXT.
    Rumble is the uncensored channel and keeps it as it was said."""
    import sys

    sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))
    from utils.clip_queue import caption_for

    base = "Wifi Cooked - Clip 01"
    clip = tmp_path / f"{base}.mp4"
    clip.write_bytes(b"x")
    (tmp_path / f"{base}.txt").write_text("Gumball ass animations")

    caption = caption_for("instagram", str(clip), "",
                          {"instagram": {"caption_template": "{title}"}})

    assert caption.startswith("Gumball a"), "it posted the stream title again"
    assert "ass" not in caption.lower().split()[1], "unfiltered on Instagram"
    assert clip_title(str(clip)) == "Gumball ass animations", \
        "Rumble's title was filtered too"
