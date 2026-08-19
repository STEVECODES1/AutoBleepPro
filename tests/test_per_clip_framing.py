"""A stream is not one thing for two hours.

This channel opens on a Monkey call and then plays GTA for a hundred
minutes. One framing decision for the whole file is wrong for whichever
half loses the vote: a call cropped as gameplay, or gameplay cropped onto
a face - which is how twenty clips came out framed on a browser window
while the two people talking were off-crop.

Looking at each clip's own frames cannot be outvoted by a different part
of the stream, because there is no other part of the clip.
"""

from __future__ import annotations

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from autoreel import clip_maker  # noqa: E402
from autoreel.clip_maker import ClipMaker, ClipSpec  # noqa: E402
from autoreel.crop_strategy import (CROP_FACE_PAN, CROP_FIT,  # noqa: E402
                                    CROP_MOTION)


def _maker(**kw):
    return ClipMaker(output_dir="/out", per_clip_framing=True, **kw)


def _at(monkeypatch, kind):
    monkeypatch.setattr("autoreel.content_kind.kind_for_video",
                        lambda *a, **k: kind)


def test_a_call_inside_a_gameplay_stream_is_framed_as_a_call(monkeypatch):
    _at(monkeypatch, "monkey")

    strategy, region = _maker()._framing_at(
        "/v.mp4", ClipSpec(start=120.0, end=140.0, index=1), CROP_FIT, None)

    assert strategy == CROP_FACE_PAN
    assert region, "the call pane rectangle came with it"


def test_gameplay_inside_a_call_stream_keeps_the_whole_frame(monkeypatch):
    _at(monkeypatch, "gta")

    strategy, _ = _maker()._framing_at(
        "/v.mp4", ClipSpec(start=4000.0, end=4020.0, index=9),
        CROP_FACE_PAN, {"x": 0.0, "y": 0.0, "width": 0.5, "height": 1.0})

    assert strategy == CROP_FIT


def test_the_run_s_own_decision_stands_when_nothing_can_be_read(monkeypatch):
    """No numpy, no ffmpeg, a corrupt stretch. An improvement on a guess,
    never a requirement."""
    _at(monkeypatch, "")
    region = {"x": 0.1, "y": 0.0, "width": 0.5, "height": 1.0}

    assert _maker()._framing_at(
        "/v.mp4", ClipSpec(start=10.0, end=20.0, index=2),
        CROP_FACE_PAN, region) == (CROP_FACE_PAN, region)


def test_an_unreadable_clip_never_raises(monkeypatch):
    def explode(*_a, **_k):
        raise OSError("corrupt")

    monkeypatch.setattr("autoreel.content_kind.kind_for_video", explode)

    assert _maker()._framing_at(
        "/v.mp4", ClipSpec(start=0.0, end=10.0, index=1),
        CROP_MOTION, None) == (CROP_MOTION, None)


def test_an_unknown_kind_changes_nothing(monkeypatch):
    _at(monkeypatch, "something_new")

    assert _maker()._framing_at(
        "/v.mp4", ClipSpec(start=0.0, end=10.0, index=1),
        CROP_FIT, None)[0] == CROP_FIT


def test_it_says_so_only_when_it_disagrees(monkeypatch, capsys):
    _at(monkeypatch, "gta")

    _maker()._framing_at("/v.mp4", ClipSpec(start=0.0, end=10.0, index=3),
                         CROP_FIT, None)
    assert capsys.readouterr().out == "", "a clip that agrees is not news"

    _maker()._framing_at("/v.mp4", ClipSpec(start=0.0, end=10.0, index=4),
                         CROP_FACE_PAN, None)
    assert "this stretch is gta" in capsys.readouterr().out


def test_it_is_off_unless_the_profile_was_left_to_the_tool():
    """A profile named in config is a decision someone made."""
    assert ClipMaker(output_dir="/out").per_clip_framing is False


def test_a_named_profile_is_not_second_guessed():
    """The flag comes from what was ASKED for, not from what `auto`
    resolved to - by ClipMaker time the resolved name is all that is left
    in the config, and reading it there would turn every run into an
    auto run."""
    body = open(os.path.join(_REPO, "auto_uploader", "utils",
                             "clip_runner.py"), encoding="utf-8").read()
    assert "asked_for_auto" in body
    assert "per_clip_framing=asked_for_auto" in body
