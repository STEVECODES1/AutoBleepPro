"""
How a 16:9 source gets cropped to a 9:16 vertical clip.

The default is CENTER, and that is a deliberate choice for this channel
rather than a placeholder for something cleverer.

WHY NOT FACE TRACKING BY DEFAULT
--------------------------------
`face_tracking.FaceTracker` was built for talking-head footage. Pointed at
GTA RP it does exactly what it was told to: it finds faces. The faces it
finds are NPCs and other players wandering through frame, so the crop
snaps between them and the result jitters around the scene while the
thing you actually clipped - centre-screen - drifts out of shot. On
gameplay, face tracking is not a better centre crop that occasionally
misses; it is reliably worse.

Since roughly 95% of this channel is gameplay, the cheapest strategy is
also the correct one: GTA puts the crosshair, the HUD and the action
centre-screen, a fixed centre crop keeps all three, and it cannot drift
because it never moves. FACE stays available as an opt-in for face-cam or
IRL segments, where its assumptions actually hold.

MOTION sits between them: it follows a smoothed centroid of frame-to-frame
motion, which suits busy action but costs an extra pass over the video.
It is opt-in for that reason, not because it is unreliable.

FIT crops nothing at all. A centre crop of a 16:9 frame throws away about
two thirds of the width, and on two-person webcam footage - a Monkey app
call, say - what survives is a tight shot of whoever happens to be in the
middle of the screen. That is not a framing choice anyone made; it is what
is left over. FIT scales the whole frame to the width of a 9:16 canvas and
fills the space above and below with a blurred, zoomed copy of the same
frame, so both people stay in shot and the picture still reaches the edges
instead of sitting between black bars.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

CROP_CENTER = "center"
CROP_MOTION = "motion"
CROP_FACE = "face"
# Not a crop at all: the whole frame is kept, on a blurred background.
CROP_FIT = "fit"

VALID_STRATEGIES = (CROP_CENTER, CROP_MOTION, CROP_FACE, CROP_FIT)

# The default for this project. Changing this constant changes the default
# for every clip, which is why it is a named constant with a test on it
# rather than a literal buried in a call site.
DEFAULT_CROP_STRATEGY = CROP_CENTER

# Content kinds that must never silently get face tracking.
GAMEPLAY_CONTENT = "gameplay"
FACECAM_CONTENT = "facecam"

_CONTENT_DEFAULTS = {
    GAMEPLAY_CONTENT: CROP_CENTER,
    FACECAM_CONTENT: CROP_FACE,
}


class CropStrategyError(ValueError):
    """An unrecognised crop strategy was configured."""


def default_for_content(content_kind: str = GAMEPLAY_CONTENT) -> str:
    """The strategy to use when config says nothing.

    Anything not explicitly known falls back to CENTER, because CENTER is
    the option that cannot go wrong on unknown footage - it never tracks
    the wrong thing.
    """
    return _CONTENT_DEFAULTS.get((content_kind or "").lower(), DEFAULT_CROP_STRATEGY)


def resolve_crop_strategy(config: Optional[Dict[str, Any]] = None,
                          content_kind: str = GAMEPLAY_CONTENT) -> str:
    """Read the configured strategy, or the default for this content kind.

    Accepts the whole app config or just its `clips` block. Raises on an
    unrecognised name rather than quietly falling back: a typo like
    "centre" silently becoming something else is how a channel's clips end
    up cropped a way nobody chose.
    """
    config = config or {}
    clips = config.get("clips", config) if isinstance(config, dict) else {}
    configured = (clips.get("crop_strategy") or "").strip().lower()
    if not configured or configured == "auto":
        return default_for_content(content_kind)
    if configured not in VALID_STRATEGIES:
        raise CropStrategyError(
            f"crop_strategy '{configured}' is not one of "
            f"{', '.join(VALID_STRATEGIES)}")
    return configured


def face_tracking_enabled(config: Optional[Dict[str, Any]] = None,
                          content_kind: str = GAMEPLAY_CONTENT) -> bool:
    """True only when face tracking was asked for explicitly."""
    return resolve_crop_strategy(config, content_kind) == CROP_FACE
