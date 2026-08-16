"""
A Monkey crop that follows the person instead of averaging over them.

One rectangle held for a whole minute can only be right about the
AVERAGE position. When someone stands up, leans out of shot or swaps
seats halfway through, the average is the wall between where they were
and where they went - which is exactly what the bad clips looked like:
a face pressed against one edge, the rest blank wall.

The counter-argument is in face_region's own docstring and it has not
gone away: a crop that chases whoever is talking on a two-person call
swings back and forth and is nauseating. Everything here is about the
damping that keeps those two facts from colliding - the crop moves when
someone actually goes somewhere, and holds still when they do not.
"""

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from autoreel.crop_strategy import (CROP_FACE, CROP_FACE_PAN, CROP_MOTION,
                                    PROFILES, VALID_STRATEGIES,
                                    resolve_crop_strategy)
from autoreel.face_region import (MAX_PAN_PER_S, PATH_DEADZONE, is_worth_moving,
                                  pan_path)
from autoreel.motion_region import commands_file


def _stills(*centres, size=0.20):
    """Per-still face lists, one face per still, at the given centres.

    A box is the (x, y, w, h) tuple mediapipe's detector hands back, in
    frame fractions - the same shape _measure returns per still.
    """
    return [[(cx - size / 2, cy - size / 2, size, size)]
            for cx, cy in centres]


# ── the path itself ──────────────────────────────────────────────────

def test_the_crop_walks_after_someone_who_moves():
    """The whole reason this exists. Someone starts on the left of the
    pane and ends on the right; one static box centres on neither."""
    path = pan_path(_stills(*([(0.25, 0.5)] * 4 + [(0.75, 0.5)] * 20)),
                    fps=2.0)

    xs = [x for _, x, _ in path]
    assert xs[0] < 0.4, "it did not start where the person started"
    assert xs[-1] > 0.6, "it never followed them across"


def test_it_never_moves_faster_than_the_speed_cap():
    """A crop that snaps to a new position reads as a cut, and a clip
    full of cuts nobody made looks broken rather than edited."""
    fps = 2.0
    path = pan_path(_stills(*([(0.10, 0.10)] * 2 + [(0.90, 0.90)] * 20)),
                    fps=fps)

    for axis in (1, 2):
        values = [entry[axis] for entry in path]
        steps = [abs(b - a) for a, b in zip(values, values[1:])]
        # Positions are written to 4 decimals for the sendcmd file, so a
        # step can read one rounding unit over the cap without the cap
        # having been exceeded. 1e-4 of a frame width is a tenth of a
        # pixel at 1080p.
        assert max(steps) <= MAX_PAN_PER_S / fps + 1e-4 + 1e-6, \
            "it moved faster than the speed cap"


def test_two_people_talking_in_their_seats_does_not_swing_the_crop():
    """THE failure this feature could introduce. Both faces are visible
    in every still - they are concurrent, not sequential - so the box
    covering them does not move just because the talking does."""
    both = [[(0.10, 0.40, 0.20, 0.20),
             (0.60, 0.40, 0.20, 0.20)]] * 20

    assert not is_worth_moving(pan_path(both, fps=2.0)), \
        "the crop moved on a call where nobody went anywhere"


def test_a_person_sitting_still_is_not_worth_rendering_a_path_for():
    """It is the static crop written the expensive way - an extra filter,
    a temp file and a way to fail, for no visible difference."""
    assert not is_worth_moving(pan_path(_stills(*([(0.5, 0.5)] * 20)),
                                        fps=2.0))


def test_a_face_that_leaves_for_one_still_does_not_drag_the_crop():
    """Somebody passing behind, or a detector missing a frame. The crop
    holding position is right; lunging at a single reading is not."""
    steady = [(0.30, 0.5)] * 10
    blip = _stills(*steady)
    blip.insert(5, [])  # nothing found in this one

    path = pan_path(blip, fps=2.0)
    xs = [x for _, x, _ in path]
    assert max(xs) - min(xs) <= PATH_DEADZONE + 1e-6


# ── what gets written for ffmpeg ─────────────────────────────────────

def test_the_command_file_moves_both_axes():
    """The call pane is 0.90 of the frame tall, so unlike the 16:9->9:16
    gameplay case there is real vertical slack. A face that stands up
    leaves a crop that only tracks sideways."""
    script = commands_file([(0.0, 0.30, 0.40), (1.0, 0.45, 0.55)],
                           "iw*0.2000", "ih*0.3000")

    assert script.count("crop x") == 2
    assert script.count("crop y") == 2


def test_a_gameplay_path_still_writes_no_y():
    """Same emitter, two callers. A 16:9 source cut to 9:16 has no
    vertical slack, and a y command there would be a no-op at best."""
    script = commands_file([(0.0, 0.30), (1.0, 0.45)], "min(iw,ih*9/16)")

    assert "crop y" not in script
    assert script.count("crop x") == 2


def test_the_pan_crops_the_measured_box_not_a_full_height_slice():
    """The sharp edge. build_filter's motion branch ignores `region` by
    design - gameplay pans across the whole frame - and reusing it here
    would pan a full-height 9:16 strip across the entire desktop, slots
    browser included."""
    from autoreel.clip_maker import build_filter

    region = {"x": 0.03, "y": 0.05, "width": 0.46, "height": 0.90}
    chain = build_filter(CROP_FACE_PAN, None, region, watermark=False,
                         motion_commands="/tmp/pan.cmds")

    assert chain.count("crop=") == 1, "two crop filters crop the crop"
    assert "iw*0.4600" in chain and "ih*0.9000" in chain, \
        "the measured box's size was thrown away"
    assert "sendcmd" in chain


def test_gameplay_motion_is_untouched_by_any_of_this():
    """The gameplay crop was the thing that already worked. It must come
    out byte-identical."""
    from autoreel.clip_maker import (CROP_WIDTH_EXPR, build_filter,
                                     motion_crop_filter)

    chain = build_filter(CROP_MOTION, None, None, watermark=False,
                         motion_commands="/tmp/pan.cmds")

    assert chain == motion_crop_filter("/tmp/pan.cmds")
    assert CROP_WIDTH_EXPR in chain


# ── the rules that must survive ──────────────────────────────────────

def test_face_pan_is_not_face_tracking():
    """CROP_FACE means the per-frame moviepy renderer and is banned from
    every profile. This is a different thing - a fixed-size box walked by
    sendcmd - and conflating the two names would quietly re-enable the
    banned one."""
    assert CROP_FACE_PAN != CROP_FACE
    assert CROP_FACE_PAN in VALID_STRATEGIES


def test_gameplay_never_reaches_the_face_pan():
    """Permanent rule: GTA is full of NPC faces and a detector locks onto
    whichever is nearest the lens. Frame-to-frame change has no opinion
    about faces, which is exactly why gameplay uses it."""
    for name in ("gta", "whole"):
        strategy = PROFILES[name]["crop_strategy"]
        assert strategy != CROP_FACE_PAN
        # And structurally: no content_region means nothing to search.
        assert not PROFILES[name].get("content_region")

    assert resolve_crop_strategy(
        {"clips": {"profile": "auto",
                   "content_title": "stackswopo + gta D10 johnny cox"}}) \
        == CROP_MOTION


# ── the wiring in ClipMaker.make ─────────────────────────────────────

def _maker(tmp_path):
    from autoreel.clip_maker import ClipMaker

    return ClipMaker(output_dir=str(tmp_path / "clips"), count=1,
                     min_seconds=2.0, max_seconds=5.0, preset="ultrafast",
                     captions=False,
                     config={"clips": {"profile": "monkey"}})


_SEGMENTS = [
    {"start": 0.0, "end": 2.0, "text": "walking", "words": []},
    {"start": 2.0, "end": 6.0,
     "text": "OH MY GOD what the hell holy crap bro", "words": []},
]


def test_the_maker_renders_a_pan_and_deletes_its_command_file(
        tmp_path, monkeypatch):
    """The .cmds script is scratch. It was never cleaned - a hidden file
    beside every clip, left for the life of the folder."""
    import subprocess

    import autoreel.clip_maker as clip_maker
    from autoreel.clip_maker import ClipSpec, have_ffmpeg

    if not have_ffmpeg():
        import pytest
        pytest.skip("ffmpeg not installed")

    source = str(tmp_path / "source.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=8",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        source], check=True)

    # Stand in for mediapipe: somebody who walks across the pane.
    size = {"x": 0.05, "y": 0.10, "width": 0.30, "height": 0.50}
    path = [(0.0, 0.20, 0.30), (1.0, 0.28, 0.36), (2.0, 0.36, 0.42)]
    monkeypatch.setattr(clip_maker.face_region, "path_for",
                        lambda *a, **k: (size, path))

    # Prove the pan was actually taken. Without this the test passes just
    # as happily on a run that fell back to the static crop and therefore
    # had no .cmds file to leave behind.
    seen = {}
    real_render = clip_maker.render_clip

    def spy(*args, **kwargs):
        seen["commands"] = kwargs.get("motion_commands", "")
        seen["region"] = args[8] if len(args) > 8 else kwargs.get("region")
        seen["existed"] = os.path.exists(seen["commands"] or "")
        return real_render(*args, **kwargs)

    monkeypatch.setattr(clip_maker, "render_clip", spy)

    results = _maker(tmp_path).make(source, _SEGMENTS, basename="stream")

    assert seen.get("commands", "").endswith(".cmds"), \
        "the maker rendered without a path at all"
    assert seen["existed"], "the sendcmd script was not written before render"
    assert seen["region"] == size, \
        "the measured box was dropped - the crop would be a 9:16 slice of " \
        "the whole desktop"
    assert results, "a clearly clip-worthy segment produced nothing"
    assert all(os.path.exists(r.path) for r in results)
    leftovers = [p for p in os.listdir(tmp_path / "clips")
                 if p.endswith(".cmds")]
    assert leftovers == [], f"the sendcmd script was left behind: {leftovers}"


def test_a_clip_nobody_moves_in_falls_back_to_the_static_box(monkeypatch):
    """path_for refuses a path that is not worth rendering, and that
    refusal must land on the measured rectangle - not on the whole frame,
    and not on a stale one."""
    import autoreel.clip_maker as clip_maker
    from autoreel.clip_maker import CROP_REGION, build_filter

    monkeypatch.setattr(clip_maker.face_region, "path_for",
                        lambda *a, **k: (None, []))

    box = {"x": 0.03, "y": 0.05, "width": 0.46, "height": 0.90}
    chain = build_filter(CROP_REGION, None, box, watermark=False)

    assert "sendcmd" not in chain
    assert chain.count("crop=") == 1
    assert "iw*0.4600" in chain


def test_the_monkey_pan_still_searches_only_the_call_pane():
    """Moving does not widen where it may look. The browser beside the
    call is off-limits whether the crop is static or walking - that is
    how twenty clips came out framed on a slots game."""
    pane = PROFILES["monkey"]["content_region"]

    assert pane["x"] + pane["width"] <= 0.5


# ── the pan has to LOOK smooth, not just be gentle ───────────────────
#
# "I dont like how the face sensor jumping like this it should be smooth
# and barely noticeable." The points were already speed-capped and
# damped - the jumping came from sendcmd, which does not interpolate. It
# STEPS to each value and holds it, so a path emitted twice a second
# lurches twice a second however carefully the points were smoothed.

def test_the_crop_is_told_where_to_be_far_more_often_than_it_is_measured():
    from autoreel.face_region import EMIT_FPS, pan_path

    measured = _stills(*([(0.25, 0.5)] * 4 + [(0.75, 0.5)] * 16))
    path = pan_path(measured, fps=2.0)

    assert len(path) > len(measured) * 3, \
        "the crop still moves at the sampling rate"
    gaps = [b[0] - a[0] for a, b in zip(path, path[1:])]
    assert max(gaps) <= (1.0 / EMIT_FPS) + 0.02


def test_the_steps_between_points_are_small_enough_not_to_read_as_a_jump():
    from autoreel.face_region import pan_path

    path = pan_path(_stills(*([(0.20, 0.5)] * 2 + [(0.80, 0.5)] * 20)),
                    fps=2.0)

    xs = [x for _, x, _ in path]
    steps = [abs(b - a) for a, b in zip(xs, xs[1:])]
    assert max(steps) < 0.02, f"biggest single move was {max(steps):.4f}"


def test_filling_in_invents_no_movement(): 
    """Every emitted point lies on the line between two the detector
    actually produced. A still person must stay still."""
    from autoreel.face_region import pan_path

    path = pan_path(_stills(*([(0.5, 0.5)] * 20)), fps=2.0)

    assert len({round(x, 3) for _, x, _ in path}) == 1


def test_it_still_arrives_where_the_person_went():
    from autoreel.face_region import pan_path

    path = pan_path(_stills(*([(0.25, 0.5)] * 4 + [(0.75, 0.5)] * 20)),
                    fps=2.0)

    xs = [x for _, x, _ in path]
    assert xs[0] < 0.4 and xs[-1] > 0.6


def test_a_path_too_short_to_fill_is_returned_as_it_is():
    from autoreel.face_region import _glide

    assert _glide([]) == []
    assert _glide([(0.0, 0.5, 0.5)]) == [(0.0, 0.5, 0.5)]


# ── a clip with nobody in it is not a clip about somebody ────────────

def test_no_faces_keeps_the_whole_frame_not_the_call_pane():
    """A stream is not one thing for ninety minutes. The profile is
    chosen once from a sample of the picture, and a clip taken at 90m can
    be the plain desktop while the sample at 8m was a call - so a taskbar
    full of Steam icons went out cropped to 9:16 as if it were a person."""
    import inspect

    from autoreel import clip_maker

    body = inspect.getsource(clip_maker.ClipMaker.make)

    assert "framing the call pane" not in body, \
        "no faces still falls back to a rectangle that guessed"
    assert "CROP_FIT" in body
