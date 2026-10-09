"""Monkey App rough cuts for BinScripts: what gets left out, what stays."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

import monkey_prep as mp  # noqa: E402


def seg(s, e, text):
    return {"start": s, "end": e, "text": text}


def test_age_school_and_stop_requests_are_flagged():
    segs = [seg(100, 104, "how old are you though"), seg(300, 303, "I'm 15 bro"),
            seg(500, 502, "yo stop recording me"), seg(700, 703, "you funny as hell"),
            seg(900, 903, "what's your snap"), seg(1100, 1103, "you ugly fat boy")]
    reasons = [f["reason"] for f in mp.flags_for(segs)]
    assert reasons[0].startswith("age") and reasons[1].startswith("age")
    assert reasons[2].startswith("asked to stop")
    assert reasons[3].startswith("personal info")
    assert reasons[4].startswith("looks")
    assert len(reasons) == 5                      # "you funny as hell" is fine


def test_age_flag_reaches_well_past_the_line():
    f = mp.flags_for([seg(300, 303, "i'm only fourteen")])[0]
    assert f["start"] == 210 and f["end"] == 393


def test_flagged_moments_are_left_out_and_sexual_ones_only_listed():
    curve = [0.0] * 400
    curve[60], curve[200], curve[330] = 0.9, 0.8, 0.7
    segs = [seg(i, i + 5, "talking") for i in range(0, 400, 5)]
    flags = [{"start": 190, "end": 215, "reason": "asked to stop / upset", "text": ""},
             {"start": 320, "end": 335, "reason": mp.SEXUAL_WORDS, "text": ""}]
    chosen, left = mp.pick(curve, segs, flags, target_s=600)
    assert [round(m["start"]) for m in chosen] == [25, 295]
    assert chosen[1]["checks"] == [mp.SEXUAL_WORDS]
    assert left[0]["reasons"] == ["asked to stop / upset"]


def test_hate_speech_is_always_cut():
    flags = mp.flags_for([], hate_spans=[(50.0, 51.0)])
    assert mp.is_cut(flags[0]) and flags[0]["start"] == 45


def test_the_review_page_says_nothing_was_uploaded():
    page = mp.review_page("x.ts", [], [], [{"where": "0:01:00", "reason": "age - check how "
                                                      "old they are", "text": "how old"}])
    assert "Nothing has been uploaded" in page and "mention age" in page


def test_who_said_it_changes_only_insults_aimed_at_him():
    segs = [dict(seg(10, 12, "you ugly fat boy"), speaker="other person"),
            dict(seg(40, 42, "you ugly fat boy"), speaker="Stackswopo"),
            dict(seg(400, 402, "i'm 15"), speaker="other person")]
    flags = mp.flags_for(segs)
    assert flags[0]["reason"] == mp.LOOKS_FROM_THEM and not mp.is_cut(flags[0])
    assert flags[1]["reason"].startswith("looks") and mp.is_cut(flags[1])
    assert flags[2]["who"] == "other person" and mp.is_cut(flags[2])   # age: always cut


def test_what_was_said_shows_whose_line_it_is():
    segs = [dict(seg(0, 2, "yo what's good"), speaker="Stackswopo"),
            dict(seg(2, 4, "nothing much"), speaker="other person")]
    assert mp._said(segs, 0, 4) == "STACKS: yo what's good THEM: nothing much"
