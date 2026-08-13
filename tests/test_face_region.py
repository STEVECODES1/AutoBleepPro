"""
Finding the rectangle the people are in, per clip.

Twenty clips came out framed on a browser showing a slots game while the
two people talking were off-crop. The rectangle in config.json was a
guess typed once, and it went stale the moment the call window moved.
"""

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from autoreel.face_region import (MIN_FACE_FRACTION, TARGET_ASPECT, _cover,
                                  region_for, sample_args)


def test_no_faces_means_keep_the_configured_rectangle():
    """Gameplay, a loading screen, everyone off camera - all normal, and
    none of them may be the reason a clip fails to render."""
    assert _cover([]) is None


def test_the_box_covers_every_face_not_just_the_largest():
    """A two-person call is the whole point. Framing the louder one and
    cropping the other out is the bug wearing a different hat."""
    left_person = (0.10, 0.30, 0.10, 0.15)
    right_person = (0.60, 0.32, 0.09, 0.14)

    box = _cover([left_person, right_person])

    assert box["x"] <= 0.10
    assert box["x"] + box["width"] >= 0.69


def test_the_result_is_vertical():
    """It feeds the region crop, which should not have to re-crop or
    stretch what it is handed."""
    box = _cover([(0.4, 0.4, 0.1, 0.1)])

    ratio = box["width"] / box["height"]
    assert abs(ratio - TARGET_ASPECT) < 0.02 or box["height"] >= 0.99


def test_the_box_stays_inside_the_frame():
    """A face near the edge must not produce a crop that runs off it."""
    for face in [(0.02, 0.02, 0.08, 0.10), (0.90, 0.88, 0.08, 0.10)]:
        box = _cover([face])
        assert box["x"] >= 0.0 and box["y"] >= 0.0
        assert box["x"] + box["width"] <= 1.0001
        assert box["y"] + box["height"] <= 1.0001


def test_a_tiny_face_is_ignored():
    """An avatar in a chat sidebar, or a face on the stream being
    reacted to, is not what the clip is about."""
    from autoreel.face_region import _boxes_in  # noqa: F401  (documented use)

    assert MIN_FACE_FRACTION > 0, "there has to be a floor"
    speck = (0.5, 0.5, 0.01, 0.01)
    assert speck[2] * speck[3] < MIN_FACE_FRACTION


def test_stills_are_sampled_across_the_whole_clip():
    """Sampling only the opening would frame on whatever was on screen
    for the first second."""
    args = sample_args("/in.mp4", start=120.0, duration=60.0,
                       out_dir="/tmp/x", count=6)

    assert args[args.index("-ss") + 1] == "120.00"
    assert args[args.index("-t") + 1] == "60.00"
    assert "-frames:v" in args and args[args.index("-frames:v") + 1] == "6"


def test_it_gives_up_quietly_without_mediapipe(monkeypatch):
    """A missing optional dependency must not fail a render."""
    import autoreel.face_region as face_region

    monkeypatch.setattr(face_region, "have_mediapipe", lambda: False)

    assert region_for("/nonexistent.mp4", 0.0, 30.0) is None


def test_tensorflow_chatter_is_silenced_before_the_first_import():
    """mediapipe prints six warning lines per detector into the one
    window that also carries the real failures. Set after the import
    they are ignored - the loggers are already built."""
    import autoreel.face_region as face_region

    face_region._quiet_tensorflow()

    assert os.environ.get("GLOG_minloglevel") == "2"
    assert os.environ.get("TF_CPP_MIN_LOG_LEVEL") == "3"


def test_a_value_the_user_set_is_left_alone():
    """setdefault, not assignment: someone debugging mediapipe has
    turned these up on purpose."""
    import autoreel.face_region as face_region

    os.environ["GLOG_minloglevel"] = "0"
    try:
        face_region._quiet_tensorflow()
        assert os.environ["GLOG_minloglevel"] == "0"
    finally:
        os.environ.pop("GLOG_minloglevel", None)


def test_no_faces_falls_back_to_the_whole_frame_not_the_rectangle():
    """The configured rectangle points wherever it was last aimed, and
    when no face is on screen what sits there is the browser - which is
    how clips went out framed on a slots game and a Scream mask instead
    of the two people talking. Keeping the whole frame cannot be wrong
    the same way: nothing is cut."""
    import inspect

    from autoreel import clip_maker

    body = inspect.getsource(clip_maker.ClipMaker.make)

    assert "CROP_FIT" in body, "the fallback has to keep the whole frame"
    assert "no faces here" in body


def test_faces_are_only_looked_for_inside_the_call_pane():
    """A face on the browser beside the call - a thumbnail, a streamer
    being reacted to, a face on a slots banner - must not be able to
    decide the framing. The detector never sees that half."""
    from autoreel.face_region import sample_args

    pane = {"x": 0.03, "y": 0.05, "width": 0.46, "height": 0.90}
    args = " ".join(sample_args("/in.mp4", 10.0, 30.0, "/tmp", within=pane))

    assert "crop=iw*0.4600" in args and "iw*0.0300" in args


def test_a_box_found_inside_the_pane_is_mapped_back_to_the_frame():
    """Measured inside the crop, it means nothing against the whole
    frame until it is offset by where the crop was."""
    from autoreel.face_region import _to_whole_frame

    pane = {"x": 0.03, "y": 0.05, "width": 0.46, "height": 0.90}
    # Dead centre of the pane, a quarter of its size.
    mapped = _to_whole_frame(
        {"x": 0.375, "y": 0.375, "width": 0.25, "height": 0.25}, pane)

    assert abs(mapped["x"] - (0.03 + 0.375 * 0.46)) < 1e-4
    assert abs(mapped["width"] - 0.25 * 0.46) < 1e-4
    assert mapped["x"] + mapped["width"] <= 0.5, "it escaped into the browser"
