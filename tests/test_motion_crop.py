"""
A gameplay crop that follows the action instead of sitting dead centre.

A centre crop keeps the crosshair and the HUD and misses the thing that
made the clip. The ask was a crop centred on the action that "kinda
moves slightly" when something happens elsewhere - a drift, not a chase.
"""

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from autoreel.clip_maker import (CROP_WIDTH_EXPR, build_filter,
                                 motion_crop_filter)
from autoreel.motion_region import (DEADZONE, MAX_PAN_PER_S, SAMPLE_FPS,
                                    commands_file, is_worth_moving, pan_path,
                                    sample_args)


def _readings(*targets, activity=9.0):
    return [(i + 1, x, activity) for i, x in enumerate(targets)]


def test_the_crop_drifts_rather_than_snapping():
    """A crop that jumps every time a car passes is unpleasant to watch
    in a way a viewer feels without being able to name."""
    # Action appears hard left and stays there.
    path = pan_path(_readings(*([0.05] * int(SAMPLE_FPS * 4))))

    positions = [x for _, x in path]
    steps = [abs(b - a) for a, b in zip(positions, positions[1:])]

    assert max(steps) <= MAX_PAN_PER_S / SAMPLE_FPS + 1e-6, \
        "it moved faster than the speed cap"
    assert positions[-1] < 0.5, "it never moved toward the action at all"


def test_a_crop_that_is_roughly_right_holds_perfectly_still():
    """Without a deadzone the crop creeps constantly, which reads as a
    drifting camera nobody asked for."""
    nudged = 0.5 + DEADZONE / 3
    path = pan_path(_readings(*([nudged] * 12)))

    assert {x for _, x in path} == {0.5}


def test_a_still_scene_does_not_move_the_crop():
    """A menu, a garage, a loading screen - all frames change a little
    and none of them are action."""
    quiet = [(i + 1, 0.05, 0.0) for i in range(12)]

    assert {x for _, x in pan_path(quiet)} == {0.5}


def test_a_path_that_never_leaves_the_middle_is_not_worth_rendering():
    """It is a centre crop written the expensive way, and it adds a
    filter, a temp file and a way to fail for no visible difference."""
    assert not is_worth_moving(pan_path(_readings(*([0.5] * 12))))
    assert is_worth_moving(pan_path(_readings(*([0.02] * 40))))


def test_the_pan_is_written_as_timestamped_commands():
    """The alternative is one enormous crop expression with a hundred
    nested if()s in it, which is unreadable and slow to parse."""
    body = commands_file([(0.0, 0.5), (0.25, 0.46)], CROP_WIDTH_EXPR)

    lines = [line for line in body.splitlines() if line.strip()]
    assert len(lines) == 2
    assert lines[0].startswith("0.00 crop x ")
    assert lines[1].startswith("0.25 crop x ")


def test_the_left_edge_is_clamped_inside_the_frame():
    """Action at the very edge must not produce a crop that runs off it
    - ffmpeg errors on that rather than clamping for you."""
    body = commands_file([(0.0, 0.0), (1.0, 1.0)], CROP_WIDTH_EXPR)

    assert "clip(" in body
    assert f"iw-{CROP_WIDTH_EXPR}" in body


def test_the_pan_and_the_crop_agree_on_the_width():
    """The path computes its left edge from half the crop width. If the
    two ever disagree the pan lands half a crop off target."""
    body = commands_file([(0.0, 0.5)], CROP_WIDTH_EXPR)

    assert CROP_WIDTH_EXPR in body
    assert CROP_WIDTH_EXPR in motion_crop_filter("/tmp/x.cmds")


def test_motion_replaces_the_static_crop_not_adds_to_it():
    """Two crop filters in one chain crops the crop."""
    chain = build_filter(motion_commands="/tmp/x.cmds")

    assert chain.count("crop=") == 1
    assert "sendcmd" in chain


def test_captions_and_watermark_still_come_after_the_moving_crop():
    chain = build_filter(caption_path="/tmp/c.ass", watermark=False,
                         motion_commands="/tmp/x.cmds")

    assert chain.index("sendcmd") < chain.index("subtitles")


def test_frames_are_sampled_small_and_gray():
    """Motion at 160x90 is the same motion as at 1080p for this, and it
    is 70x less data to move."""
    args = sample_args("/in.mp4", 10.0, 30.0)

    assert args[args.index("-pix_fmt") + 1] == "gray"
    assert "rawvideo" in args
    assert "160:90" in " ".join(args)
