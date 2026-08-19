"""The frame that makes someone stop scrolling.

Until now the thumbnail was whatever the platform grabbed - in practice
the first frame, which on a clip cut out of a stream is the tail end of
whatever came before it. A grey loading screen, a menu, the back of
somebody's head.

The clip already gets looked at by a model to decide it was worth
cutting. Asking the same model which of eight frames is the one worth
showing is a small extra question with a large effect on whether anyone
presses play.

The picture is used AS IT IS. No burned-in text, no zoom, no border. A
thumbnail that looks made rather than captured reads as an ad, and the
clips already carry their title across the top of the frame - saying it
twice is worse than saying it once.
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

# Where to grab from when no model answers. Just past a third: far enough
# in that the previous shot is gone, early enough that most clips have
# started doing whatever they were cut for.
FALLBACK_FRACTION = 0.35

PROMPT = """\
These are %(count)d frames from one short video clip, in order.

Pick the ONE that would make somebody scrolling past stop and watch.
That is usually a face mid-reaction, a moment of impact, or something
plainly odd on screen. Avoid loading screens, menus, plain scenery, motion
blur, and frames where nothing is happening.

Answer as JSON: {"frame": <number from 1 to %(count)d>}
No other text.
"""


def _grab(source: str, at: float, out_path: str, width: int = 0) -> bool:
    """One JPEG at one timestamp. False if it could not be read."""
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{max(0.0, at):.2f}", "-i", source, "-frames:v", "1"]
    if width:
        args += ["-vf", f"scale={width}:-2"]
    args += ["-q:v", "2", out_path]
    try:
        subprocess.run(args, timeout=120, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return os.path.isfile(out_path) and os.path.getsize(out_path) > 0


def timestamps(duration: float) -> list:
    """Where to look, in seconds from the start of the clip."""
    if duration <= 0:
        return []
    return [duration * f for f in SAMPLE_FRACTIONS]


def _choose(frames: list, ask=None) -> Optional[int]:
    """Which frame (0-based), from a model. None when nobody answered."""
    from .llm_highlights import (ANTHROPIC, GEMINI, OPENAI, all_available,
                                 api_key, available, resolve_model)
    from .vision_frames import as_inline_data

    if not frames:
        return None
    prompt = PROMPT % {"count": len(frames)}

    if ask is not None:
        raw = ask(prompt, frames)
    else:
        # Gemini only: it is the one provider wired for images here, and
        # a text-only model cannot answer a question about pictures. No
        # answer is not a failure - the fallback frame is a fine picture.
        key = api_key(GEMINI)
        if not key:
            return None
        from .llm_highlights import _ask_gemini_vision

        parts = [{"text": prompt}] + [as_inline_data(f) for f in frames]
        raw, _why = _ask_gemini_vision(key, resolve_model(GEMINI, key, ""),
                                       parts)

    number = _read_number(raw, len(frames))
    return None if number is None else number - 1


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


def make(clip_path: str, duration: float, out_path: str = "",
         ask=None) -> str:
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
        if not _grab(clip_path, at, out_path):
            return ""
        return out_path
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
