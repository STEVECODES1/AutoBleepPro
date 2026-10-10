"""A casino on screen keeps a clip off the strike platforms."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from autoreel import gambling_check as gc  # noqa: E402


def test_replies_are_read_strictly():
    assert gc._parse('{"gambling": true, "what": "slots"}') == (True, "slots")
    assert gc._parse('sure! {"gambling": "false", "what": "GTA"}') == (False, "GTA")
    assert gc._parse("no idea") is None
    assert gc._parse('{"gambling": "maybe"}') is None


def test_any_frame_with_a_casino_is_enough(monkeypatch):
    monkeypatch.setattr(gc, "frame_jpeg", lambda src, at: f"frame{at}")
    answers = {"frame1.0": '{"gambling": false}', "frame2.0": '{"gambling": true, "what": "Howl"}'}
    verdict, hits = gc.gambling_on_screen("x.mp4", [1.0, 2.0, 3.0],
                                          ask=lambda s, p, imgs: answers.get(imgs[0], "{}"))
    assert verdict is True and hits == [(2.0, "Howl")]


def test_no_model_means_not_sure(monkeypatch):
    monkeypatch.setattr(gc, "frame_jpeg", lambda src, at: "f")
    verdict, hits = gc.gambling_on_screen("x.mp4", [1.0], ask=lambda *a: "garbage")
    assert verdict is None and hits == []


def test_a_gambling_clip_is_held_everywhere_but_rumble(monkeypatch):
    from publishers.errors import HeldBack
    from utils import clip_queue as cq

    monkeypatch.setattr(cq, "_gambling_verdict", lambda path, config: "slot machine")
    with pytest.raises(HeldBack):
        cq._hold_if_gambling("youtube_shorts", "clip.mp4", {})
    cq._hold_if_gambling("rumble", "clip.mp4", {})          # no raise


def test_a_clean_clip_goes_out(monkeypatch):
    from utils import clip_queue as cq

    monkeypatch.setattr(cq, "_gambling_verdict", lambda path, config: "")
    cq._hold_if_gambling("instagram", "clip.mp4", {})


def test_monkey_drafts_cut_the_casino_spots(monkeypatch):
    import monkey_prep as mp

    monkeypatch.setattr(gc, "frame_jpeg", lambda src, at: f"f{int(at)}")
    flags = mp.gambling_flags("x.mp4", 90, every=30,
                              ask=lambda s, p, imgs: '{"gambling": true, "what": "slots"}'
                              if imgs[0] == "f45" else '{"gambling": false}')
    assert [(f["start"], f["end"]) for f in flags] == [(30.0, 60.0)]
    assert mp.is_cut(flags[0])
