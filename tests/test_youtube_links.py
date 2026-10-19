"""Links on every YouTube description, because there they are clickable.

VODs, recaps, Shorts, weeklies, compilations and Postproxy-routed
Shorts all upload to YouTube; every one of their descriptions carries
the channels. A surface that goes up linkless is a viewer with nowhere
to tap.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

from utils.stream_links import youtube_footer  # noqa: E402


def test_the_template_block_wins_when_it_is_there():
    template = ("Intro line\n\nALL LINKS\n"
                "Rumble: https://rumble.com/user/BinScripts\n\nTags")
    assert youtube_footer(template, "https://rumble.com/user/Other") == \
        "ALL LINKS\nRumble: https://rumble.com/user/BinScripts"


def test_the_fallback_names_every_channel():
    block = youtube_footer("", "")
    for needle in ("https://www.youtube.com/@wopovod",
                   "https://rumble.com/user/BinScripts",
                   "https://www.youtube.com/@stackswopo_"):
        assert needle in block
    # YouTube removed @wopoclipper on 10-18 (impersonation): no dead link.
    assert "@wopoclipper" not in block


def test_the_fallback_follows_the_configured_rumble_channel():
    block = youtube_footer("", "https://rumble.com/user/SomeoneElse")
    assert "https://rumble.com/user/SomeoneElse" in block
    assert "BinScripts" not in block


def test_a_compilation_description_carries_the_links():
    import compile_channel as cc

    videos = [cc.Video("abc", "One", 0)]
    text = cc.make_description(
        "Series", "2 Hours", videos, [10],
        {"credit_name": "X", "credit_url": "https://example.com",
         "mixed_series_label": "M"},
        footer=youtube_footer("", ""))
    assert "https://rumble.com/user/BinScripts" in text
    assert "https://www.youtube.com/@wopovod" in text
    assert "https://youtu.be/abc" in text


def test_a_compilation_without_a_footer_still_builds():
    import compile_channel as cc

    videos = [cc.Video("abc", "One", 0)]
    text = cc.make_description(
        "Series", "2 Hours", videos, [10],
        {"credit_name": "X", "credit_url": "https://example.com",
         "mixed_series_label": "M"})
    assert "https://youtu.be/abc" in text


def test_postproxy_shorts_carry_the_full_stream_link(tmp_path, monkeypatch):
    """Postproxy builds the YouTube body from the Shorts template; the
    per-clip full-stream line needs the clip path, which it was not
    being given."""
    from publishers import postproxy
    import utils.stream_links as stream_links

    monkeypatch.setattr(stream_links, "link_for_clip",
                        lambda *a, **k: "https://www.youtube.com/watch?v=full")
    cfg = {"youtube_shorts": {
        "description_template": "[CAPTION]\n\nFull streams: https://rumble.com/user/BinScripts",
        "privacy": "private", "tags": []}}
    clip = tmp_path / "Clip 01.mp4"
    clip.write_bytes(b"x" * 10)
    pub = postproxy.PostproxyPublisher(cfg, "postproxy_youtube")
    fields = dict(pub._form("A funny line", str(clip)))
    body = fields["post[body]"]
    assert "▶ Full stream: https://www.youtube.com/watch?v=full" in body
    assert "https://rumble.com/user/BinScripts" in body


def test_recap_links_fall_back_when_the_template_loses_its_header():
    """_links_block used to return '' then; the recap went up linkless
    and nothing said so."""
    import types
    from utils import recap

    cfg = types.SimpleNamespace(
        youtube=types.SimpleNamespace(description_template="no header here"),
        rumble=types.SimpleNamespace(channel_url="https://rumble.com/user/BinScripts"))
    block = recap._links_block(cfg)
    assert "ALL LINKS" in block
    assert "https://rumble.com/user/BinScripts" in block
