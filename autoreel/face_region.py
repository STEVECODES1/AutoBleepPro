"""
Find the rectangle the people are in, once per clip.

WHY NOT FACE TRACKING
---------------------
There is already a FaceTracker here that follows a face frame by frame,
and it is the wrong tool for this. Following a face means the crop moves,
and a crop that moves on a two-person call swings between them every time
someone talks - which is nauseating to watch and looks like a mistake.
It is also the thing that must never touch gameplay, because it locks
onto NPC faces.

What a Monkey-app clip needs is the opposite: ONE fixed rectangle, held
for the whole clip, containing whoever is on camera. So this samples a
handful of frames, collects every face it sees across all of them, and
returns the single box that covers them - a measurement, not a tracker.
The existing region crop then does the work, at full ffmpeg speed, with
no per-frame anything.

WHY IT IS MEASURED PER CLIP
---------------------------
The configured rectangle is a guess typed into config.json once, and a
guess is wrong the moment the streamer moves the call window - which is
exactly what happened: the rectangle was aimed at the right-hand side,
the call moved, and twenty clips came out framed on a browser showing a
slots game. A measurement cannot go stale that way.

WHEN IT FINDS NOTHING
---------------------
Returns None, and the caller keeps the configured rectangle. No faces is
a normal answer for a stretch of gameplay, a loading screen, or a clip
where everyone is off camera, and it must never be the reason a clip
fails to render.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import Optional

# Enough to find the window without paying for the whole clip. The call
# window does not move mid-clip; this is looking for where it IS, not
# where it goes.
# Stills taken across the clip. Twelve rather than six: this measures ONE
# rectangle that then has to hold for the whole clip, and six samples over
# 45 seconds is a still every 7.5s - thin evidence for a decision that
# cannot be revised once the clip is rendered. Each still is a mediapipe
# pass, so this is the expensive knob; twelve is about a second per clip
# on a 4060.
SAMPLE_COUNT = 12

# Faces are small in a full-screen capture. Everything below this is
# usually a thumbnail, an avatar in a chat sidebar, or a face on the
# stream the streamer is reacting to.
MIN_FACE_FRACTION = 0.012

# Room around the faces, as a fraction of the box. Cropping to the faces
# alone gives a shot of two foreheads; people expect shoulders and some
# of the room.
PADDING = 0.55

# 9:16. The box is grown to this before it is returned, so the region
# crop is not stretching or re-cropping afterwards.
TARGET_ASPECT = 9 / 16

_TIMEOUT = 120


# mediapipe prints a block of TensorFlow Lite warnings per detector -
# "Feedback manager requires a model with a single signature inference"
# and friends. They are harmless and they are also six lines per clip in
# the one window that carries the real failures, which is how a genuine
# error gets skimmed past. Set before the first import; after it, the
# loggers are already built and the values are ignored.
def _quiet_tensorflow() -> None:
    os.environ.setdefault("GLOG_minloglevel", "2")
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
    os.environ.setdefault("GRPC_VERBOSITY", "ERROR")


def have_mediapipe() -> bool:
    _quiet_tensorflow()
    try:
        import mediapipe  # noqa: F401

        return True
    except Exception:
        return False


def sample_args(source: str, start: float, duration: float,
                out_dir: str, count: int = SAMPLE_COUNT,
                within: Optional[dict] = None) -> list:
    """ffmpeg pulling `count` stills spread across the window.

    `within` restricts the stills to one rectangle of the frame, so the
    detector never even sees the rest. That is what stops a face on the
    browser beside the call - a thumbnail, a streamer being reacted to,
    a face on a slots banner - from deciding the framing.
    """
    every = max(0.5, duration / max(1, count))
    crop = ""
    if within:
        crop = (f"crop=iw*{within['width']:.4f}:ih*{within['height']:.4f}:"
                f"iw*{within['x']:.4f}:ih*{within['y']:.4f},")
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.2f}", "-t", f"{duration:.2f}", "-i", source,
        "-vf", f"fps=1/{every:.3f},{crop}scale=960:-2",
        "-frames:v", str(count),
        os.path.join(out_dir, "frame_%02d.png"),
    ]


def _to_whole_frame(box: dict, within: dict) -> dict:
    """A box measured inside `within`, expressed against the whole frame."""
    return {
        "x": round(within["x"] + box["x"] * within["width"], 4),
        "y": round(within["y"] + box["y"] * within["height"], 4),
        "width": round(box["width"] * within["width"], 4),
        "height": round(box["height"] * within["height"], 4),
    }


def _boxes_in(path: str, detector) -> list:
    """(x, y, w, h) in fractions, for every face in one still."""
    import mediapipe as mp
    import numpy as np

    try:
        from PIL import Image

        with Image.open(path) as handle:
            frame = np.ascontiguousarray(handle.convert("RGB"))
    except Exception:
        return []

    height, width = frame.shape[0], frame.shape[1]
    if not height or not width:
        return []

    try:
        result = detector.detect(
            mp.Image(image_format=mp.ImageFormat.SRGB, data=frame))
    except Exception:
        return []

    found = []
    for detection in getattr(result, "detections", None) or []:
        box = detection.bounding_box
        w = box.width / width
        h = box.height / height
        if w * h < MIN_FACE_FRACTION:
            continue
        found.append((box.origin_x / width, box.origin_y / height, w, h))
    return found


def _steady_box(per_still: list) -> Optional[dict]:
    """The rectangle to hold for the whole clip, from every still.

    Two different questions, and conflating them is what put a person at
    the edge of their own clip:

      WITHIN one still, faces are concurrent - a two-person call needs
      both, so those are unioned by _cover.

      ACROSS stills they are the same people at different MOMENTS. A
      union there spans where someone stood at second 0 and where they
      stood at second 40, then centres on the wall between the two.

    So: union each still, then take the MEDIAN of those. Someone leaning
    out of shot for one sample, or a face passing behind, moves a median
    barely at all where a union is dragged the whole way.

    Size comes from the roomiest typical still rather than the median
    one. A crop slightly too generous keeps a head in frame when somebody
    leans; slightly too tight cuts it off. Only one of those is
    recoverable after the clip is posted.
    """
    stills = [_cover(boxes) for boxes in per_still if boxes]
    stills = [box for box in stills if box]
    if not stills:
        return None
    if len(stills) == 1:
        return stills[0]

    def middle(values: list) -> float:
        ordered = sorted(values)
        return ordered[len(ordered) // 2]

    centre_x = middle([b["x"] + b["width"] / 2 for b in stills])
    centre_y = middle([b["y"] + b["height"] / 2 for b in stills])
    width = _percentile(sorted(b["width"] for b in stills), 0.75)
    height = _percentile(sorted(b["height"] for b in stills), 0.75)

    box = {"x": centre_x - width / 2, "y": centre_y - height / 2,
           "width": width, "height": height}
    return _slide_inside(box)


def _slide_inside(box: dict) -> dict:
    """Move a box back inside the frame rather than squashing it.

    Shrinking to fit would cut off the face this exists to keep.
    """
    width = min(1.0, box["width"])
    height = min(1.0, box["height"])
    x = min(max(0.0, box["x"]), 1.0 - width)
    y = min(max(0.0, box["y"]), 1.0 - height)
    return {"x": round(x, 4), "y": round(y, 4),
            "width": round(width, 4), "height": round(height, 4)}


def _percentile(values: list, fraction: float) -> float:
    """`fraction` of the way up a sorted list."""
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, int(round(fraction * (len(values) - 1)))))
    return values[index]


def _cover(boxes: list) -> Optional[dict]:
    """One rectangle containing all of them, padded and made 9:16.

    Everything handed here is CONCURRENT - faces seen at the same moment.
    A two-person call is the whole point of this profile, so both have to
    fit, and that means a union. Spreading boxes from different MOMENTS
    across this is what produced the bad crops; see _steady_box.
    """
    if not boxes:
        return None

    left = min(x for x, _, _, _ in boxes)
    top = min(y for _, y, _, _ in boxes)
    right = max(x + w for x, _, w, _ in boxes)
    bottom = max(y + h for _, y, _, h in boxes)

    width = right - left
    height = bottom - top
    left -= width * PADDING / 2
    right += width * PADDING / 2
    # More room below than above: a face sits in the upper part of a
    # torso, and padding evenly crops the chin while leaving headroom.
    top -= height * PADDING * 0.45
    bottom += height * PADDING * 0.9

    width = max(1e-6, right - left)
    height = max(1e-6, bottom - top)

    # Grow the short side to 9:16 rather than shrinking the long one -
    # shrinking would cut off a face this exists to keep.
    if width / height > TARGET_ASPECT:
        wanted = width / TARGET_ASPECT
        middle = (top + bottom) / 2
        top, bottom = middle - wanted / 2, middle + wanted / 2
    else:
        wanted = height * TARGET_ASPECT
        middle = (left + right) / 2
        left, right = middle - wanted / 2, middle + wanted / 2

    # Slide back inside the frame before clamping, so a box that ran off
    # the edge keeps its size instead of being squashed against it.
    if right > 1.0:
        left, right = left - (right - 1.0), 1.0
    if left < 0.0:
        right, left = right - left, 0.0
    if bottom > 1.0:
        top, bottom = top - (bottom - 1.0), 1.0
    if top < 0.0:
        bottom, top = bottom - top, 0.0

    left, top = max(0.0, left), max(0.0, top)
    right, bottom = min(1.0, right), min(1.0, bottom)
    if right - left < 0.05 or bottom - top < 0.05:
        return None

    return {"x": round(left, 4), "y": round(top, 4),
            "width": round(right - left, 4), "height": round(bottom - top, 4)}


def region_for(source: str, start: float, duration: float,
               within: Optional[dict] = None) -> Optional[dict]:
    """The rectangle the people are in, or None if none were found.

    `within` confines the search to one part of the frame - the call
    pane - so nothing outside it can be framed. None is a normal answer
    (gameplay, a loading screen, everyone off camera) and the caller
    keeps whatever rectangle it already had.
    """
    if not have_mediapipe() or not shutil.which("ffmpeg"):
        return None
    _quiet_tensorflow()

    workspace = tempfile.mkdtemp(prefix="faces_")
    try:
        try:
            subprocess.run(sample_args(source, start, duration, workspace,
                                       within=within),
                           timeout=_TIMEOUT, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            return None

        stills = sorted(name for name in os.listdir(workspace)
                        if name.endswith(".png"))
        if not stills:
            return None

        import mediapipe as mp

        from .face_tracking import _ensure_model

        try:
            options = mp.tasks.vision.FaceDetectorOptions(
                base_options=mp.tasks.BaseOptions(
                    model_asset_path=_ensure_model()),
                min_detection_confidence=0.5)
            detector = mp.tasks.vision.FaceDetector.create_from_options(options)
        except Exception:
            return None

        # Grouped per still, NOT flattened: which faces were on screen
        # together is the difference between framing a two-person call
        # and framing the gap between where one person used to be.
        boxes = []
        try:
            for name in stills:
                boxes.append(_boxes_in(os.path.join(workspace, name), detector))
        finally:
            try:
                detector.close()
            except Exception:
                pass

        found = _steady_box(boxes)
        # Measured inside the crop, so it has to be mapped back before
        # anyone uses it against the whole frame.
        return _to_whole_frame(found, within) if (found and within) else found
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
