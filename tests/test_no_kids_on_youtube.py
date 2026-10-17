"""A moment that talks about kids never gets cut into a recap or a weekly.

On 10-16 the weekly on STACKSWOPOVODS went out with "YOU DO F*** KIDS,
THOUGH." burned in as a caption. Masking the swear does not help - the
subject is what YouTube's child-safety review acts on.
"""
import datetime as dt
import json
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from utils import recap, weekly  # noqa: E402

LINES = [{"start": 10.0, "end": 14.0, "text": "bro what are you doing"},
         {"start": 14.0, "end": 17.5, "text": "you do fuck kids though"},
         {"start": 30.0, "end": 33.0, "text": "nah that's crazy"}]


def test_what_was_said_includes_a_line_that_started_just_before():
    assert "kids" in recap.said(LINES, 15.0, 25.0)
    assert "kids" not in recap.said(LINES, 20.0, 40.0)
    assert recap.said([], 0, 10) == "" and recap.said(None, 0, 10) == ""


def test_the_kids_line_is_caught_and_ordinary_lines_are_not():
    assert recap.about_children("YOU DO F*** KIDS, THOUGH.")
    assert recap.about_children("you do fuck kids though")
    assert not recap.about_children("you kidding me bro")
    assert not recap.about_children("nah that's crazy")


def test_the_weekly_skips_a_moment_about_kids(tmp_path):
    cfg = SimpleNamespace(general=SimpleNamespace(censored_folder=str(tmp_path),
                                                  logs_folder=str(tmp_path)))
    video = tmp_path / "x.mp4"
    video.write_bytes(b"")
    dest = weekly.keep(cfg, str(video), "TALK TO ME", "10-14-26", "r", "f",
                       [(0, 20, 9.0, "you do fuck kids though"), (20, 40, 1.0, "lol"),
                        (40, 60, 5.0)], now=dt.datetime(2026, 10, 14))
    with open(dest + ".json", encoding="utf-8") as f:
        rec = json.load(f)
    assert rec["moments"][0][3] == "you do fuck kids though"
    assert len(rec["moments"][2]) == 3                     # older 3-field moments still load
    picked = [(a, b) for _, a, b, _ in weekly.pick([rec])]
    assert (0.0, 20.0) not in picked
    assert (20.0, 40.0) in picked and (40.0, 60.0) in picked
