"""Talk about kids in a sexual way is cut out of the YouTube copy of a full
stream before upload (10-17: 25 parts had to be cut by hand in Studio).
Too much of it, or a failed cut, sends the stream up private instead."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

from utils import kid_guard as kg  # noqa: E402


def seg(t0, text):
    return {"start": float(t0), "end": float(t0) + 4, "text": text}


def test_sexual_talk_about_a_minor_is_found():
    found = kg.ranges([seg(0, "gg"), seg(100, "he wants to have sex with a minor"),
                       seg(104, "that's crazy")])
    assert [(a, b) for a, b, _ in found] == [(92.0, 112.0)]


def test_gaming_talk_about_kids_is_left_alone():
    assert kg.ranges([seg(0, "these kids are cracked at fortnite"),
                      seg(4, "bro got sniped")]) == []


def test_close_parts_become_one_cut():
    assert len(kg.ranges([seg(100, "that guy is a pedo"), seg(130, "underage again")])) == 1


def test_keep_spans():
    assert kg.keep_spans(100, [(10, 20, []), (50, 60, [])]) == [(0.0, 10), (20, 50), (60, 100)]


def _run(monkeypatch, segs, duration, cut_ok=True):
    said, cuts = [], []
    monkeypatch.setattr(kg, "_segments", lambda cfg, *p: segs)
    monkeypatch.setattr(kg, "_duration", lambda p: duration)
    monkeypatch.setattr(kg, "cut", lambda src, c, out, d: cuts.append(c) or cut_ok)
    out = kg.make_safe(None, "v.mp4", "v_CENSORED.mp4", "outdir",
                       alert=lambda t, m, urgent=True: said.append(t))
    return out, said, cuts


def test_parts_are_cut_and_said(monkeypatch):
    (path, why), said, cuts = _run(monkeypatch, [seg(100, "sex with a minor")], 3600)
    assert path.endswith("v_CENSORED_KIDSAFE.mp4") and why == "" and len(cuts) == 1
    assert said and "Cut" in said[0]


def test_too_much_goes_private(monkeypatch):
    segs = [seg(t, "pedo talk") for t in range(0, 1800, 25)]
    (path, why), said, cuts = _run(monkeypatch, segs, 3600)
    assert path is None and why and not cuts and "PRIVATE" in said[0]


def test_failed_cut_goes_private(monkeypatch):
    (path, why), said, _ = _run(monkeypatch, [seg(100, "sex with a minor")], 3600, cut_ok=False)
    assert path is None and why and "PRIVATE" in said[0]


def test_clean_stream_is_untouched(monkeypatch):
    (path, why), said, cuts = _run(monkeypatch, [seg(0, "gg go next")], 3600)
    assert (path, why, said, cuts) == (None, "", [], [])


def test_main_wires_it_in():
    src = open(os.path.join(ROOT, "auto_uploader", "main.py"), encoding="utf-8").read()
    assert "_kid_guard.make_safe(" in src
    assert 'if kid_private[0] and not is_clip:\n                yt_privacy = "private"' in src
    assert "not is_clip and not kid_private[0]" in src
