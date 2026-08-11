"""
Renders AutoReel highlights into vertical (9:16) clips for Reels/TikTok.

All moviepy imports are deferred into the methods that need them so this
module - and the rest of the package - stays importable and unit-testable
in environments where moviepy/ffmpeg aren't installed.
"""

import os
import sys
from dataclasses import dataclass

from .face_tracking import FaceTracker, interpolate_center, smooth_centers
from .highlights import Highlight

VERTICAL_WIDTH = 1080
VERTICAL_HEIGHT = 1920


def _suppress_fontconfig() -> None:
    """On Windows, ffmpeg/moviepy emit a noisy 'Fontconfig error: Cannot load
    default config file' warning because fontconfig isn't installed.  This
    doesn't affect rendering at all - it's purely cosmetic - but moviepy
    treats any stderr output from ffmpeg as a failure and marks every clip
    as failed.

    The fix is to point FONTCONFIG_FILE at a valid (even empty) config file
    before the first ffmpeg call. We write a minimal one into the system
    temp folder if it doesn't already exist.
    """
    if sys.platform != "win32":
        return
    if os.environ.get("FONTCONFIG_FILE"):
        return
    import tempfile
    cfg_path = os.path.join(tempfile.gettempdir(), "autoreel_fontconfig.conf")
    if not os.path.exists(cfg_path):
        with open(cfg_path, "w") as f:
            f.write('<?xml version="1.0"?>\n'
                    '<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n'
                    '<fontconfig>\n</fontconfig>\n')
    os.environ["FONTCONFIG_FILE"] = cfg_path


@dataclass
class ClipRenderer:
    output_dir: str
    # OFF by default. This class was written for talking-head footage;
    # pointed at gameplay it locks onto whatever NPC face crosses the
    # frame and the crop jitters around the scene while the action drifts
    # out of shot. See crop_strategy.py - `face` is an opt-in mode for
    # face-cam and IRL segments, not a better centre crop.
    face_tracking: bool = False

    def _to_vertical(self, clip):
        """Center-crop/resize a clip to fill a 1080x1920 vertical frame."""
        target_ratio = VERTICAL_WIDTH / VERTICAL_HEIGHT
        source_ratio = clip.w / clip.h

        if source_ratio > target_ratio:
            # Source is wider than target: crop the sides after matching height.
            resized = clip.resized(height=VERTICAL_HEIGHT)
            excess = resized.w - VERTICAL_WIDTH
            return resized.cropped(x1=excess / 2, x2=resized.w - excess / 2)

        # Source is taller/narrower than target: match width, crop top/bottom.
        resized = clip.resized(width=VERTICAL_WIDTH)
        excess = resized.h - VERTICAL_HEIGHT
        return resized.cropped(y1=excess / 2, y2=resized.h - excess / 2)

    def _to_vertical_tracked(self, clip, timeline):
        """Like `_to_vertical`, but slides the crop window per-frame to keep
        `timeline` (a smoothed face-center timeline from `face_tracking.py`)
        in view instead of always cropping around a fixed center ("TRACK
        mode"). Only the axis that has slack to pan (matching `_to_vertical`'s
        own choice of which axis to crop) actually moves."""
        target_ratio = VERTICAL_WIDTH / VERTICAL_HEIGHT
        source_ratio = clip.w / clip.h

        if source_ratio > target_ratio:
            resized = clip.resized(height=VERTICAL_HEIGHT)
            axis = "x"
        else:
            resized = clip.resized(width=VERTICAL_WIDTH)
            axis = "y"

        def crop_frame(get_frame, t):
            frame = get_frame(t)
            frame_h, frame_w = frame.shape[0], frame.shape[1]
            cx, cy = interpolate_center(timeline, t)

            if axis == "x":
                excess = frame_w - VERTICAL_WIDTH
                x1 = int(round(cx * frame_w - VERTICAL_WIDTH / 2))
                x1 = max(0, min(x1, excess))
                return frame[:, x1 : x1 + VERTICAL_WIDTH]

            excess = frame_h - VERTICAL_HEIGHT
            y1 = int(round(cy * frame_h - VERTICAL_HEIGHT / 2))
            y1 = max(0, min(y1, excess))
            return frame[y1 : y1 + VERTICAL_HEIGHT, :]

        return resized.transform(crop_frame)

    def _face_timeline(self, clip):
        """Detect+smooth a face-center timeline for `clip`. Returns an empty
        list (falling back to the static "GENERAL" crop) if no face was ever
        found, or if face detection itself isn't usable (mediapipe missing,
        model download failed, etc.) - a missing/broken tracker should never
        break rendering, just make it fall back to the old static crop."""
        try:
            samples = FaceTracker().detect_centers(clip)
        except Exception:
            return []
        return smooth_centers(samples)

    def render(self, source_path: str, highlight: Highlight, filename: str) -> str:
        """Cut and reframe one highlight to vertical; returns the output path."""
        from moviepy import VideoFileClip

        _suppress_fontconfig()

        os.makedirs(self.output_dir, exist_ok=True)
        output_path = os.path.join(self.output_dir, filename)

        with VideoFileClip(source_path) as source:
            subclip = source.subclipped(highlight.start, highlight.end)

            timeline = self._face_timeline(subclip) if self.face_tracking else []
            if timeline:
                vertical = self._to_vertical_tracked(subclip, timeline)
            else:
                vertical = self._to_vertical(subclip)

            vertical.write_videofile(output_path, codec="libx264", audio_codec="aac", logger=None)
            vertical.close()

        return output_path

    def render_all(self, source_path: str, highlights: list[Highlight], prefix: str = "clip") -> list[str]:
        paths = []
        for i, highlight in enumerate(highlights, start=1):
            filename = f"{prefix}_{i:02d}.mp4"
            paths.append(self.render(source_path, highlight, filename))
        return paths
