"""Who is on screen: in GTA RP Stackswopo plays JOHNNY COX (owner 11-04:
"why do you say stackswopo when the character is johnny cox ... it's good
to have stackswopo somewhere sometimes to keep traffic").

is_gta_clip(): looks at the clip itself (content_kind on a few frames),
then its stream's name, then what is said. name_in_title(): a GTA clip is
about Johnny Cox, with "Stackswopo" kept once for search.
"""

import json
import os
import re

GTA_WORDS = re.compile(r"\b(gta|rp|roleplay|fivem|nopixel|johnny cox|cox)\b", re.I)
NAME = re.compile(r"\bstackswopo\b", re.I)


def _stream_title(video_path: str) -> str:
    stem = os.path.splitext(video_path or "")[0]
    for path in (stem + "_source.json",):
        try:
            with open(path, encoding="utf-8") as f:
                return str(json.load(f).get("stream_title", ""))
        except (OSError, ValueError):
            pass
    return ""


def is_gta_clip(video_path: str, said: str = "", look=None) -> bool:
    """True when the clip is GTA RP. Never raises."""
    try:
        if look is None:
            from autoreel.content_kind import kind_for_video as look
        length = 0.0
        try:
            import subprocess

            length = float(subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", video_path], capture_output=True, text=True,
                timeout=60).stdout.strip() or 0)
        except Exception:
            pass
        points = [length * f for f in (0.25, 0.6)] if length else [1.0]
        kinds = [look(video_path, start=p) for p in points]
        if any(k == "gta" for k in kinds):
            return True
        if any(k for k in kinds):          # the frames say something else
            return False
    except Exception:
        pass
    text = f"{_stream_title(video_path)} {os.path.basename(video_path or '')} {said or ''}"
    return bool(GTA_WORDS.search(text))


def name_in_title(title: str, gta: bool, limit: int = 100) -> str:
    """Stackswopo -> Johnny Cox for a GTA clip, "| Stackswopo GTA RP" kept
    at the end when it fits (search traffic). Non-GTA titles unchanged."""
    text = " ".join(str(title or "").split())
    if not gta or not text:
        return text
    text = re.sub(r"\s*\|\s*stackswopo\s*$", "", text, flags=re.I)
    if "johnny cox" not in text.lower():
        if NAME.search(text):
            text = NAME.sub("Johnny Cox", text, count=1)
        else:
            text = f"Johnny Cox: {text}"
    text = NAME.sub("", text).replace(" | ", " ").strip(" |")
    text = " ".join(text.split())
    tail = " | Stackswopo GTA RP"
    if len(text) + len(tail) <= limit:
        text += tail
    return text[:limit]


CAPTION_NOTE = (
    "\nWHO IS ON SCREEN: this clip is GTA RP (GTA V roleplay). The streamer, "
    "Stackswopo, is playing his character JOHNNY COX - call him Johnny Cox in "
    "the caption. You may mention Stackswopo once, at most, for people who "
    "know him by that name.\n")
