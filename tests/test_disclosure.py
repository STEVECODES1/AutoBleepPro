"""The fan + actors notice is on every surface (owner, 10-22)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from utils.disclosure import (  # noqa: E402
    ACTORS_LINE, ABOUT_NOTICE, FAN_LINE, SHORT_ACTORS_LINE,
    for_about, for_caption, for_description,
)


def test_description_keeps_the_hashtags_last():
    text = for_description("A line about the stream\n\n#Stackswopo #GTARP")
    assert ACTORS_LINE in text
    assert text.index(ACTORS_LINE) < text.index("#Stackswopo")


def test_description_is_idempotent():
    once = for_description("A line")
    assert for_description(once) == once


def test_caption_puts_the_disclosure_before_the_tags():
    text = for_caption("He calls her bluff\n\n#stackswopo #shorts")
    assert SHORT_ACTORS_LINE in text
    assert text.index(SHORT_ACTORS_LINE) < text.index("#stackswopo")


def test_caption_is_idempotent():
    once = for_caption("A line")
    assert for_caption(once) == once


def test_about_replaces_the_old_fan_only_notice():
    old = FAN_LINE + "\n\nSTACKSWOPO CLIPS is home to clips."
    new = for_about(old)
    assert new.startswith(ABOUT_NOTICE)
    assert new.count(FAN_LINE) == 1
    assert new.endswith("STACKSWOPO CLIPS is home to clips.")
    assert for_about(new) == new


def test_the_shipped_template_carries_the_notice():
    import json

    path = os.path.join(ROOT, "auto_uploader", "config.example.json")
    with open(path, encoding="utf-8") as handle:
        config = json.load(handle)
    assert ACTORS_LINE in config["youtube"]["description_template"]
    assert ACTORS_LINE in config["rumble"]["description_template"]
    assert SHORT_ACTORS_LINE in config["youtube_shorts"]["description_template"]


def test_a_compilation_description_carries_the_notice():
    sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))
    import compile_channel as cc

    text = cc.make_description(
        "Series", "2 Hours", [cc.Video("abc", "One", 0)], [10],
        {"credit_name": "X", "credit_url": "https://example.com",
         "mixed_series_label": "M"})
    assert ACTORS_LINE in text
