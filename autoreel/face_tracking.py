"""
Face-tracking crop assistance for AutoReel.

Samples frames from a clip, detects a subject's face position in each, and
produces a smoothed timeline of crop-center positions so `ClipRenderer` can
keep a streamer's face in frame instead of always using a fixed center-crop
(the "TRACK mode" from the dual-mode reframing approach this was modeled
after). When no face is ever found, the timeline is empty and callers fall
back to the existing static/center crop ("GENERAL mode").

`smooth_centers` and `interpolate_center` are pure Python (no mediapipe or
moviepy dependency) so they're unit-testable in isolation with hand-built
sample lists. `FaceTracker.detect_centers` is the only piece that touches
mediapipe/moviepy, and those imports are deferred so this module stays
importable without them installed, matching the rest of this package.
"""

import os
import urllib.request
from dataclasses import dataclass

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_detector/"
    "blaze_face_short_range/float16/latest/blaze_face_short_range.tflite"
)
MODEL_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "autoreel")
MODEL_PATH = os.path.join(MODEL_CACHE_DIR, "blaze_face_short_range.tflite")


def _ensure_model() -> str:
    """Download the MediaPipe face-detector model to a local cache if needed."""
    if not os.path.exists(MODEL_PATH):
        os.makedirs(MODEL_CACHE_DIR, exist_ok=True)
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    return MODEL_PATH


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def smooth_centers(
    samples: list[tuple[float, float, float]],
    smoothing: float = 0.85,
) -> list[tuple[float, float, float]]:
    """Turn raw (time, cx, cy) face detections into an EMA-smoothed timeline.

    `samples` need not be evenly spaced or gap-free — each new sample is
    blended with the running average, so a missed detection simply means
    the previous smoothed position holds until the next real detection
    arrives, rather than the crop jumping or jittering frame to frame.

    Empty input means no face was ever detected; the empty list return
    value is the signal callers should treat as "use the static/general
    crop fallback" rather than trying to track anything.
    """
    if not samples:
        return []

    ordered = sorted(samples, key=lambda s: s[0])
    smoothed: list[tuple[float, float, float]] = []
    prev_cx, prev_cy = ordered[0][1], ordered[0][2]
    for t, cx, cy in ordered:
        prev_cx = smoothing * prev_cx + (1 - smoothing) * cx
        prev_cy = smoothing * prev_cy + (1 - smoothing) * cy
        smoothed.append((t, prev_cx, prev_cy))
    return smoothed


def interpolate_center(
    timeline: list[tuple[float, float, float]],
    t: float,
    default: tuple[float, float] = (0.5, 0.5),
) -> tuple[float, float]:
    """Look up the crop center for time `t`.

    Holds the first/last known sample outside the timeline's range, and
    linearly interpolates between the two surrounding samples inside it.
    Returns `default` (frame center) for an empty timeline.
    """
    if not timeline:
        return default
    if t <= timeline[0][0]:
        return timeline[0][1], timeline[0][2]
    if t >= timeline[-1][0]:
        return timeline[-1][1], timeline[-1][2]

    for (t0, cx0, cy0), (t1, cx1, cy1) in zip(timeline, timeline[1:]):
        if t0 <= t <= t1:
            if t1 == t0:
                return cx0, cy0
            frac = (t - t0) / (t1 - t0)
            return cx0 + (cx1 - cx0) * frac, cy0 + (cy1 - cy0) * frac

    return default


@dataclass
class FaceTracker:
    """Samples frames from a moviepy clip and detects the primary face's position."""

    min_confidence: float = 0.5

    def detect_centers(self, clip, sample_interval: float = 0.5) -> list[tuple[float, float, float]]:
        """Return (time, cx_fraction, cy_fraction) for the largest detected
        face in each sampled frame. Frames with no detected face are
        omitted rather than padded, so gaps are handled by `smooth_centers`."""
        import mediapipe as mp
        import numpy as np

        model_path = _ensure_model()
        base_options = mp.tasks.BaseOptions(model_asset_path=model_path)
        options = mp.tasks.vision.FaceDetectorOptions(
            base_options=base_options, min_detection_confidence=self.min_confidence
        )

        samples: list[tuple[float, float, float]] = []
        detector = mp.tasks.vision.FaceDetector.create_from_options(options)
        try:
            t = 0.0
            while t < clip.duration:
                frame = clip.get_frame(t)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(frame))
                result = detector.detect(mp_image)

                if result.detections:
                    largest = max(
                        result.detections,
                        key=lambda d: d.bounding_box.width * d.bounding_box.height,
                    )
                    bbox = largest.bounding_box
                    cx = _clamp01((bbox.origin_x + bbox.width / 2) / frame.shape[1])
                    cy = _clamp01((bbox.origin_y + bbox.height / 2) / frame.shape[0])
                    samples.append((t, cx, cy))

                t += sample_interval
        finally:
            detector.close()

        return samples
