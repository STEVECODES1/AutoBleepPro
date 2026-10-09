"""Full streams are named "Stackswopo - THOTBREAKER - 10-08-26 (FULL STREAM)".

2026-10-09: the user pointed at that title as how a full stream should
read. {TITLE} is the stream name in capitals; date_style "%m-%d-%y"
gives the zero-padded dashed date.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from utils.templating import build_title, format_date  # noqa: E402
from utils import stream_links  # noqa: E402

FORMAT = "Stackswopo - {TITLE} - {date} (FULL STREAM)"


def test_the_full_stream_title():
    date = format_date(datetime(2026, 10, 8), "%m-%d-%y")
    assert date == "10-08-26"
    assert build_title("thotbreaker", date, FORMAT) == (
        "Stackswopo - THOTBREAKER - 10-08-26 (FULL STREAM)")


def test_lowercase_title_placeholder_is_unchanged():
    assert build_title("thotbreaker", "10-08-26", '"{title}" {date}') == '"thotbreaker" 10-08-26'


def test_a_long_name_is_shortened_not_the_format():
    title = build_title("word " * 40, "10-08-26", FORMAT)
    assert len(title) <= 100
    assert title.startswith("Stackswopo - WORD")
    assert title.endswith(" - 10-08-26 (FULL STREAM)")


# ── clips cut before the date style changed still find their stream ──

def test_old_and_new_date_styles_are_the_same_day(tmp_path):
    store = str(tmp_path / "links.json")
    stream_links.remember("thotbreaker", "10/8/26",
                          {"youtube": "https://www.youtube.com/watch?v=_Va-AIG_5QE"},
                          store=store)
    assert stream_links.link_for("thotbreaker", "10-08-26", store=store) == (
        "https://www.youtube.com/watch?v=_Va-AIG_5QE")
    assert stream_links.link_for("thotbreaker", "10-09-26", store=store) == ""


def test_a_rumble_only_reupload_keeps_the_youtube_link(tmp_path):
    store = str(tmp_path / "links.json")
    yt = "https://www.youtube.com/watch?v=_Va-AIG_5QE"
    rb = "https://rumble.com/v7gm1ab-stackswopo-thotbreaker-10-08-26-full-stream.html"
    stream_links.remember("thotbreaker", "10/8/26", {"youtube": yt}, store=store)
    stream_links.remember("thotbreaker", "10-08-26", {"rumble": rb}, store=store)
    assert stream_links.link_for("thotbreaker", "10/8/26", platform="rumble", store=store) == rb
    assert stream_links.link_for("thotbreaker", "10-08-26", platform="youtube", store=store) == yt
