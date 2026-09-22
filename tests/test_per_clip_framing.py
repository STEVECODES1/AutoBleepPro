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
from autoreel.crop_strategy import (CROP_CENTER, CROP_FACE_PAN,  # noqa: E402
                                    CROP_FIT,
                                    CROP_FIT, CROP_MOTION, CROP_REGION)


def _maker(**kw):
    return ClipMaker(output_dir="/out", per_clip_framing=True, **kw)


def _at(monkeypatch, kind):
    monkeypatch.setattr("autoreel.content_kind.kind_for_video",
                        lambda *a, **k: kind)


def test_a_call_inside_a_gameplay_stream_is_framed_as_a_call(monkeypatch):
    _at(monkeypatch, "monkey")

    strategy, region = _maker()._framing_at(
        "/v.mp4", ClipSpec(start=120.0, end=140.0, index=1), CROP_FIT, None)

    # CROP_REGION since face_pan was retired 2026-09-21: the same
    # measured call-pane rectangle, held still instead of walking.
    assert strategy == CROP_REGION
    assert region, "the call pane rectangle came with it"


def test_gameplay_inside_a_call_stream_gets_the_gameplay_framing(monkeypatch):
    """A stream that starts on the call and moves to GTA: the gameplay
    stretch must not keep the call pane's rectangle."""
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


# ═════════════════════════════════════════════════════════════════════════════
# One default, not five copies of a literal
#
# The framing default has now moved twice, and both times the change landed
# on crop_strategy.py while a second path kept a hardcoded "center" and went
# on quietly producing the old framing. The tests below pin every place that
# can decide a framing without being told one, so the next move is a one-line
# change and not a hunt.
# ═════════════════════════════════════════════════════════════════════════════

def test_nothing_in_the_renderer_hardcodes_its_own_default():
    """Every default argument tracks DEFAULT_CROP_STRATEGY."""
    import inspect

    from autoreel import clip_maker
    from autoreel.crop_strategy import DEFAULT_CROP_STRATEGY

    for name in ("crop_filter", "build_filter", "render_clip",
                 "make_vertical"):
        default = inspect.signature(
            getattr(clip_maker, name)).parameters["strategy"].default
        assert default == DEFAULT_CROP_STRATEGY, (
            f"{name}() would frame a clip as {default!r} when the project "
            f"default is {DEFAULT_CROP_STRATEGY!r}")


def test_the_upload_paths_ask_rather_than_assume():
    """main.vertical_path and social_promoter._vertical_copy each re-frame
    a landscape file on its way to a platform. Neither may pick a framing
    of its own - a clip and the same clip posted through the other path
    have to look the same."""
    for rel in (("auto_uploader", "main.py"),
                ("auto_uploader", "utils", "social_promoter.py")):
        body = open(os.path.join(_REPO, *rel), encoding="utf-8").read()
        assert "make_vertical" in body
        # The strategy handed to make_vertical is resolved, never typed.
        assert "make_vertical(source, target, strategy)" in body \
            or "make_vertical(video_path, target, strategy)" in body
        assert "resolve_crop_strategy" in body


def test_the_whole_frame_is_what_gameplay_gets_everywhere():
    """The three lookups that can answer "how do I frame this?" for a
    gameplay stream with no explicit setting, and they must agree."""
    from autoreel.crop_strategy import (CROP_FIT, DEFAULT_CROP_STRATEGY,
                                        default_for_content,
                                        resolve_crop_strategy)

    assert DEFAULT_CROP_STRATEGY == CROP_FIT
    assert default_for_content("gameplay") == CROP_FIT
    assert resolve_crop_strategy({}, "gameplay") == CROP_FIT
    assert resolve_crop_strategy({"clips": {"profile": "gta"}}) == CROP_FIT


def test_an_explicit_centre_crop_still_wins():
    """Fit is the default, not a lock. Someone who types "center" into
    config.json gets a centre crop."""
    from autoreel.crop_strategy import CROP_CENTER, resolve_crop_strategy

    assert resolve_crop_strategy(
        {"clips": {"crop_strategy": "center"}}, "gameplay") == CROP_CENTER
