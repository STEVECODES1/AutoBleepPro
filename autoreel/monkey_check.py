"""Is that Monkey App on screen, for real this time?

WHY
---
The pixel classifier (autoreel.content_kind) cannot tell a dark, still
game scene from a video call: stillness + mid-brightness + spread is
exactly what both look like at 160x90 grey. A parked car, a death
screen, a quiet menu - all read "monkey", and a run built on that
reading cuts GTA into a Monkey draft. The pixels say "something still
is here"; only looking at the picture says what it is.

HOW
---
Each candidate run is shown to the local vision model
(autoreel.local_llm, Qwen3-VL on this PC's own card) - three frames
spread across the run, one question each: is a random video-chat app
on screen? - and listened to (one loudness series for the whole
source, autoreel.audio_energy): a run nobody talks in is not a call
worth keeping, whatever is on screen. A run stays when most of its
frames read as a video call AND it is not near-silent. Nothing leaves
the PC, and when the model cannot answer the run stays: an unavailable
model must not stop every draft (same convention as gambling_check).
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
from typing import List, Optional, Sequence, Tuple

SYSTEM = (
    "You check one frame of a livestream recording for a RANDOM VIDEO "
    "CHAT APP (Monkey App, Omegle, OmeTV, Azar, Chatroulette or similar). "
    "Reply only JSON: {\"monkey\": true or false, \"what\": \"a few words\"}. "
    "monkey is true when the frame shows a video call with a stranger: "
    "a call pane with another person's face, side-by-side caller windows, "
    "next/skip buttons, a chat box beside a live stranger, 'unblur' or "
    "matching UI. It is false for a video game (GTA, FiveM, RP servers, "
    "shooters - HUD, minimap, health bars, dialogue over a 3D world), a "
    "single webcam face with no call UI, a desktop, a browser, a casino "
    "or slots page, a loading screen, or a menu."
)

WIDTH = 768
# Frames per candidate run, spread across it. Three is the whole range
# of answers that matter: 3/3 and 2/3 keep, 1/3 and 0/3 drop.
FRAMES_PER_RUN = 3

# A run nobody talks in is not a call worth keeping, whatever is on
# screen. Windows louder than this count as audible; a run stays when
# at least this share of its windows are audible. Deliberately
# lenient - transcription and scoring downstream do the fine
# selection; this gate only removes near-total silence.
SILENCE_DB = -60.0
AUDIBLE_SHARE = 0.2


def frame_jpeg(source: str, at: float, width: int = WIDTH) -> str:
    """One frame as base64 JPEG, or "" when it cannot be read."""
    from autoreel.gambling_check import frame_jpeg as _grab

    try:
        return _grab(source, at, width)
    except Exception:
        return ""


def _parse(reply: str) -> Optional[Tuple[bool, str]]:
    m = re.search(r"\{.*\}", reply or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    value = data.get("monkey")
    if isinstance(value, str):
        word = value.strip().lower()
        value = True if word in ("true", "yes") else False if word in ("false", "no") else None
    if not isinstance(value, bool):
        return None
    return value, str(data.get("what") or "")[:80]


def frame_has_monkey(jpeg_b64: str, ask=None) -> Optional[Tuple[bool, str]]:
    """(monkey?, what) for one frame, or None when it could not be told."""
    if not jpeg_b64:
        return None
    if ask is None:
        from autoreel import local_llm

        if not local_llm.ready():
            return None

        def ask(system, prompt, images):
            text, _why = local_llm.chat(system, prompt, images=images, json_reply=True)
            return text
    try:
        reply = ask(SYSTEM, "Is a random video chat app on screen in this frame?", [jpeg_b64])
    except Exception:
        return None
    return _parse(reply)


def run_times(start: float, end: float, count: int = FRAMES_PER_RUN) -> List[float]:
    """`count` evenly spread points inside (start, end)."""
    span = max(0.0, end - start)
    return [start + span * (i + 1) / (count + 1) for i in range(max(1, count))]


def confirm_runs(source: str, runs: Sequence[Tuple[float, float]], ask=None,
                 frames_per_run: int = FRAMES_PER_RUN,
                 levels: Optional[Sequence[float]] = None,
                 window_s: float = 1.0) -> Tuple[list, list]:
    """(kept, dropped) after looking at each run AND listening to it.

    A run stays when most of its frames read as Monkey App AND it is
    not near-silent. Either check on its own is a different thing
    wearing its name: pixels alone keep parked cars, audio alone keeps
    loud gameplay. A run whose frames could not be judged at all stays
    too - no measurement, no cut. Dropped entries are
    (start, end, why) so the log says why, not just that.
    """
    kept: list = []
    dropped: list = []
    for start, end in runs or ():
        heard: List[Tuple[bool, str]] = []
        for at in run_times(start, end, frames_per_run):
            verdict = frame_has_monkey(frame_jpeg(source, at), ask=ask)
            if verdict is None:
                continue
            heard.append((bool(verdict[0]), verdict[1]))
        if heard:
            # Most frames must agree it is Monkey App. One matching frame
            # in a long run is a menu, an overlay, or a mistake - the same
            # reason the pixel classifier's single samples never decide.
            yes = sum(1 for is_monkey, _ in heard if is_monkey)
            if yes * 2 < len(heard):
                seen = "; ".join(sorted({what for is_monkey, what in heard
                                         if not is_monkey}) or ["not a video call"])
                dropped.append((start, end, seen))
                continue
        if levels is not None and not _audible(levels, start, end, window_s):
            dropped.append((start, end, "near-silent - nobody talking"))
            continue
        kept.append((start, end))
    return kept, dropped


def _audible(levels: Sequence[float], start: float, end: float,
             window_s: float = 1.0) -> bool:
    """Whether anyone is making sound in this run.

    `levels` are dB per window across the whole source
    (autoreel.audio_energy.measure); `window_s` is the width the
    series itself implies (source seconds per reading), because the
    only thing guaranteed is one reading per window in order.
    """
    try:
        windows = list(levels or [])
        if not windows or window_s <= 0:
            return True
        first = max(0, int(start // window_s))
        last = min(len(windows), int(end // window_s) + 1)
        inside = windows[first:last]
        if not inside:
            return True
        loud = sum(1 for db in inside if db > SILENCE_DB)
        return loud / len(inside) >= AUDIBLE_SHARE
    except Exception:
        return True
