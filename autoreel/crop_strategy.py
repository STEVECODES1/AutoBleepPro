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
instead of sitting between black bars. It is the safe answer for footage
whose layout is unknown, and the wrong answer when the layout IS known:
a whole desktop shrunk to a strip puts two faces in a postage stamp.

REGION is that known case. This channel records two different things and
they want opposite framing:

  - a Monkey app call, where a browser and the call window share the
    screen and the clip is entirely about the call
  - GTA RP gameplay, where the action is centre-screen and a centre crop
    is exactly right

So the framing is chosen per PROFILE rather than per project. REGION cuts
out the rectangle a named profile points at and frames that, which is how
the call fills a phone screen instead of sitting in the corner of one.
Where that rectangle is depends on how the windows were arranged, so it
is written in fractions of the frame and checked against a real still
with --preview-crop rather than guessed.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

CROP_CENTER = "center"
CROP_MOTION = "motion"
CROP_FACE = "face"
# Not a crop at all: the whole frame is kept, on a blurred background.
CROP_FIT = "fit"
# Crop to a named rectangle of the source, then frame that.
CROP_REGION = "region"
# Two rectangles from the same frame, stacked one above the other and
# filling 9:16 together. This is the Monkey-app format the channel's own
# edits use: BOTH people on the call, one above the other, each filling
# half the height. No single crop can produce it - a crop keeps one
# rectangle - which is why every rectangle tried so far looked wrong.
CROP_STACK = "stack"
# A measured rectangle that MOVES, following the people inside it.
#
# Deliberately not CROP_FACE. That constant means the per-frame moviepy
# renderer, every profile is tested against ever becoming it, and the
# gameplay rule that face tracking never touches GTA is permanent. This
# is a different thing: a fixed-size box, measured once from the call
# pane, whose centre is walked along a damped path by sendcmd - the same
# transport the gameplay motion crop already uses.
#
# It is off unless asked for. face_region's own docstring argues a
# moving crop on a two-person call "swings between them every time
# someone talks", and that reasoning has not stopped being true - the
# damping below is what makes it survivable, not a proof it is better.
CROP_FACE_PAN = "face_pan"

VALID_STRATEGIES = (CROP_CENTER, CROP_MOTION, CROP_FACE, CROP_FIT,
                    CROP_REGION, CROP_STACK, CROP_FACE_PAN)

# The rectangle REGION keeps, as fractions of the source width and
# height. The default points at the right-hand third, full height, which
# is where a call window usually sits beside a browser - but the whole
# point is that it gets MEASURED from a real frame, not inherited.
# MEASURED from a real still of this channel's Monkey layout, not
# guessed: monkey.app fills the left half of the screen and howl.gg sits
# on the right. The previous value - x 0.50, width 0.37 - was the
# RIGHT-hand half, which is the slots browser. It framed the gambling
# window perfectly on every clip where no face was found.
DEFAULT_REGION = {"x": 0.03, "y": 0.05, "width": 0.46, "height": 0.90}

# Where the PEOPLE are, whatever else is on screen. Face detection only
# looks inside this, and it is what the crop falls back to when no face
# is found - so a clip can never be framed on the browser beside the
# call, which is what happened when the fallback was the whole frame or
# a stale rectangle.
DEFAULT_CONTENT_REGION = {"x": 0.03, "y": 0.05, "width": 0.46, "height": 0.90}

# The two rectangles STACK keeps - one per person on the call.
# These are the halves of a 16:9 frame that a two-window Monkey layout
# usually puts them in - and like DEFAULT_REGION they are a starting
# point to be checked against a real still with --preview-crop, not a
# measurement. Getting them wrong frames the browser twice.
DEFAULT_STACK = {
    "top": {"x": 0.0, "y": 0.0, "width": 0.5, "height": 1.0},
    "bottom": {"x": 0.5, "y": 0.0, "width": 0.5, "height": 1.0},
}

# Named framings for the kinds of content this channel actually records.
# A profile is a whole answer - strategy AND rectangle - because those
# two settings are meaningless apart: a region with a centre crop ignores
# the region, and a region strategy with no rectangle crops nothing.
PROFILES: Dict[str, Dict[str, Any]] = {
    # Two people on camera, in a window beside a browser. The clip is the
    # call; the browser is not in it.
    # Both people on the call, one above the other - the layout the
    # channel's own hand-made edits use.
    # One call pane, framed on whoever is in it. This is the layout the
    # channel actually records: monkey.app on the left of the screen,
    # howl.gg on the right, and the clip is the call.
    #
    # FACE_PAN rather than REGION: the box is still measured from the
    # call pane and still held at one size, but its centre is allowed to
    # walk. A person who stands up, leans out or swaps seats mid-clip was
    # ending up at the edge of a frame that was mostly wall, because one
    # rectangle for a whole minute can only be right about the average.
    # When nobody moves far enough to matter the path is refused and this
    # renders as the static rectangle it always was.
    "monkey": {"crop_strategy": CROP_FACE_PAN, "crop_region": DEFAULT_REGION,
               "content_region": DEFAULT_CONTENT_REGION},
    # Both people stacked, for a stream where the camera is a separate
    # pane from the call - the layout the hand-made CapCut edits use.
    "monkey_stack": {"crop_strategy": CROP_STACK, "stack": DEFAULT_STACK,
                     "content_region": DEFAULT_CONTENT_REGION},
    # The centre of the gameplay frame, filling the phone screen.
    #
    # This was CROP_FIT for a while, on the reasoning that a fit crop
    # "cannot be wrong" - nothing is cut, so whatever the moment was, it
    # is still in the picture. That argument is true and it is not the
    # one that matters. Measured on a real posted clip, the uncut 16:9
    # frame occupies about 31% of a 9:16 canvas: a thin band of picture
    # with blurred filler above and below it, and the burned-in captions
    # sitting in the blur rather than on the video. Nothing was lost and
    # nobody could see any of it.
    #
    # Short-form is watched on a phone held upright. Filling that screen
    # is not a nicety, it is the format. GTA puts the character, the
    # nametags and the action centre-screen, so the centre slice keeps
    # what the clip is about and throws away sky and pavement.
    #
    # Set clips.crop_strategy to "fit" to get the uncut frame back, or
    # "motion" for the tracking crop.
    "gta": {"crop_strategy": CROP_CENTER},
    # Layout unknown: keep everything, lose nothing.
    "whole": {"crop_strategy": CROP_FIT},
}
DEFAULT_PROFILE = "monkey"

# Set clips.profile to this and the framing is chosen per VIDEO from its
# title, instead of one global setting being wrong for half the content.
#
# This is the bug that put GTA clips through the Monkey rectangle: the
# profile is configured once, the channel streams two completely
# different things, and the auto-clip path after a stream has no way to
# say which one it just recorded. Clips came out framed on the left 46%
# of a gameplay frame - the crop for a call window that was not there.
AUTO_PROFILE = "auto"

# Matched against the stream title, longest first so "monkey app" wins
# over a bare "app". Titles on this channel name the content plainly -
# "stackswopo + gta D10 johnny cox + Lifestyle RP", "monkey app trolling"
# - which is what makes this reliable rather than clever.
_TITLE_HINTS = (
    ("monkey", "monkey"),
    ("omegle", "monkey"),
    ("trolling", "monkey"),
    # The site on screen beside the call. A Monkey stream is very often
    # titled after what was being gambled rather than after the app, and
    # those titles used to hit no hint at all and fall through to
    # `whole` - "yoo_howl" and "monkey_n_gamble_howl" are both real.
    ("howl", "monkey"),
    ("gamble", "monkey"),
    ("gambling", "monkey"),
    ("slots", "monkey"),
    ("casino", "monkey"),
    ("stake", "monkey"),
    ("roulette", "monkey"),
    ("gta", "gta"),
    ("rp", "gta"),
    ("roleplay", "gta"),
    ("fivem", "gta"),
    ("nopixel", "gta"),
    ("lifestyle", "gta"),
    ("roblox", "gta"),
    ("fortnite", "gta"),
    ("minecraft", "gta"),
)

# Hints that are only meaningful as whole words. "rp" inside "sharp",
# "burp" and "corporate" sent all three to the GTA motion crop, which is
# the wrong framing applied confidently - the worst kind. Anything short
# enough to hide inside an ordinary word belongs here.
_WHOLE_WORD_ONLY = frozenset({"rp", "gta", "stake"})


def profile_for_title(title: str, fallback: str = "whole") -> str:
    """The framing this stream's title asks for.

    Falls back to `whole` rather than to a guess: keeping the entire
    frame loses nothing, and a wrong CROP is unrecoverable once the clip
    is posted. Being un-cropped is a smaller failure than being cropped
    onto the wrong half of the screen.
    """
    text = " ".join(str(title or "").lower().split())
    if not text:
        return fallback
    # Underscores and punctuation are word breaks here: real filenames
    # are "monkey_n_gamble_howl", not "monkey n gamble howl".
    padded = " " + re.sub(r"[^a-z0-9]+", " ", text) + " "
    best, best_at = fallback, len(padded) + 1
    for needle, profile in _TITLE_HINTS:
        if needle in _WHOLE_WORD_ONLY:
            found = re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])",
                              padded)
            at = found.start() if found else -1
        else:
            at = padded.find(needle)
        if at >= 0 and at < best_at:
            best, best_at = profile, at
    return best

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


def resolve_profile(config: Optional[Dict[str, Any]] = None) -> dict:
    """The named profile's settings, merged over anything set directly.

    `clips.profile` names one of PROFILES; `clips.crop_strategy` and
    `clips.crop_region` still win when set, so a one-off override does
    not require inventing a profile for it.
    """
    config = config or {}
    clips = config.get("clips", config) if isinstance(config, dict) else {}
    name = str(clips.get("profile") or DEFAULT_PROFILE).strip().lower()
    if name == AUTO_PROFILE:
        # Chosen from the title the caller put in the config it passed
        # here, so nothing below needs to know about auto.
        name = profile_for_title(clips.get("content_title", ""))
    settings = dict(PROFILES.get(name) or PROFILES[DEFAULT_PROFILE])
    # A profile can be overridden per key without redefining the profile.
    custom = dict((clips.get("profiles") or {}).get(name) or {})
    settings.update(custom)
    for key in ("crop_strategy", "crop_region"):
        if clips.get(key):
            settings[key] = clips[key]
    return settings


def resolve_content_region(config: Optional[Dict[str, Any]] = None) -> Optional[dict]:
    """Where the people are, or None when the profile does not say.

    Face detection is confined to this and the crop falls back to it, so
    whatever else shares the screen - a browser, a slots game, a chat
    window - can never end up being the clip.
    """
    configured = resolve_profile(config).get("content_region")
    return _clamped(dict(configured)) if configured else None


def resolve_stack(config: Optional[Dict[str, Any]] = None) -> dict:
    """The two rectangles STACK composites, top first.

    Same fractions-of-the-frame contract as resolve_region, and each half
    is clamped the same way, because a rectangle running off an edge is
    an ffmpeg error rather than a slightly wrong crop.
    """
    configured = dict(resolve_profile(config).get("stack") or DEFAULT_STACK)
    halves = {}
    for slot in ("top", "bottom"):
        raw = dict(DEFAULT_STACK[slot])
        raw.update({k: v for k, v in (configured.get(slot) or {}).items()
                    if k in DEFAULT_REGION})
        halves[slot] = _clamped(raw)
    return halves


def _clamped(raw: dict) -> dict:
    def fraction(key: str, lowest: float = 0.0) -> float:
        try:
            value = float(raw[key])
        except (TypeError, ValueError, KeyError):
            value = DEFAULT_REGION[key]
        return min(1.0, max(lowest, value))

    region = {"x": fraction("x"), "y": fraction("y"),
              "width": fraction("width", 0.05),
              "height": fraction("height", 0.05)}
    region["width"] = min(region["width"], 1.0 - region["x"])
    region["height"] = min(region["height"], 1.0 - region["y"])
    return region


def resolve_region(config: Optional[Dict[str, Any]] = None) -> dict:
    """The crop rectangle, clamped to something ffmpeg can actually cut.

    Fractions rather than pixels so one setting is correct whether the
    source is 1080p or 4K, and so a rectangle measured on one recording
    still means the same thing on the next.
    """
    raw = dict(DEFAULT_REGION)
    raw.update({k: v for k, v in (resolve_profile(config).get("crop_region")
                                  or {}).items() if k in DEFAULT_REGION})

    def fraction(key: str, lowest: float = 0.0) -> float:
        try:
            value = float(raw[key])
        except (TypeError, ValueError):
            value = DEFAULT_REGION[key]
        return min(1.0, max(lowest, value))

    region = {"x": fraction("x"), "y": fraction("y"),
              "width": fraction("width", 0.05),
              "height": fraction("height", 0.05)}
    # A rectangle running off the right or bottom edge is an ffmpeg
    # error, not a crop that gets quietly clipped.
    region["width"] = min(region["width"], 1.0 - region["x"])
    region["height"] = min(region["height"], 1.0 - region["y"])
    return region


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
        # A profile is a fuller answer than a content kind, so it wins
        # when one is named.
        if clips.get("profile"):
            return str(resolve_profile(config).get(
                "crop_strategy", DEFAULT_CROP_STRATEGY))
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
