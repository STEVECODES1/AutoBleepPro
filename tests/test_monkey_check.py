"""A Monkey run has to survive being looked at, not just measured."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from autoreel import monkey_check as mc  # noqa: E402


def test_replies_are_read_strictly():
    assert mc._parse('{"monkey": true, "what": "call pane"}') == (True, "call pane")
    assert mc._parse('sure! {"monkey": "false", "what": "GTA HUD"}') == (False, "GTA HUD")
    assert mc._parse("no idea") is None
    assert mc._parse('{"monkey": "maybe"}') is None


def _ask_factory(answers):
    def ask(system, prompt, images):
        return answers.get(images[0], "{}")
    return ask


def test_majority_keeps_a_run(monkeypatch):
    monkeypatch.setattr(mc, "frame_jpeg", lambda src, at: f"frame{at}")
    ask = _ask_factory({"frame22.5": '{"monkey": true}',
                        "frame45.0": '{"monkey": true}',
                        "frame67.5": '{"monkey": false, "what": "menu"}'})
    kept, dropped = mc.confirm_runs("x.mp4", [(0.0, 90.0)], ask=ask,
                                    frames_per_run=3)
    assert kept == [(0.0, 90.0)] and dropped == []


def test_one_matching_frame_does_not_keep_a_run(monkeypatch):
    monkeypatch.setattr(mc, "frame_jpeg", lambda src, at: f"frame{at}")
    ask = _ask_factory({"frame22.5": '{"monkey": true, "what": "call"}',
                        "frame45.0": '{"monkey": false, "what": "gameplay"}',
                        "frame67.5": '{"monkey": false, "what": "gameplay"}'})
    kept, dropped = mc.confirm_runs("x.mp4", [(0.0, 90.0)], ask=ask,
                                    frames_per_run=3)
    assert kept == []
    assert len(dropped) == 1 and "gameplay" in dropped[0][2]


def test_an_unjudgeable_run_stays(monkeypatch):
    """No measurement, no cut: a missing model must not stop drafts."""
    monkeypatch.setattr(mc, "frame_jpeg", lambda src, at: "")
    kept, dropped = mc.confirm_runs("x.mp4", [(0.0, 90.0)],
                                    ask=lambda *a: "garbage")
    assert kept == [(0.0, 90.0)] and dropped == []


def test_a_silent_run_is_dropped_even_when_it_looks_right(monkeypatch):
    """Ears plus eyes: a Monkey-looking run nobody talks in is a
    paused screen, not a call."""
    monkeypatch.setattr(mc, "frame_jpeg", lambda src, at: f"frame{at}")
    ask = _ask_factory({"frame22.5": '{"monkey": true}',
                        "frame45.0": '{"monkey": true}',
                        "frame67.5": '{"monkey": true}'})
    levels = [-70.0] * 90
    kept, dropped = mc.confirm_runs("x.mp4", [(0.0, 90.0)], ask=ask,
                                    levels=levels, window_s=1.0)
    assert kept == []
    assert dropped[0][2] == "near-silent - nobody talking"


def test_a_talking_run_is_kept(monkeypatch):
    monkeypatch.setattr(mc, "frame_jpeg", lambda src, at: f"frame{at}")
    ask = _ask_factory({"frame22.5": '{"monkey": true}',
                        "frame45.0": '{"monkey": true}',
                        "frame67.5": '{"monkey": true}'})
    levels = [-70.0] * 90
    levels[30:60] = [-20.0] * 30
    kept, dropped = mc.confirm_runs("x.mp4", [(0.0, 90.0)], ask=ask,
                                    levels=levels, window_s=1.0)
    assert kept == [(0.0, 90.0)] and dropped == []


def test_each_run_is_judged_on_its_own_frames(monkeypatch):
    seen = []

    def ask(system, prompt, images):
        seen.append(images[0])
        return '{"monkey": true}'

    monkeypatch.setattr(mc, "frame_jpeg", lambda src, at: f"frame{at}")
    kept, _ = mc.confirm_runs("x.mp4", [(0.0, 60.0), (600.0, 660.0)],
                              ask=ask, frames_per_run=2)
    assert kept == [(0.0, 60.0), (600.0, 660.0)]
    # Two frames per run, at thirds - inside their own run, not leaking.
    assert seen == ["frame20.0", "frame40.0", "frame620.0", "frame640.0"]
