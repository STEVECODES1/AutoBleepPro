"""The frame that makes someone stop scrolling.

Until now the thumbnail was whatever the platform grabbed - in practice
the first frame, which on a clip cut out of a stream is the tail end of
whatever came before it. A grey loading screen, a menu, the back of
somebody's head.

The clip already gets looked at by a model to decide it was worth
cutting. Asking the same model which of eight frames is the one worth
showing is a small extra question with a large effect on whether anyone
presses play.

No burned-in text and no border. A thumbnail that looks made rather
than captured reads as an ad, and the clips already carry their title
across the top of the frame - saying it twice is worse than saying it
once.

FULL STREAMS get more (style="stream"). The channels that win on this
same footage - Woper Recap, WopoLive - all do the same three things: one
character big in the frame, bright punchy colour, the game's logo in the
corner, and on many a cut-out of the streamer on the right. The stream
thumbnail that prompted this was a dark wide shot with the people a
fifth of the height. So for a stream:

- dark frames are dropped before anything is chosen, and with no model
  the brightest, most colourful look wins instead of a fixed timestamp;
- the model also marks where the main character is, and the picture is
  zoomed onto them (no answer: a gentle zoom on the middle, which also
  takes the minimap and chat box off the edges);
- a dark frame is lifted before the grade;
- a sticker - a cut-out PNG the channel supplies - goes on the right.

What it does get: a grade. Stream frames come out dark and flat next to
the thumbnails that win on the same footage - contrast, saturation and
sharpness all visibly pushed (a re-upload channel took 10-14k views on
the same streams this channel posted to 300-800). And, only when a
picture is supplied for it, a small logo in the top-left corner - the
game's mark, so the scroller knows the game before reading anything.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import Optional

# Eight looks across the clip. Enough that a reaction somewhere in the
# middle is caught; few enough that the request stays one small call.
# The ends are skipped: the first frame is the previous shot and the last
# is usually the cut.
SAMPLE_FRACTIONS = (0.10, 0.22, 0.34, 0.46, 0.58, 0.70, 0.82, 0.92)

# A full stream is hours long; eight looks across it miss most of it.
STREAM_SAMPLES = 16
STREAM_SPAN = (0.05, 0.95)

# signalstats YAVG (16-235). Below this a frame is a dark room or a
# night street - the kind of picture that disappears in a feed.
DARK_YAVG = 55
# Below this the frame is lifted before the grade.
LIFT_BELOW_YAVG = 95

# The zoom: the character's box fills this share of the picture's height,
# never closer than MAX_ZOOM (a 1080p stream upscaled further goes soft).
SUBJECT_HEIGHT = 0.80
MAX_ZOOM = 2.2
# With no box, a gentle zoom on the middle - it also trims the HUD that
# sits on the edges (minimap bottom-left, chat top-left).
DEFAULT_ZOOM = 1.25
# Where the character sits across the picture. Left of centre, so a
# sticker on the right does not cover them.
SUBJECT_X = 0.40

THUMB_W, THUMB_H = 1280, 720
# The sticker's height as a share of the thumbnail's, on the right edge.
STICKER_HEIGHT = 0.92

# Where to grab from when no model answers. Just past a third: far enough
# in that the previous shot is gone, early enough that most clips have
# started doing whatever they were cut for.
FALLBACK_FRACTION = 0.35

PROMPT = """\
These are %(count)d frames from one short video clip, in order.

Pick the ONE that would make somebody scrolling past stop and watch.
Strongly prefer a frame where ONE character is close to the camera and
fills a large part of the picture against a simple background, and where
the stream's overlay - chat box, minimap, health bars, alerts, webcam
box - is least visible. After that: a face mid-reaction, a moment of
impact, or something plainly odd on screen. Avoid loading screens, menus,
plain scenery, crowded wide shots, motion blur, and frames where nothing
is happening.

Answer as JSON: {"frame": <number from 1 to %(count)d>}
No other text.
"""

STREAM_PROMPT = """These are %(count)d frames from one long gaming stream (GTA roleplay), in
order. One will be the video's thumbnail, zoomed in on its main
character.

Pick the ONE that would make somebody scrolling past stop and click.
Strongly prefer a bright, well-lit frame where ONE character is clearly
visible, facing the camera or in profile, close enough that their face
and upper body will still look sharp when zoomed in, against a simple
background. After that: a face mid-reaction, a fight, a chase, an arrest,
something plainly odd. Avoid dark frames, loading screens, menus, plain
scenery, crowded wide shots and motion blur.

Then give the box around that character's head and upper body (head to
waist), as [ymin, xmin, ymax, xmax] on a 0-1000 scale of the frame.

Answer as JSON: {"frame": <number from 1 to %(count)d>, "box": [ymin, xmin, ymax, xmax]}
No other text.
"""


# Contrast, saturation, a touch of brightness, then sharpening. Enough to
# read as a thumbnail rather than a paused stream; not so much that skin
# turns orange.
GRADE = ("eq=contrast=1.12:saturation=1.30:brightness=0.02,"
         "unsharp=5:5:0.8:5:5:0.0")

# Streams push further: the winning thumbnails on this footage are
# visibly brighter and more saturated than a clip needs to be.
STREAM_GRADE = ("eq=contrast=1.18:saturation=1.45:brightness=0.03,"
                "unsharp=5:5:1.0:5:5:0.0")

# The logo's width as a share of the thumbnail's, and its inset.
LOGO_WIDTH = 0.20
LOGO_MARGIN = 0.03


def _grab(source: str, at: float, out_path: str, width: int = 0,
          vf: str = "") -> bool:
    """One JPEG at one timestamp. False if it could not be read."""
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{max(0.0, at):.2f}", "-i", source, "-frames:v", "1"]
    filters = [f for f in (f"scale={width}:-2" if width else "", vf) if f]
    if filters:
        args += ["-vf", ",".join(filters)]
    args += ["-q:v", "2", out_path]
    try:
        subprocess.run(args, timeout=120, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return os.path.isfile(out_path) and os.path.getsize(out_path) > 0


def timestamps(duration: float, samples: int = 0) -> list:
    """Where to look, in seconds from the start of the clip. `samples`
    spreads that many looks evenly across STREAM_SPAN instead."""
    if duration <= 0:
        return []
    if samples:
        start, end = STREAM_SPAN
        step = (end - start) / max(1, samples - 1)
        return [duration * (start + step * i) for i in range(samples)]
    return [duration * f for f in SAMPLE_FRACTIONS]


def _measure(path: str) -> Optional[tuple]:
    """(brightness, saturation) of an image from ffmpeg's signalstats,
    or None when it cannot be read."""
    import re

    try:
        found = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path,
             "-vf", "signalstats,metadata=print:file=-", "-f", "null", "-"],
            capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    y = re.search(r"YAVG=([\d.]+)", found or "")
    sat = re.search(r"SATAVG=([\d.]+)", found or "")
    if not y:
        return None
    return float(y.group(1)), float(sat.group(1)) if sat else 0.0


def _appeal(stats: Optional[tuple]) -> float:
    """How much a look has going for it before anyone looks at it."""
    if not stats:
        return 0.0
    bright, sat = stats
    return min(bright, 150.0) + 1.5 * sat


def _read_box(raw: str) -> Optional[tuple]:
    """The model's [ymin, xmin, ymax, xmax] (0-1000) as fractions
    (x0, y0, x1, y1), or None for anything unusable."""
    import re

    if not raw:
        return None
    text = str(raw).strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        box = json.loads(text).get("box")
        ymin, xmin, ymax, xmax = (float(v) / 1000.0 for v in box)
    except (ValueError, TypeError, AttributeError):
        return None
    x0, x1 = sorted((max(0.0, min(1.0, xmin)), max(0.0, min(1.0, xmax))))
    y0, y1 = sorted((max(0.0, min(1.0, ymin)), max(0.0, min(1.0, ymax))))
    # A box that is a sliver, or the whole frame, says nothing useful.
    if (x1 - x0) < 0.03 or (y1 - y0) < 0.05 or (y1 - y0) > 0.98:
        return None
    return x0, y0, x1, y1


def zoom_window(box: Optional[tuple], aspect: float = 16 / 9) -> tuple:
    """(x, y, w, h) as fractions of the frame: the 16:9 window to keep.

    With a box, the character's head-to-waist fills SUBJECT_HEIGHT of the
    picture and sits at SUBJECT_X across it. Without one, DEFAULT_ZOOM on
    the middle. Always inside the frame, never closer than MAX_ZOOM.
    """
    if box is None:
        w = h = 1.0 / DEFAULT_ZOOM
        return (1 - w) / 2, (1 - h) / 2, w, h
    x0, y0, x1, y1 = box
    h = (y1 - y0) / SUBJECT_HEIGHT
    h = max(1.0 / MAX_ZOOM, min(1.0, h))
    w = h                       # same zoom both ways keeps 16:9
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    x = cx - w * SUBJECT_X
    y = cy - h * 0.45           # a little headroom above the middle
    x = max(0.0, min(1.0 - w, x))
    y = max(0.0, min(1.0 - h, y))
    return x, y, w, h


def _choose(frames: list, ask=None, prompt_template: str = PROMPT,
            want_box: bool = False):
    """Which frame (0-based), from a model. None when nobody answered.
    With `want_box`, (index, box) - box None when it was not given."""
    from .llm_highlights import (ANTHROPIC, GEMINI, OPENAI, all_available,
                                 api_key, available, resolve_model)
    from .vision_frames import as_inline_data

    if not frames:
        return (None, None) if want_box else None
    prompt = prompt_template % {"count": len(frames)}

    if ask is not None:
        raw = ask(prompt, frames)
    else:
        # Gemini only: it is the one provider wired for images here, and
        # a text-only model cannot answer a question about pictures. No
        # answer is not a failure - the fallback frame is a fine picture.
        key = api_key(GEMINI)
        if not key:
            return (None, None) if want_box else None
        from .llm_highlights import _ask_gemini_vision

        parts = [{"text": prompt}] + [as_inline_data(f) for f in frames]
        raw, _why = _ask_gemini_vision(key, resolve_model(GEMINI, key, ""),
                                       parts)

    number = _read_number(raw, len(frames))
    index = None if number is None else number - 1
    if want_box:
        return index, (_read_box(raw) if index is not None else None)
    return index


def _read_number(raw: str, count: int) -> Optional[int]:
    import re

    if not raw:
        return None
    text = str(raw).strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
        value = data.get("frame") if isinstance(data, dict) else data
    except ValueError:
        # A model that answered "3" has still answered.
        found = re.search(r"\d+", text)
        value = found.group(0) if found else None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 1 <= number <= count else None


def _size(picture: str) -> Optional[tuple]:
    """(width, height) of an image, from ffprobe."""
    try:
        found = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0",
             picture], capture_output=True, text=True, timeout=60).stdout
        width, height = (int(v) for v in found.strip().split(",")[:2])
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return width, height


def _stamp_logo(picture: str, logo: str) -> bool:
    """Put `logo` in the top-left corner of `picture`, in place.

    Sized in pixels from the picture's measured width. scale2ref's
    main_w means the reference on some ffmpeg builds and the input on
    others - on 7.x a logo came out at a fifth of its OWN size, a speck.
    """
    size = _size(picture)
    if not size:
        return False
    width = max(2, int(size[0] * LOGO_WIDTH) // 2 * 2)
    margin = int(size[0] * LOGO_MARGIN)
    stamped = picture + ".logo.jpg"
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", picture, "-i", logo, "-filter_complex",
            f"[1:v]scale={width}:-2[mark];"
            f"[0:v][mark]overlay={margin}:{margin}",
            "-frames:v", "1", "-q:v", "2", stamped]
    try:
        subprocess.run(args, timeout=120, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if not (os.path.isfile(stamped) and os.path.getsize(stamped) > 0):
        return False
    os.replace(stamped, picture)
    return True


def _stamp_sticker(picture: str, sticker: str) -> bool:
    """Put `sticker` (a cut-out PNG) against the right edge, standing on
    the bottom, in place."""
    size = _size(picture)
    if not size:
        return False
    height = max(2, int(size[1] * STICKER_HEIGHT) // 2 * 2)
    stamped = picture + ".sticker.jpg"
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", picture, "-i", sticker, "-filter_complex",
            f"[1:v]scale=-2:{height}[mark];"
            f"[0:v][mark]overlay=main_w-overlay_w:main_h-overlay_h",
            "-frames:v", "1", "-q:v", "2", stamped]
    try:
        subprocess.run(args, timeout=120, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if not (os.path.isfile(stamped) and os.path.getsize(stamped) > 0):
        return False
    os.replace(stamped, picture)
    return True


def stream_filters(window: tuple, brightness: Optional[float]) -> str:
    """The ffmpeg filter chain for a stream thumbnail: zoom, lift a dark
    frame, grade, 1280x720."""
    x, y, w, h = window
    steps = [f"crop=iw*{w:.4f}:ih*{h:.4f}:iw*{x:.4f}:ih*{y:.4f}",
             f"scale={THUMB_W}:{THUMB_H}:flags=lanczos"]
    if brightness is not None and brightness < LIFT_BELOW_YAVG:
        # gamma > 1 lifts the mids without blowing out the highlights.
        lift = min(1.6, 1.0 + (LIFT_BELOW_YAVG - brightness) / 100.0)
        steps.append(f"eq=gamma={lift:.2f}")
    steps.append(STREAM_GRADE)
    return ",".join(steps)


def make(clip_path: str, duration: float, out_path: str = "",
         ask=None, logo_path: str = "", grade: bool = True,
         style: str = "clip", sticker_path: str = "") -> str:
    """Write a thumbnail for this clip. "" when one cannot be made.

    Never raises and never blocks a post: a clip with no thumbnail is a
    clip the platform picks a frame for, which is exactly where this
    started.
    """
    if not clip_path or not os.path.isfile(clip_path) or duration <= 0:
        return ""
    if not shutil.which("ffmpeg"):
        return ""
    out_path = out_path or os.path.splitext(clip_path)[0] + "_thumb.jpg"
    if style == "stream":
        return _make_stream(clip_path, duration, out_path, ask, logo_path,
                            sticker_path)

    marks = timestamps(duration)
    workspace = tempfile.mkdtemp(prefix="thumb_")
    try:
        frames, kept_marks = [], []
        for number, at in enumerate(marks):
            small = os.path.join(workspace, f"look_{number}.jpg")
            # Small copies for the LOOKING - a model reads an image at a
            # fixed token cost whatever its size, so full resolution here
            # is pure upload time.
            if not _grab(clip_path, at, small, width=512):
                continue
            try:
                with open(small, "rb") as handle:
                    frames.append(handle.read())
                kept_marks.append(at)
            except OSError:
                continue

        picked = None
        try:
            picked = _choose(frames, ask=ask)
        except Exception:
            picked = None

        at = (kept_marks[picked] if picked is not None
              and 0 <= picked < len(kept_marks)
              else duration * FALLBACK_FRACTION)
        # The real one, full size, from the timestamp that was chosen.
        if not _grab(clip_path, at, out_path, vf=GRADE if grade else ""):
            return ""
        if logo_path and os.path.isfile(logo_path):
            # A logo that will not go on costs the logo, not the picture.
            _stamp_logo(out_path, logo_path)
        return out_path
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _make_stream(source: str, duration: float, out_path: str, ask,
                 logo_path: str, sticker_path: str) -> str:
    marks = timestamps(duration, STREAM_SAMPLES)
    workspace = tempfile.mkdtemp(prefix="thumb_")
    try:
        looks = []                       # (at, bytes, stats)
        for number, at in enumerate(marks):
            small = os.path.join(workspace, f"look_{number}.jpg")
            if not _grab(source, at, small, width=512):
                continue
            try:
                with open(small, "rb") as handle:
                    data = handle.read()
            except OSError:
                continue
            looks.append((at, data, _measure(small)))
        if not looks:
            return ""

        # Dark frames never reach the model - unless every frame is dark.
        lit = [look for look in looks
               if not look[2] or look[2][0] >= DARK_YAVG]
        candidates = lit or looks

        picked, box = None, None
        try:
            picked, box = _choose([data for _, data, _ in candidates],
                                  ask=ask, prompt_template=STREAM_PROMPT,
                                  want_box=True)
        except Exception:
            picked, box = None, None
        if picked is None or not 0 <= picked < len(candidates):
            # No model: the brightest, most colourful look, not a fixed
            # timestamp that may well be a dark room.
            picked = max(range(len(candidates)),
                         key=lambda i: _appeal(candidates[i][2]))
            box = None
        at, _data, stats = candidates[picked]

        vf = stream_filters(zoom_window(box), stats[0] if stats else None)
        if not _grab(source, at, out_path, vf=vf):
            return ""
        if sticker_path and os.path.isfile(sticker_path):
            _stamp_sticker(out_path, sticker_path)
        if logo_path and os.path.isfile(logo_path):
            _stamp_logo(out_path, logo_path)
        return out_path
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
