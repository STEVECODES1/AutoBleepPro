"""Is an online casino on screen? Asked of the local vision model, per frame.

WHY
---
Since March 2025 YouTube removes videos that point viewers to gambling
sites Google has not approved - and showing the site, its logo or a promo
code counts, not only a link. Many Stackswopo streams run a casino (Howl.gg,
slots, "use code STACKS" banners) next to the Monkey App window, and one of
those on BinScripts or the Shorts channel is a strike.

HOW
---
A few frames go to the local Ollama vision model (autoreel.local_llm), one
question each: is a casino / betting site or game on screen? Nothing leaves
the PC. When the model is not available the answer is "not sure" - callers
decide what not sure means (posting code treats it as "go ahead", since a
missing model must not stop every clip).
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
from typing import List, Optional, Sequence, Tuple

SYSTEM = (
    "You check one frame of a livestream recording for ONLINE GAMBLING. "
    "Reply only JSON: {\"gambling\": true or false, \"what\": \"a few words\"}. "
    "gambling is true when ANY part of the frame shows a casino or betting site "
    "or game: slot machine reels, 'free spins', 'bonus buy', 'big win' or "
    "multiplier screens, roulette, blackjack, crash/plinko/mines/dice games, "
    "case or crate openings with money, sportsbook odds, a casino brand or logo "
    "(Howl, Stake, Roobet, Rollbit, Shuffle, Gamdom ...), or a banner with a "
    "gambling promo code. It is false for an ordinary video game (GTA, sports or "
    "boxing games, shooters), a video call or chat app, a stream overlay, a "
    "website that is not gambling, or a desktop.")

WIDTH = 768


def frame_jpeg(source: str, at: float, width: int = WIDTH) -> str:
    """One frame as base64 JPEG, or "" when it cannot be read."""
    try:
        done = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, at):.2f}",
             "-i", source, "-frames:v", "1", "-vf", f"scale={width}:-2",
             "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "4", "-"],
            capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if done.returncode != 0 or not done.stdout:
        return ""
    return base64.b64encode(done.stdout).decode("ascii")


def _parse(reply: str) -> Optional[Tuple[bool, str]]:
    m = re.search(r"\{.*\}", reply or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    value = data.get("gambling")
    if isinstance(value, str):
        word = value.strip().lower()
        value = True if word in ("true", "yes") else False if word in ("false", "no") else None
    if not isinstance(value, bool):
        return None
    return value, str(data.get("what") or "")[:80]


def frame_has_gambling(jpeg_b64: str, ask=None) -> Optional[Tuple[bool, str]]:
    """(gambling?, what) for one frame, or None when it could not be told."""
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
        reply = ask(SYSTEM, "Is online gambling anywhere on screen in this frame?", [jpeg_b64])
    except Exception:
        return None
    return _parse(reply)


def sample_times(start: float, end: float, count: int = 3) -> List[float]:
    """`count` evenly spread points inside (start, end)."""
    span = max(0.0, end - start)
    return [start + span * (i + 1) / (count + 1) for i in range(max(1, count))]


def gambling_on_screen(source: str, times: Sequence[float], ask=None,
                       need: int = 1) -> Tuple[Optional[bool], List[Tuple[float, str]]]:
    """(True / False / None for not sure, [(time, what), ...] of the hits).

    True once `need` frames show gambling. None when no frame could be
    judged at all (no model, unreadable file)."""
    hits: List[Tuple[float, str]] = []
    judged = 0
    for at in times:
        verdict = frame_has_gambling(frame_jpeg(source, at), ask=ask)
        if verdict is None:
            continue
        judged += 1
        if verdict[0]:
            hits.append((float(at), verdict[1]))
            if len(hits) >= need:
                return True, hits
    if not judged:
        return None, hits
    return False, hits
